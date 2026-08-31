# Model 1 Webhooks, Seed Data & Delivery — Implementation Plan (Plan 6 of 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push events to Models 2–4 instead of making them poll, populate the registry at full 80,000-camera scale, write the documentation deliverables, and rehearse the demo until it is boring.

**Architecture:** Events are written to a `webhook_deliveries` outbox **inside the same transaction** as the change that produced them, so a delivery can never reference a change that was rolled back. A worker drains the outbox with exponential backoff and HMAC-SHA256 signatures.

**Tech Stack:** Transactional outbox, arq worker, HMAC-SHA256, numpy-free weighted sampling.

**Prerequisites:** Plans 1–5 complete.

---

## File structure additions

```
app/
  models/webhook.py
  schemas/webhook.py
  services/webhook_dispatch.py
  api/v1/routers/webhooks.py
seeds/
  synthetic.py                       80k camera generator
  fixtures/                          five department CSVs, five different schemas
docs/
  README.md
  api/onboarding-guide.md
  demo-script.md
```

---

## Task 1: Outbox tables

**Files:**
- Create: `app/models/webhook.py`, `app/schemas/webhook.py`
- Test: covered by Task 2

- [ ] **Step 1: Create `app/models/webhook.py`**

```python
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY, Boolean, DateTime, ForeignKey, Index, Integer, String, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class WebhookSubscription(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "webhook_subscriptions"

    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))
    secret: Mapped[str] = mapped_column(String(128))
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookDelivery(Base, UUIDMixin):
    """Transactional outbox row. Written in the same transaction as the change it
    describes, so a delivery can never describe a rolled-back write."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_pending", "status", "next_retry_at"),)

    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("webhook_subscriptions.id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 2: Create `app/schemas/webhook.py`**

```python
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class EventType(StrEnum):
    CAMERA_CREATED = "camera.created"
    CAMERA_UPDATED = "camera.updated"
    CAMERA_DECOMMISSIONED = "camera.decommissioned"
    CAMERA_STATUS_CHANGED = "camera.status_changed"
    IMPORT_COMPLETED = "import.completed"
    COVERAGE_COMPLETED = "coverage.completed"


class SubscriptionCreate(BaseModel):
    name: str
    url: HttpUrl
    event_types: list[EventType] = Field(
        default_factory=lambda: [EventType.CAMERA_STATUS_CHANGED]
    )
    department_id: UUID | None = None


class SubscriptionRead(BaseModel):
    id: UUID
    name: str
    url: str
    event_types: list[str]
    is_active: bool
    secret: str = Field(description="Shown once on creation, redacted afterwards.")
```

- [ ] **Step 3: Generate and run the migration**

Run:
```bash
alembic revision --autogenerate -m "webhook subscriptions and outbox"
alembic upgrade head
```

- [ ] **Step 4: Commit**

```bash
git add app/models/webhook.py app/schemas/webhook.py alembic
git commit -m "feat: webhook subscription and transactional outbox tables"
```

---

## Task 2: Event emission and signed delivery

**Files:**
- Create: `app/services/webhook_dispatch.py`
- Modify: `app/services/ingestion.py`, `app/services/health.py`, `app/workers/tasks.py`
- Test: `tests/services/test_webhooks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_webhooks.py`:

```python
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.schemas.webhook import EventType
from app.services.webhook_dispatch import WebhookDispatcher, deliver_pending, sign_payload


@pytest.fixture
async def subscription(session):
    sub = WebhookSubscription(
        name="Model 2 alerting",
        url="https://model2.internal/hooks/registry",
        secret="shhh-secret",
        event_types=[EventType.CAMERA_STATUS_CHANGED.value],
    )
    session.add(sub)
    await session.commit()
    return sub


@pytest.mark.asyncio
async def test_matching_event_creates_one_outbox_row(session, subscription):
    await WebhookDispatcher(session).emit(
        EventType.CAMERA_STATUS_CHANGED, {"camera_uid": "GJ-POL-000001", "status": "offline"}
    )
    await session.commit()

    rows = (await session.execute(select(WebhookDelivery))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "pending"
    assert rows[0].payload["camera_uid"] == "GJ-POL-000001"


@pytest.mark.asyncio
async def test_unsubscribed_event_creates_nothing(session, subscription):
    await WebhookDispatcher(session).emit(
        EventType.COVERAGE_COMPLETED, {"run_id": "abc"}
    )
    await session.commit()
    assert (await session.execute(select(WebhookDelivery))).first() is None


@pytest.mark.asyncio
async def test_inactive_subscription_is_skipped(session, subscription):
    subscription.is_active = False
    await session.commit()

    await WebhookDispatcher(session).emit(
        EventType.CAMERA_STATUS_CHANGED, {"camera_uid": "X"}
    )
    await session.commit()
    assert (await session.execute(select(WebhookDelivery))).first() is None


def test_signature_is_hmac_sha256_over_the_exact_body():
    body = json.dumps({"a": 1}, separators=(",", ":")).encode()
    expected = hmac.new(b"shhh-secret", body, hashlib.sha256).hexdigest()
    assert sign_payload("shhh-secret", body) == f"sha256={expected}"


@pytest.mark.asyncio
async def test_successful_delivery_marks_the_row_delivered(session, subscription):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["signature"] = request.headers.get("X-Sentinel-Signature")
        seen["event"] = request.headers.get("X-Sentinel-Event")
        seen["body"] = request.content
        return httpx.Response(200)

    await WebhookDispatcher(session).emit(
        EventType.CAMERA_STATUS_CHANGED, {"camera_uid": "GJ-POL-000001"}
    )
    await session.commit()

    await deliver_pending(session, transport=httpx.MockTransport(handler))

    row = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert row.status == "delivered"
    assert row.delivered_at is not None
    assert seen["event"] == "camera.status_changed"
    assert seen["signature"] == sign_payload("shhh-secret", seen["body"])


@pytest.mark.asyncio
async def test_failed_delivery_schedules_a_backed_off_retry(session, subscription):
    transport = httpx.MockTransport(lambda request: httpx.Response(503))

    await WebhookDispatcher(session).emit(
        EventType.CAMERA_STATUS_CHANGED, {"camera_uid": "X"}
    )
    await session.commit()

    before = datetime.now(UTC)
    await deliver_pending(session, transport=transport)

    row = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.next_retry_at > before
    assert "503" in row.last_error


@pytest.mark.asyncio
async def test_delivery_gives_up_after_the_attempt_ceiling(session, subscription):
    transport = httpx.MockTransport(lambda request: httpx.Response(500))

    await WebhookDispatcher(session).emit(EventType.CAMERA_STATUS_CHANGED, {"c": "X"})
    await session.commit()

    for _ in range(7):
        row = (await session.execute(select(WebhookDelivery))).scalar_one()
        row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
        await deliver_pending(session, transport=transport)

    row = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_a_row_not_yet_due_is_left_alone(session, subscription):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200)

    await WebhookDispatcher(session).emit(EventType.CAMERA_STATUS_CHANGED, {"c": "X"})
    await session.commit()

    row = (await session.execute(select(WebhookDelivery))).scalar_one()
    row.next_retry_at = datetime.now(UTC) + timedelta(hours=1)
    await session.commit()

    await deliver_pending(session, transport=httpx.MockTransport(handler))
    assert calls["n"] == 0
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/services/test_webhooks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.webhook_dispatch'`

