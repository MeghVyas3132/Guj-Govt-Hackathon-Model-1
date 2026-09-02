"""Managing event subscriptions.

Admin-only: a webhook is an outbound data flow, so creating one is closer to
granting access than to changing a setting.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import request_context, require_scope
from app.models.webhook import Webhook, WebhookDelivery
from app.schemas.auth import Principal
from app.schemas.webhook import (
    WebhookCreate,
    WebhookDeliveryRead,
    WebhookRead,
    WebhookTestResult,
    WebhookUpdate,
)
from app.services.audit import AuditService
from app.services.webhooks import KNOWN_EVENTS, WebhookService

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _to_read(row: Webhook) -> WebhookRead:
    return WebhookRead(
        id=str(row.id),
        name=row.name,
        url=row.url,
        events=list(row.events or []),
        department_id=str(row.department_id) if row.department_id else None,
        secret_ref=row.secret_ref,
        is_active=row.is_active,
        consecutive_failures=row.consecutive_failures or 0,
        disabled_at=row.disabled_at.isoformat() if row.disabled_at else None,
        last_delivered_at=(
            row.last_delivered_at.isoformat() if row.last_delivered_at else None
        ),
    )


@router.get(
    "/events",
    response_model=list[str],
    summary="Event names a subscription can request",
    description=(
        "A subscription with an empty event list receives all of them, so a "
        "dashboard does not need reconfiguring when this list grows."
    ),
)
async def list_events(
    principal: Principal = Depends(require_scope("admin")),
) -> list[str]:
    return list(KNOWN_EVENTS)


@router.get("", response_model=list[WebhookRead], summary="List subscriptions")
async def list_webhooks(
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookRead]:
    rows = (
        (await session.execute(select(Webhook).order_by(Webhook.created_at.desc())))
        .scalars()
        .all()
    )
    return [_to_read(row) for row in rows]


@router.post("", response_model=WebhookRead, status_code=201, summary="Subscribe")
async def create_webhook(
    payload: WebhookCreate,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> WebhookRead:
    unknown = set(payload.events) - set(KNOWN_EVENTS)
    if unknown:
        # Rejected rather than accepted-and-ignored: a subscription that silently
        # matches nothing is indistinguishable from one that is merely quiet.
        raise HTTPException(
            status_code=422,
            detail=f"Unknown events: {sorted(unknown)}. Known: {sorted(KNOWN_EVENTS)}",
        )

    hook = Webhook(
        name=payload.name,
        url=str(payload.url),
        events=payload.events,
        department_id=UUID(payload.department_id) if payload.department_id else None,
        secret_ref=payload.secret_ref,
    )
    session.add(hook)
    await session.flush()

    AuditService(session).record(
        action="webhook.created", entity_type="webhook", entity_id=hook.id,
        actor=principal, after={"url": hook.url, "events": hook.events}, **context,
    )
    await session.commit()
    return _to_read(hook)


@router.patch("/{webhook_id}", response_model=WebhookRead, summary="Update a subscription")
async def update_webhook(
    webhook_id: UUID,
    payload: WebhookUpdate,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> WebhookRead:
    hook = await session.get(Webhook, webhook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    before = {"url": hook.url, "events": list(hook.events or []), "is_active": hook.is_active}

    if payload.events is not None:
        unknown = set(payload.events) - set(KNOWN_EVENTS)
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown events: {sorted(unknown)}")
        hook.events = payload.events
    if payload.name is not None:
        hook.name = payload.name
    if payload.url is not None:
        hook.url = str(payload.url)
    if payload.secret_ref is not None:
        hook.secret_ref = payload.secret_ref
    if payload.is_active is not None:
        hook.is_active = payload.is_active
        if payload.is_active:
            # Re-enabling clears the automatic cutoff, otherwise a hook disabled
            # for repeated failures can never be brought back.
            hook.disabled_at = None
            hook.consecutive_failures = 0

    AuditService(session).record(
        action="webhook.updated", entity_type="webhook", entity_id=hook.id,
        actor=principal, before=before,
        after={"url": hook.url, "events": hook.events, "is_active": hook.is_active},
        **context,
    )
    await session.commit()
    return _to_read(hook)


@router.delete("/{webhook_id}", status_code=204, summary="Unsubscribe")
async def delete_webhook(
    webhook_id: UUID,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> None:
    hook = await session.get(Webhook, webhook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    AuditService(session).record(
        action="webhook.deleted", entity_type="webhook", entity_id=hook.id,
        actor=principal, before={"url": hook.url}, **context,
    )
    await session.delete(hook)
    await session.commit()


@router.post(
    "/{webhook_id}/test",
    response_model=WebhookTestResult,
    summary="Send a test event",
    description=(
        "Delivers a `camera.status_changed` payload with synthetic data, so an "
        "integrator can verify their signature check before a real outage."
    ),
)
async def test_webhook(
    webhook_id: UUID,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> WebhookTestResult:
    hook = await session.get(Webhook, webhook_id)
    if hook is None:
        raise HTTPException(status_code=404, detail="Webhook not found")

    service = WebhookService(session)
    envelope = {"test": True, "camera_uid": "GJ-TEST-000001", "status": "offline"}
    body = __import__("json").dumps(
        {"event": "camera.status_changed", "data": envelope}, separators=(",", ":")
    ).encode()
    secret = (
        await service.credentials.resolve(hook.secret_ref) if hook.secret_ref else None
    )
    delivery = await service._post(
        hook, "camera.status_changed", {"data": envelope}, body, secret
    )
    session.add(delivery)
    await session.commit()

    return WebhookTestResult(
        succeeded=delivery.succeeded,
        status_code=delivery.status_code,
        duration_ms=delivery.duration_ms,
        error=delivery.error,
        signed=secret is not None,
    )


@router.get(
    "/{webhook_id}/deliveries",
    response_model=list[WebhookDeliveryRead],
    summary="Recent delivery attempts",
    description="The answer to \"we never received it\", with status codes.",
)
async def list_deliveries(
    webhook_id: UUID,
    limit: int = Query(50, ge=1, le=500),
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[WebhookDeliveryRead]:
    rows = (
        (
            await session.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.webhook_id == webhook_id)
                .order_by(WebhookDelivery.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        WebhookDeliveryRead(
            id=str(row.id),
            event=row.event,
            status_code=row.status_code,
            succeeded=row.succeeded,
            duration_ms=row.duration_ms,
            error=row.error,
            created_at=row.created_at.isoformat() if row.created_at else None,
        )
        for row in rows
    ]
