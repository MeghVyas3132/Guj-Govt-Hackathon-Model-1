"""Delivering events to subscribers.

Three properties matter more than throughput here:

Signing. A receiver has to be able to tell a genuine alert from anything else
that can reach its URL, so every payload is HMAC-signed with a shared secret and
carries a timestamp the receiver can use to reject replays.

Isolation. One subscriber's dead endpoint must not delay or block anyone else's
alert, and must not be able to fail the operation that raised the event. A
camera going offline is recorded whether or not anybody could be told about it.

Evidence. Every attempt is stored with its status code. "We never received it"
is a question with an answer.
"""

import asyncio
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import Webhook, WebhookDelivery
from app.services.credentials import CredentialResolver

# Event names. Flat strings rather than an enum on the wire: a subscriber built
# against today's list must keep working when tomorrow's is longer.
CAMERA_STATUS_CHANGED = "camera.status_changed"
CAMERA_OFFLINE = "camera.offline"
CAMERA_ONLINE = "camera.online"
CAMERA_ONBOARDED = "camera.onboarded"
COVERAGE_COMPLETED = "coverage.completed"
AMC_EXPIRING = "camera.amc_expiring"

KNOWN_EVENTS = (
    CAMERA_STATUS_CHANGED,
    CAMERA_OFFLINE,
    CAMERA_ONLINE,
    CAMERA_ONBOARDED,
    COVERAGE_COMPLETED,
    AMC_EXPIRING,
)

# A subscriber this many consecutive failures deep is switched off. Without it,
# one endpoint that has been decommissioned for a month adds its full timeout to
# every batch of events, forever.
FAILURE_LIMIT = 20

DELIVERY_TIMEOUT_S = 10.0
CONCURRENCY = 10


def sign(secret: str, timestamp: str, body: bytes) -> str:
    """The signature a receiver recomputes to authenticate a delivery.

    The timestamp is inside the signed material, so a captured payload cannot be
    replayed later with a fresh header.
    """
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


class WebhookService:
    def __init__(
        self,
        session: AsyncSession,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.session = session
        self.transport = transport
        self.credentials = CredentialResolver(session)

    async def subscribers(
        self, event: str, department_id: UUID | None = None
    ) -> list[Webhook]:
        """Active hooks that want this event.

        An empty `events` array means every event: a dashboard should not need
        reconfiguring each time a new event type is added.
        """
        rows = (
            (
                await self.session.execute(
                    select(Webhook).where(Webhook.is_active, Webhook.disabled_at.is_(None))
                )
            )
            .scalars()
            .all()
        )
        return [
            hook
            for hook in rows
            if (not hook.events or event in hook.events)
            # A hook scoped to a department hears only that department. A hook
            # with no department hears everything, which is the state-level view.
            and (hook.department_id is None or hook.department_id == department_id)
        ]

    async def emit(
        self,
        event: str,
        data: dict[str, Any],
        department_id: UUID | None = None,
    ) -> list[WebhookDelivery]:
        """Deliver one event to every interested subscriber.

        Never raises. The caller is recording something that happened; a
        subscriber being unreachable does not make it un-happen, and letting a
        delivery failure roll back a health observation would lose real data to
        an unrelated outage.
        """
        try:
            hooks = await self.subscribers(event, department_id)
        except Exception:  # noqa: BLE001 - see docstring
            return []
        if not hooks:
            return []

        envelope = {
            "event": event,
            "delivered_at": datetime.now(UTC).isoformat(),
            "data": data,
        }
        body = json.dumps(envelope, default=str, separators=(",", ":")).encode()

        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def deliver(hook: Webhook) -> WebhookDelivery:
            secret = (
                await self.credentials.resolve(hook.secret_ref)
                if hook.secret_ref
                else None
            )
            async with semaphore:
                return await self._post(hook, event, envelope, body, secret)

        # Fan out on the network, fan in on the database: AsyncSession is not
        # concurrency-safe, so every write below happens after the gather.
        results = await asyncio.gather(
            *(deliver(hook) for hook in hooks), return_exceptions=True
        )

        deliveries: list[WebhookDelivery] = []
        for hook, result in zip(hooks, results, strict=True):
            if isinstance(result, BaseException):
                result = WebhookDelivery(
                    webhook_id=hook.id, event=event, payload=envelope,
                    error=f"{type(result).__name__}: {result}"[:2000], succeeded=False,
                )
            self.session.add(result)
            deliveries.append(result)

            if result.succeeded:
                hook.consecutive_failures = 0
                hook.last_delivered_at = datetime.now(UTC)
            else:
                hook.consecutive_failures = (hook.consecutive_failures or 0) + 1
                if hook.consecutive_failures >= FAILURE_LIMIT:
                    hook.disabled_at = datetime.now(UTC)

        # Flushed, not committed: the delivery record and the failure counters
        # belong to the caller's transaction. Without this the counters live only
        # in the identity map and are lost the moment anything reloads the row --
        # so a dead endpoint would never accumulate failures and never be
        # disabled.
        await self.session.flush()
        return deliveries

    async def _post(
        self,
        hook: Webhook,
        event: str,
        envelope: dict[str, Any],
        body: bytes,
        secret: str | None,
    ) -> WebhookDelivery:
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "sentinel-registry/1.0",
            "X-Sentinel-Event": event,
            "X-Sentinel-Timestamp": timestamp,
            "X-Sentinel-Delivery": str(hook.id),
        }
        if secret:
            headers["X-Sentinel-Signature"] = sign(secret, timestamp, body)

        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=DELIVERY_TIMEOUT_S,
                # A subscriber that answers with a redirect is misconfigured.
                # Following it could post the payload somewhere unintended.
                follow_redirects=False,
            ) as client:
                response = await client.post(hook.url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            return WebhookDelivery(
                webhook_id=hook.id, event=event, payload=envelope,
                error=f"{type(exc).__name__}: {exc}"[:2000],
                duration_ms=int((time.perf_counter() - started) * 1000),
                succeeded=False,
            )

        return WebhookDelivery(
            webhook_id=hook.id,
            event=event,
            payload=envelope,
            status_code=response.status_code,
            duration_ms=int((time.perf_counter() - started) * 1000),
            # Any 2xx is acceptance. Insisting on 200 breaks the very common
            # receiver that queues the event and answers 202.
            succeeded=200 <= response.status_code < 300,
            error=None if response.is_success else response.text[:2000] or None,
        )


__all__ = ["KNOWN_EVENTS", "WebhookService", "sign"]