- [ ] **Step 3: Create `app/services/webhook_dispatch.py`**

```python
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook import WebhookDelivery, WebhookSubscription
from app.schemas.webhook import EventType

MAX_ATTEMPTS = 6
BASE_BACKOFF_SECONDS = 30
BATCH_SIZE = 50


def sign_payload(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _serialise(payload: dict[str, Any]) -> bytes:
    # Compact separators and sorted keys so the signature is reproducible by the
    # consumer from the exact bytes they received.
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()


class WebhookDispatcher:
    """Writes outbox rows. Call inside the transaction that made the change."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def emit(self, event_type: EventType, payload: dict[str, Any]) -> int:
        subscriptions = (
            (
                await self.session.execute(
                    select(WebhookSubscription).where(
                        WebhookSubscription.is_active,
                        WebhookSubscription.event_types.any(event_type.value),
                    )
                )
            )
            .scalars()
            .all()
        )

        for subscription in subscriptions:
            self.session.add(
                WebhookDelivery(
                    subscription_id=subscription.id,
                    event_type=event_type.value,
                    payload={
                        "event": event_type.value,
                        "emitted_at": datetime.now(UTC).isoformat(),
                        "data": payload,
                    },
                    status="pending",
                    next_retry_at=datetime.now(UTC),
                )
            )
        return len(subscriptions)


async def deliver_pending(
    session: AsyncSession, transport: httpx.BaseTransport | None = None
) -> dict[str, int]:
    now = datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(WebhookDelivery, WebhookSubscription)
                .join(
                    WebhookSubscription,
                    WebhookSubscription.id == WebhookDelivery.subscription_id,
                )
                .where(
                    WebhookDelivery.status == "pending",
                    WebhookDelivery.next_retry_at <= now,
                )
                .order_by(WebhookDelivery.created_at)
                .limit(BATCH_SIZE)
            )
        )
        .all()
    )

    delivered = failed = 0

    async with httpx.AsyncClient(transport=transport, timeout=15.0) as client:
        for delivery, subscription in rows:
            body = _serialise(delivery.payload)
            headers = {
                "content-type": "application/json",
                "X-Sentinel-Event": delivery.event_type,
                "X-Sentinel-Delivery": str(delivery.id),
                "X-Sentinel-Signature": sign_payload(subscription.secret, body),
            }
            delivery.attempts += 1

            try:
                response = await client.post(subscription.url, content=body, headers=headers)
                if 200 <= response.status_code < 300:
                    delivery.status = "delivered"
                    delivery.delivered_at = datetime.now(UTC)
                    delivery.last_error = None
                    delivered += 1
                    continue
                error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"

            delivery.last_error = error[:1000]
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = "failed"
                failed += 1
            else:
                delay = BASE_BACKOFF_SECONDS * (2 ** (delivery.attempts - 1))
                delivery.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)

    await session.commit()
    return {"delivered": delivered, "failed": failed, "considered": len(rows)}
```

- [ ] **Step 4: Emit on status change**

In `app/services/health.py`, the `record` method already computes `changed`. Add emission
just before the return:

```python
        if changed:
            from app.schemas.webhook import EventType
            from app.services.webhook_dispatch import WebhookDispatcher

            await WebhookDispatcher(self.session).emit(
                EventType.CAMERA_STATUS_CHANGED,
                {
                    "camera_id": str(camera.id),
                    "camera_uid": camera.camera_uid,
                    "department_id": str(camera.department_id),
                    "previous_status": previous,
                    "status": status,
                    "since": observed_at.isoformat(),
                },
            )
```

The emit happens before `await self.session.flush()` completes the transaction, so the
outbox row and the status change commit together or not at all.

- [ ] **Step 5: Emit on camera create**

In `app/services/ingestion.py` `_persist`, in the create branch after the audit call:

```python
            from app.schemas.webhook import EventType
            from app.services.webhook_dispatch import WebhookDispatcher

            await WebhookDispatcher(self.session).emit(
                EventType.CAMERA_CREATED,
                {
                    "camera_id": str(camera.id),
                    "camera_uid": camera.camera_uid,
                    "department_id": str(department.id),
                    "external_camera_id": camera.external_camera_id,
                },
            )
```

- [ ] **Step 6: Add the delivery worker**

In `app/workers/tasks.py`:

```python
async def dispatch_webhooks(ctx: dict) -> dict[str, int]:
    from app.services.webhook_dispatch import deliver_pending

    async with SessionLocal() as session:
        return await deliver_pending(session)
```

Add `dispatch_webhooks` to `WorkerSettings.functions` and add a cron entry:

```python
    cron_jobs = [
        cron(probe_cameras, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(dispatch_webhooks, second={0, 30}),
    ]
```

- [ ] **Step 7: Add the subscription API**

Create `app/api/v1/routers/webhooks.py`:

```python
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_scope
from app.models.webhook import WebhookSubscription
from app.schemas.auth import Principal
from app.schemas.webhook import SubscriptionCreate, SubscriptionRead

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("", response_model=SubscriptionRead, status_code=201)
async def subscribe(
    payload: SubscriptionCreate,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> SubscriptionRead:
    secret = secrets.token_urlsafe(32)
    subscription = WebhookSubscription(
        name=payload.name,
        url=str(payload.url),
        secret=secret,
        event_types=[e.value for e in payload.event_types],
        department_id=payload.department_id,
    )
    session.add(subscription)
    await session.commit()
    return SubscriptionRead(
        id=subscription.id, name=subscription.name, url=subscription.url,
        event_types=subscription.event_types, is_active=True, secret=secret,
    )


@router.get("", response_model=list[SubscriptionRead])
async def list_subscriptions(
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[SubscriptionRead]:
    rows = (await session.execute(select(WebhookSubscription))).scalars().all()
    return [
        SubscriptionRead(
            id=r.id, name=r.name, url=r.url, event_types=r.event_types,
            is_active=r.is_active, secret="********",
        )
        for r in rows
    ]


@router.delete("/{subscription_id}", status_code=204)
async def unsubscribe(
    subscription_id: UUID,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> None:
    subscription = await session.get(WebhookSubscription, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    subscription.is_active = False
    await session.commit()
```

Register it in `app/api/v1/router.py`.

- [ ] **Step 8: Run the tests and make sure they pass**

Run: `pytest tests/services/test_webhooks.py -v && pytest -v`
Expected: 8 passed, then the full suite passes.

- [ ] **Step 9: Commit**

```bash
git add app/services/webhook_dispatch.py app/api/v1/routers/webhooks.py app/workers/tasks.py app/services/health.py app/services/ingestion.py tests/services/test_webhooks.py
git commit -m "feat: transactional outbox webhooks with HMAC signing and backoff"
```

---

## Task 3: The 80,000-camera synthetic dataset

**Files:**
- Create: `seeds/synthetic.py`
- Test: `tests/seeds/test_synthetic.py`

- [ ] **Step 1: Write the failing test**

Create `tests/seeds/test_synthetic.py`:

```python
from seeds.synthetic import DEPARTMENTS, generate_cameras


def test_generates_the_requested_count():
    cameras = generate_cameras(count=500, seed=42)
    assert len(cameras) == 500


def test_generation_is_deterministic_for_a_given_seed():
    first = generate_cameras(count=200, seed=7)
    second = generate_cameras(count=200, seed=7)
    assert [c["external_camera_id"] for c in first] == [
        c["external_camera_id"] for c in second
    ]


def test_every_camera_falls_inside_the_gujarat_bounding_box():
    for camera in generate_cameras(count=1000, seed=1):
        assert 20.0 <= camera["latitude"] <= 24.8
        assert 68.0 <= camera["longitude"] <= 74.6


def test_all_five_sandbox_departments_are_represented():
    codes = {c["department_code"] for c in generate_cameras(count=2000, seed=3)}
    assert codes == {d["code"] for d in DEPARTMENTS}


def test_a_realistic_share_of_cameras_is_offline():
    cameras = generate_cameras(count=5000, seed=11)
    offline = sum(1 for c in cameras if c["status"] == "offline")
    assert 0.05 < offline / len(cameras) < 0.20


def test_fixed_cameras_get_a_bearing_and_ptz_cameras_do_not():
    cameras = generate_cameras(count=2000, seed=5)
    fixed = [c for c in cameras if c["camera_type"] in ("fixed", "bullet", "anpr")]
    ptz = [c for c in cameras if c["camera_type"] in ("ptz", "dome")]
    assert any(c["azimuth_deg"] is not None for c in fixed)
    assert all(c["azimuth_deg"] is None for c in ptz)


def test_some_cameras_are_private_and_analog():
    cameras = generate_cameras(count=3000, seed=9)
    assert any(c["ownership_class"] == "private" for c in cameras)
    assert any(c["camera_technology"] == "analog" for c in cameras)


def test_the_designated_gap_district_is_deliberately_sparse():
    cameras = generate_cameras(count=20000, seed=13)
    from collections import Counter

    per_district = Counter(c["district"] for c in cameras)
    assert per_district["Dahod"] < per_district["Ahmedabad"] / 20
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/seeds/test_synthetic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'seeds.synthetic'`

- [ ] **Step 3: Create `seeds/synthetic.py`**

```python
"""Synthetic camera generator for scale demonstration.

Cameras are distributed across real Gujarat district centroids, weighted roughly by
population so cities cluster and rural districts stay sparse. Two districts are
deliberately under-served so the gap analysis has something real to find.
"""

import asyncio
import math
import random
from typing import Any

# Five departments matching the Sentinel sandbox (FAQ #39).
DEPARTMENTS: list[dict[str, Any]] = [
    {"code": "POL", "name": "Gujarat Police", "weight": 0.34,
     "site_types": ["traffic_junction", "border_checkpost", "public_space"]},
    {"code": "MUN", "name": "Municipal Corporation", "weight": 0.30,
     "site_types": ["traffic_junction", "public_space", "office"]},
    {"code": "GSRTC", "name": "Gujarat State Road Transport Corporation", "weight": 0.16,
     "site_types": ["bus_depot", "office"]},
    {"code": "HLTH", "name": "Health Department", "weight": 0.12,
     "site_types": ["hospital", "office"]},
    {"code": "PANCH", "name": "Panchayat Department", "weight": 0.08,
     "site_types": ["office", "pds_shop", "godown"]},
]

# (name, lon, lat, weight). Weights approximate relative population; exact values do not
# matter, only that cities cluster and the two gap districts stay sparse.
DISTRICTS: list[tuple[str, float, float, float]] = [
    ("Ahmedabad", 72.5714, 23.0225, 100.0),
    ("Surat", 72.8311, 21.1702, 90.0),
    ("Vadodara", 73.1812, 22.3072, 55.0),
    ("Rajkot", 70.8022, 22.3039, 48.0),
    ("Bhavnagar", 72.1519, 21.7645, 26.0),
    ("Jamnagar", 70.0577, 22.4707, 22.0),
    ("Junagadh", 70.4579, 21.5222, 20.0),
    ("Gandhinagar", 72.6369, 23.2156, 18.0),
    ("Anand", 72.9289, 22.5645, 17.0),
    ("Kutch", 69.8597, 23.7337, 16.0),
    ("Mehsana", 72.3693, 23.5880, 15.0),
    ("Bharuch", 72.9951, 21.7051, 14.0),
    ("Navsari", 72.9270, 20.9467, 12.0),
    ("Valsad", 72.9342, 20.5992, 12.0),
    ("Patan", 72.1302, 23.8493, 10.0),
    ("Amreli", 71.2200, 21.6032, 9.0),
    ("Porbandar", 69.6293, 21.6417, 7.0),
    ("Somnath", 70.4013, 20.8880, 6.0),
    ("Dwarka", 68.9685, 22.2394, 5.0),
    # Deliberately sparse — these are the visible coverage gaps in the demo.
    ("Dahod", 74.2599, 22.8352, 1.2),
    ("Narmada", 73.5000, 21.8700, 0.9),
]

CAMERA_TYPES = [
    ("fixed", 0.44), ("ptz", 0.18), ("dome", 0.16),
    ("bullet", 0.14), ("anpr", 0.06), ("thermal", 0.02),
]
CONNECTIVITY = [("fiber", 0.52), ("4g", 0.26), ("lan", 0.14), ("wifi", 0.06), ("5g", 0.02)]

# District spread in degrees — cameras cluster near the centroid with a long tail.
SPREAD_DEG = 0.16


def _weighted(rng: random.Random, options: list[tuple[str, float]]) -> str:
    total = sum(weight for _, weight in options)
    pick = rng.random() * total
    cumulative = 0.0
    for value, weight in options:
        cumulative += weight
        if pick <= cumulative:
            return value
    return options[-1][0]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def generate_cameras(count: int = 80_000, seed: int = 2026) -> list[dict[str, Any]]:
    rng = random.Random(seed)

    district_weights = [d[3] for d in DISTRICTS]
    dept_weights = [d["weight"] for d in DEPARTMENTS]

    cameras: list[dict[str, Any]] = []
    for index in range(count):
        district = rng.choices(DISTRICTS, weights=district_weights, k=1)[0]
        name, centre_lon, centre_lat, _ = district
        department = rng.choices(DEPARTMENTS, weights=dept_weights, k=1)[0]

        # Gaussian scatter around the centroid produces dense cores and sparse edges,
        # which is what real deployments look like and what makes gaps visible.
        lon = _clamp(rng.gauss(centre_lon, SPREAD_DEG), 68.0, 74.6)
        lat = _clamp(rng.gauss(centre_lat, SPREAD_DEG), 20.0, 24.8)

        camera_type = _weighted(rng, CAMERA_TYPES)
        directional = camera_type in ("fixed", "bullet", "anpr")

        # 12% of the fleet offline, plus a heavier concentration in one district so the
        # installed-vs-effective coverage delta is dramatic where it is demonstrated.
        offline_rate = 0.34 if name == "Bhavnagar" else 0.10
        roll = rng.random()
        if roll < offline_rate:
            status = "offline"
        elif roll < offline_rate + 0.04:
            status = "maintenance"
        elif roll < offline_rate + 0.06:
            status = "unknown"
        else:
            status = "online"

        cameras.append(
            {
                "external_camera_id": f"{department['code']}-{index + 1:06d}",
                "department_code": department["code"],
                "district": name,
                "name": f"{name} {rng.choice(['Junction', 'Chowk', 'Circle', 'Gate', 'Depot'])} {rng.randint(1, 99)}",
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "camera_type": camera_type,
                "camera_technology": "analog" if rng.random() < 0.18 else "ip",
                "azimuth_deg": round(rng.uniform(0, 359.9), 1) if directional else None,
                "fov_deg": rng.choice([60.0, 75.0, 90.0, 110.0]) if directional else None,
                "range_m": (
                    rng.choice([80.0, 100.0, 120.0]) if directional
                    else rng.choice([200.0, 250.0, 300.0])
                ),
                "height_m": round(rng.uniform(3.0, 9.0), 1),
                "resolution": rng.choice(["1280x720", "1920x1080", "2560x1440", "3840x2160"]),
                "has_night_vision": rng.random() < 0.62,
                "connectivity": _weighted(rng, CONNECTIVITY),
                "storage_type": rng.choice(["local", "local", "cloud"]),
                "retention_days": rng.choice([7, 15, 15, 30]),
                "ownership_class": "private" if rng.random() < 0.09 else "government",
                "site_type": rng.choice(department["site_types"]),
                "status": status,
                "amc_vendor": rng.choice(
                    ["Hikvision GJ", "CP Plus West", "Bosch India", "Matrix Systems", None]
                ),
            }
        )

    return cameras


async def main(count: int = 80_000) -> None:
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.core.enums import SourceType
    from app.models.department import Department
    from app.schemas.ingestion import RawCameraRecord
    from app.services.ingestion import IngestionService

    cameras = generate_cameras(count=count)

    async with SessionLocal() as session:
        departments = {
            d.code: d
            for d in (await session.execute(select(Department))).scalars().all()
        }
        missing = {c["department_code"] for c in cameras} - departments.keys()
        if missing:
            raise SystemExit(f"Run seeds.departments first; missing: {sorted(missing)}")

        service = IngestionService(session)
        by_department: dict[str, list[dict]] = {}
        for camera in cameras:
            by_department.setdefault(camera["department_code"], []).append(camera)

        for code, rows in by_department.items():
            department = departments[code]
            for start in range(0, len(rows), 2000):
                chunk = rows[start : start + 2000]
                await service.ingest(
                    [
                        RawCameraRecord(
                            payload=row,
                            department_id=department.id,
                            source_type=SourceType.CSV,
                        )
                        for row in chunk
                    ],
                    department,
                    mode="commit",
                )
                print(f"{code}: {start + len(chunk)}/{len(rows)}")

    print(f"Seeded {len(cameras):,} cameras")


if __name__ == "__main__":
    import sys

    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 80_000))
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `pytest tests/seeds/test_synthetic.py -v`
Expected: 8 passed

- [ ] **Step 5: Seed the database and check tile performance**

Run:
```bash
python -m seeds.departments
python -m seeds.synthetic 80000
docker compose exec db psql -U sentinel -d sentinel -c \
  "SELECT current_status, count(*) FROM cameras GROUP BY 1 ORDER BY 2 DESC;"
time curl -s -o /dev/null "localhost:8000/api/v1/tiles/cameras/8/180/112.mvt" \
  -H "Authorization: Bearer <token>"
```
Expected: 80,000 rows across four statuses, and the low-zoom cluster tile returning in well
under a second. If it is slow, run `VACUUM ANALYZE cameras;` — the planner needs statistics
after a bulk load.

- [ ] **Step 6: Commit**

```bash
git add seeds/synthetic.py tests/seeds/test_synthetic.py
git commit -m "feat: 80k synthetic camera generator with deliberate coverage gaps"
```

---

## Task 4: Five department CSVs with five different schemas

This is what makes the `field_mappings` demo real rather than illustrative.

**Files:**
- Create: `seeds/fixtures/*.csv`, `seeds/field_mappings.py`

- [ ] **Step 1: Create the fixture CSVs**

```bash
mkdir -p seeds/fixtures

cat > seeds/fixtures/police.csv <<'CSV'
camera_id,latitude,longitude,type,status,location_name,ward
POL-A-001,23.0225,72.5714,FIXED,ACTIVE,Nehru Bridge East,Ward 12
POL-A-002,23.0301,72.5799,PTZ,ACTIVE,Ashram Road Junction,Ward 12
POL-A-003,23.0180,72.5650,FIXED,DOWN,Ellis Bridge West,Ward 11
CSV

cat > seeds/fixtures/municipal.csv <<'CSV'
cam_no,lat_dd,long_dd,cam_kind,working,place,pole_no,engineer
MUN/2024/0001,23.0410,72.5501,DOME,1,Vastrapur Lake Gate 2,P-4471,R. Patel
MUN/2024/0002,23.0388,72.5478,BULLET,1,Vastrapur Circle,P-4472,R. Patel
MUN/2024/0003,23.0455,72.5620,DOME,0,Drive In Road,P-4480,S. Mehta
CSV

cat > seeds/fixtures/gsrtc.csv <<'CSV'
AssetCode,GPS_Lat,GPS_Long,DeviceType,OperationalState,DepotName,InstalledOn
GSRTC-DEP-0001,23.0272,72.6010,IP-BULLET,UP,Geeta Mandir Depot,2023-04-11
GSRTC-DEP-0002,21.1702,72.8311,IP-DOME,UP,Surat Central Depot,2023-06-02
GSRTC-DEP-0003,22.3072,73.1812,ANALOG-BULLET,MAINT,Vadodara Depot,2022-11-19
CSV

cat > seeds/fixtures/health.csv <<'CSV'
facility_cam_ref,coord_lat,coord_lon,camera_category,live,facility,retention
HLTH-CH-0001,23.0339,72.5476,FIXED,YES,Civil Hospital Gate,15
HLTH-CH-0002,23.0341,72.5480,FIXED,NO,Civil Hospital OPD Corridor,15
HLTH-CH-0003,21.1959,72.8302,PTZ,YES,New Civil Surat Entrance,30
CSV

cat > seeds/fixtures/panchayat.csv <<'CSV'
id,latitude_dms,longitude_dms,kind,state,village,scheme
PAN-DHD-001,22 50 06.7 N,74 15 35.6 E,FIXED,OK,Dahod Taluka Office,PDS
PAN-DHD-002,22 49 55.0 N,74 15 12.0 E,FIXED,FAULT,Dahod Godown 3,PDS
PAN-NRM-001,21 52 12.0 N,73 30 00.0 E,BULLET,OK,Rajpipla Panchayat Bhavan,ADMIN
CSV
```

Five genuinely different shapes: different column names, different status vocabularies
(`ACTIVE`/`1`/`UP`/`YES`/`OK`), different type words, and Panchayat records coordinates in
**degrees-minutes-seconds** rather than decimal degrees.

- [ ] **Step 2: Create `seeds/field_mappings.py`**

```python
import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.department import Department
from app.models.field_mapping import FieldMapping

CONFIGS: dict[str, dict] = {
    "POL": {
        "column_map": {
            "camera_id": "external_camera_id", "latitude": "latitude",
            "longitude": "longitude", "type": "camera_type", "status": "status",
            "location_name": "name",
        },
        "value_maps": {
            "status": {"ACTIVE": "online", "DOWN": "offline", "AMC": "maintenance"},
            "camera_type": {"FIXED": "fixed", "PTZ": "ptz"},
        },
        "defaults": {"connectivity": "fiber", "ownership_class": "government"},
        "passthrough_to_metadata": True,
    },
    "MUN": {
        "column_map": {
            "cam_no": "external_camera_id", "lat_dd": "latitude", "long_dd": "longitude",
            "cam_kind": "camera_type", "working": "status", "place": "name",
        },
        "value_maps": {
            "status": {"1": "online", "0": "offline"},
            "camera_type": {"DOME": "dome", "BULLET": "bullet"},
        },
        "defaults": {"connectivity": "fiber", "site_type": "public_space"},
        "passthrough_to_metadata": True,
    },
    "GSRTC": {
        "column_map": {
            "AssetCode": "external_camera_id", "GPS_Lat": "latitude",
            "GPS_Long": "longitude", "DeviceType": "camera_type",
            "OperationalState": "status", "DepotName": "name",
            "InstalledOn": "install_date",
        },
        "value_maps": {
            "status": {"UP": "online", "DOWN": "offline", "MAINT": "maintenance"},
            "camera_type": {
                "IP-BULLET": "bullet", "IP-DOME": "dome", "ANALOG-BULLET": "bullet",
            },
            "camera_technology": {"ANALOG-BULLET": "analog"},
        },
        "defaults": {"site_type": "bus_depot", "connectivity": "lan"},
        "passthrough_to_metadata": True,
    },
    "HLTH": {
        "column_map": {
            "facility_cam_ref": "external_camera_id", "coord_lat": "latitude",
            "coord_lon": "longitude", "camera_category": "camera_type",
            "live": "status", "facility": "name", "retention": "retention_days",
        },
        "value_maps": {
            "status": {"YES": "online", "NO": "offline"},
            "camera_type": {"FIXED": "fixed", "PTZ": "ptz"},
        },
        "defaults": {"site_type": "hospital", "connectivity": "lan"},
        "passthrough_to_metadata": True,
    },
    "PANCH": {
        "column_map": {
            "id": "external_camera_id", "latitude_dms": "latitude",
            "longitude_dms": "longitude", "kind": "camera_type",
            "state": "status", "village": "name",
        },
        "value_maps": {
            "status": {"OK": "online", "FAULT": "offline"},
            "camera_type": {"FIXED": "fixed", "BULLET": "bullet"},
        },
        "defaults": {"connectivity": "4g", "site_type": "office"},
        "coordinate_format": "dms",
        "passthrough_to_metadata": True,
    },
}


async def main() -> None:
    async with SessionLocal() as session:
        for code, config in CONFIGS.items():
            department = (
                await session.execute(select(Department).where(Department.code == code))
            ).scalar_one_or_none()
            if department is None:
                print(f"skipping {code}: department not seeded")
                continue
            existing = (
                await session.execute(
                    select(FieldMapping).where(FieldMapping.department_id == department.id)
                )
            ).scalars().first()
            if existing:
                existing.config = config
            else:
                session.add(
                    FieldMapping(department_id=department.id, version=1, config=config)
                )
        await session.commit()
    print(f"Configured {len(CONFIGS)} department field mappings")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Verify each fixture imports cleanly**

Run:
```bash
python -m seeds.departments
python -m seeds.field_mappings
for f in police municipal gsrtc health panchayat; do
  echo "--- $f ---"
  curl -s -X POST "localhost:8000/api/v1/onboarding/preview?department_id=<CODE_UUID>" \
    -H "Authorization: Bearer <token>" \
    -F "file=@seeds/fixtures/$f.csv" | python -m json.tool | head -20
done
```
Expected: every file previews with `failed: 0`. The Panchayat file is the one to watch —
its DMS coordinates must resolve to decimal degrees inside Gujarat.

- [ ] **Step 4: Commit**

```bash
git add seeds/fixtures seeds/field_mappings.py
git commit -m "feat: five department fixtures with genuinely different schemas"
```

---

## Task 5: Documentation deliverables

**Files:**
- Create: `README.md`, `docs/api/onboarding-guide.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# Sentinel CCTV Registry — Model 1

Centralised CCTV Registry & GIS Foundation for the Gujarat Police Innovation
Challenge 2026. Metadata and asset visibility only — no video streaming, recording,
or analytics. Model 1 is the mandatory foundation that Models 2–4 query.

## Quick start

```bash
docker compose up -d db redis
pip install -e ".[dev]"
alembic upgrade head
python -m seeds.departments
python -m seeds.field_mappings
python -m seeds.boundaries
python -m seeds.users
python -m seeds.synthetic 80000
uvicorn app.main:app --reload --port 8000
cd web && npm install && npm run dev
```

Portal: http://localhost:3000 · API docs: http://localhost:8000/docs

Demo accounts (password `Sentinel@2026`): `root@`, `amc.admin@`, `analyst@`,
`viewer@` — all `gujarat.gov.in`.

## What it does

| Capability | Where |
|---|---|
| Bulk CSV import with validate → preview → import | `/onboarding/import` |
| Manual camera entry | `/cameras/new` |
| API onboarding for departmental systems | `POST /api/v1/onboarding/bulk` |
| Live onboarding from the Sentinel sandbox catalogue | `POST /api/v1/onboarding/adapters/sentinel/sync` |
| GIS map with layer toggles and filters | `/map` |
| Radius and district spatial search | `GET /api/v1/cameras/nearby` |
| Health monitoring with downtime ranking | `/health` |
| Coverage gap analysis | `/coverage` |
| CSV export and per-camera audit trail | `/cameras` |

## Architecture

FastAPI (routers → services → repositories) over PostgreSQL + PostGIS, Next.js and
MapLibre GL JS on the front, arq + Redis for background work.

Every onboarding path — CSV, form, REST, adapter — funnels through one
`IngestionService.ingest()` call, so validation and normalization cannot diverge by
source. Per-department `field_mappings` JSONB config translates each department's
field names and status vocabulary into canonical enums, which means onboarding a new
department is a config row rather than a code change.

Ingestion is idempotent on `(department_id, external_camera_id)`: re-running a nightly
sync produces zero writes and zero audit entries when nothing changed.

## For Models 2–4

```python
import jwt, httpx

jwks = httpx.get("http://<registry>/.well-known/jwks.json").json()
key = jwt.PyJWK.from_dict(jwks["keys"][0]).key
claims = jwt.decode(token, key, algorithms=["RS256"], audience="sentinel-platform")

streams = httpx.get(
    f"http://<registry>/api/v1/cameras/{camera_id}/streams",
    headers={"X-API-Key": key},
).json()
```

Pick the endpoint whose `reachability` matches your network: `public_cdn` works
anywhere, `direct_ip` needs gateway ports 8554/8889 open. Subscribe to
`camera.status_changed` via `POST /api/v1/webhooks` to react without polling.

## Known limitations

Stated plainly rather than hidden:

- Rate limiting records a tier per API key and exposes a middleware hook, but
  enforcement is intended at an API gateway and is not implemented.
- API key rotation is designed (overlapping validity keyed on `key_prefix`), not built.
- `camera_health` partitioning by month is designed, not built.
- Active health probing covers 200 cameras per five-minute pass, ordered by staleness.
  Full-fleet coverage needs the partitioned worker pool described in the HLD.
- Coverage analysis is 2D with no terrain or building occlusion, uses nominal range per
  camera type, and assumes recorded bearings are accurate. Cameras without a recorded
  bearing are treated as omnidirectional, which overstates their contribution — the
  generated report says so and counts them.
```

- [ ] **Step 2: Write `docs/api/onboarding-guide.md`**

```markdown
# Onboarding a department

Three steps, no code changes.

## 1. Register the department

```bash
curl -X POST localhost:8000/api/v1/departments \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"code":"RTO","name":"Regional Transport Office"}'
```

## 2. Describe their data shape

Post a `field_mappings` config translating their vocabulary to ours. Only
`column_map` is required; everything else has sensible defaults.

```json
{
  "column_map": { "veh_cam_id": "external_camera_id", "y": "latitude", "x": "longitude" },
  "value_maps": { "status": { "RUNNING": "online", "HALTED": "offline" } },
  "defaults": { "site_type": "rto_checkpoint", "connectivity": "lan" },
  "coordinate_format": "decimal_degrees",
  "passthrough_to_metadata": true
}
```

Two guarantees worth knowing:

- **Unmapped columns are kept**, not dropped. With `passthrough_to_metadata` they land
  in `cameras.metadata` and can be promoted to real columns later.
- **Unmapped values warn, never fail.** A status word we have never seen normalizes to
  `unknown` and raises a row warning, so a new vocabulary term cannot break a nightly sync.

## 3. Send data by any of three routes

All three run identical validation and normalization.

**File upload** — validate first, then commit:

```bash
curl -X POST "localhost:8000/api/v1/onboarding/preview?department_id=$DEPT" \
  -H "X-API-Key: $KEY" -F "file=@cameras.csv"

curl -X POST "localhost:8000/api/v1/onboarding/import?department_id=$DEPT" \
  -H "X-API-Key: $KEY" -F "file=@cameras.csv"
```

**REST** — for a departmental system pushing directly:

```bash
curl -X POST "localhost:8000/api/v1/onboarding/bulk?department_id=$DEPT" \
  -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '[{"external_camera_id":"RTO-1","latitude":23.02,"longitude":72.57,
        "department_id":"'$DEPT'"}]'
```

**Adapter pull** — for a source with its own catalogue endpoint:

```bash
curl -X POST "localhost:8000/api/v1/onboarding/adapters/sentinel/sync" \
  -H "X-API-Key: $KEY"
```

## Idempotency

Records dedupe on `(department_id, external_camera_id)`. Re-sending identical data
returns `skipped` and writes nothing — no duplicate rows, no audit noise. Safe to run
nightly on a cron.

## Row-level errors

`preview` returns per-row results so a bad file can be fixed before anything is written:

```json
{ "total": 2, "created": 1, "failed": 1,
  "rows": [
    { "row_number": 2, "external_camera_id": "RTO-1", "outcome": "created" },
    { "row_number": 3, "outcome": "failed",
      "errors": [ { "code": "outside_gujarat", "field": "location",
                    "message": "Point (28.61, 77.20) falls outside the Gujarat bounding box." } ] }
  ] }
```
```

- [ ] **Step 3: Export the finished OpenAPI spec**

Run:
```bash
curl -s localhost:8000/openapi.json > docs/api/openapi.json
python -c "
import json
spec = json.load(open('docs/api/openapi.json'))
print(len(spec['paths']), 'documented paths')
"
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/api
git commit -m "docs: README, onboarding guide and exported OpenAPI spec"
```

---

## Task 6: Demo rehearsal

**Files:**
- Create: `docs/demo-script.md`

- [ ] **Step 1: Write `docs/demo-script.md`**

```markdown
# Model 1 demo script

Target: 4 minutes. Rehearse with the network disabled and the PMTiles basemap on.

## Before you start

- `docker compose up -d` — db, redis, basemap
- `alembic upgrade head` and every seed script run
- `NEXT_PUBLIC_BASEMAP=pmtiles npm run dev`
- Logged out, browser at `/login`
- A terminal open at the repo root

## 0:00 — Scale, immediately

Log in as `root@gujarat.gov.in`. Land on `/map` zoomed to all of Gujarat.

> "This is 80,000 cameras across five departments, served as vector tiles from PostGIS.
> The map is rendering the whole state at once."

Zoom into Ahmedabad. Markers separate from clusters. Colour by status.

## 0:45 — Onboarding anything

Open `/onboarding/import`, upload `seeds/fixtures/panchayat.csv`.

> "Panchayat sends degrees-minutes-seconds coordinates, calls a working camera 'OK',
> and names its id column 'id'. We didn't write code for that — it's a config row."

Show the preview: rows validated, coordinates converted, one deliberate error caught
with its row number and reason. Commit. The cameras appear on the map.

## 1:30 — Onboarding from the government's own catalogue

Run the Sentinel adapter live:

```bash
curl -X POST localhost:8000/api/v1/onboarding/adapters/sentinel/sync \
  -H "Authorization: Bearer $TOKEN"
```

> "That just pulled the Sentinel sandbox catalogue and onboarded the real government
> cameras through the exact same validation path as the CSV."

Click one on the map. The drawer shows all three endpoints — HLS, RTSP, WHEP — each
tagged with its reachability.

> "This is what Models 2 to 4 call. They don't keep their own camera list."

Run the same command a second time.

> "Zero created, thirty skipped. Idempotent — safe to run nightly."

## 2:30 — Health, on real infrastructure

Open `/health`.

> "These statuses came from probing the government's own HLS manifests. Sorted by how
> long each camera has been down, not when we last checked it."

## 3:00 — The gap analysis

Open `/coverage`, choose Bhavnagar, run it.

> "Installed coverage 38%. Effective coverage 24%. That 14-point gap isn't missing
> cameras — it's cameras that are broken. Fixing them recovers the coverage without
> buying a single new device."

Open the report. Scroll to the limitations section.

> "It says what it can't model — no terrain, no buildings, nominal ranges — and it
> counts the cameras with no recorded bearing, because those overstate our numbers."

## 3:40 — RBAC, in one move

Log out, log in as `viewer@gujarat.gov.in`. Same map, no export button. Try the import
endpoint and get a 403.

> "Read is statewide by design — the point is removing blind spots. Write is scoped to
> your own department. Every change is in the audit trail."

## If something breaks

- Map grey → PMTiles container down: `docker compose up -d basemap`
- Sentinel sync 401 → session cookie expired; re-copy from the portal
- Slow tiles → `VACUUM ANALYZE cameras;`
- Fall back to the recorded run in `docs/demo-recording.mp4`
```

- [ ] **Step 2: Rehearse it end to end, offline, three times**

Time each run. Anything above 4:30 needs cutting, not talking faster.

- [ ] **Step 3: Record the fallback video**

Screen-record one clean run to `docs/demo-recording.mp4`. Live demos on government
networks fail; a recorded run is the difference between a stumble and a disaster.

- [ ] **Step 4: Final full-suite check**

Run: `pytest -v && cd web && npm run build`
Expected: all tests pass and the frontend builds without type errors.

- [ ] **Step 5: Commit**

```bash
git add docs/demo-script.md
git commit -m "docs: demo script with offline fallbacks and failure recovery"
```

---

## Self-review against the spec

**Covered:** §7 `webhook_subscriptions` / `webhook_deliveries`, §8 webhook routes, §13
webhooks in full including the transactional outbox and HMAC signing, §15 seed data at full
scale with the five sandbox departments and deliberate gaps, plus the official deliverables
*"sample camera-metadata dataset"* and *"registry API documentation."*

**Spec sections now complete across all six plans:** §4 through §15, every functional
requirement in the official Model 1 list, and all five official deliverables.

**Remaining polish not owned by any plan** — pick these up only if time allows:
- Coverage overlay layer rendered on `/map` (the tile endpoint exists; only the MapLibre
  layer is missing)
- Field-mapping editor UI (the API exists; editing is currently done via `seeds/`)
- `/auth/refresh` route (refresh tokens are issued but unused)
- `/cameras/new` manual entry form with the azimuth compass control — **note this one is a
  named official deliverable ("manual onboarding demonstration"), so if it has not been
  built by the end of Plan 2, it must be done before the demo.** The `POST /api/v1/cameras`
  endpoint behind it already works.
