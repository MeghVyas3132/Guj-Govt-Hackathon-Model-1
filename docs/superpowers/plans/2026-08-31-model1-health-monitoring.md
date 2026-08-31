# Model 1 Health Monitoring — Implementation Plan (Plan 3 of 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Know which cameras are down, for how long, and prove it against the live Sentinel feeds rather than fixtures.

**Architecture:** `camera_health` is an append-only observation log — the truth. `cameras.current_status` and `status_since` are a denormalized projection updated **only on state change**, which makes "offline longest" an index scan and gives an honest downtime clock. Probing runs as a scheduled arq job using a cheapest-first ladder so it stays affordable as camera count grows.

**Tech Stack:** PostgreSQL time-series table, arq + Redis scheduled jobs, httpx for HLS manifest probes.

**Prerequisites:** Plans 1 and 2 complete.

**Why this matters for scoring:** FAQ #38 lists "health monitoring" and "operational dashboards" as named bonus criteria. Because the Sentinel feeds are supervised and restart, this can be demonstrated against real government infrastructure — not a simulation.

---

## File structure additions

```
app/
  models/camera_health.py
  schemas/health.py
  services/health.py                 record_observation + transition logic
  services/probe.py                  the probe ladder
  repositories/health.py
  api/v1/routers/health.py
  workers/tasks.py                   arq definitions + cron schedule
web/
  app/health/page.tsx
  components/OfflineTable.tsx
  components/HealthSparkline.tsx
```

---

## Task 1: The health observation table

**Files:**
- Create: `app/models/camera_health.py`, `app/schemas/health.py`
- Test: `tests/repositories/test_camera_health.py`

- [ ] **Step 1: Create `app/models/camera_health.py`**

```python
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class CameraHealth(Base, UUIDMixin):
    """Append-only observation log. Never updated, never deleted.

    Monthly range partitioning on observed_at is the growth path at 80k cameras;
    documented in the spec, not implemented here.
    """

    __tablename__ = "camera_health"
    __table_args__ = (
        Index("ix_camera_health_camera_observed", "camera_id", "observed_at"),
    )

    camera_id: Mapped[UUID] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(16))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source: Mapped[str] = mapped_column(String(16), default="probe")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
```

The composite index is `(camera_id, observed_at)` ascending rather than descending because
PostgreSQL can scan a b-tree backwards at no cost, and ascending order also serves the
sparkline query which reads a window forwards.

- [ ] **Step 2: Create `app/schemas/health.py`**

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import CameraStatus


class HealthObservationIn(BaseModel):
    """One observation pushed by a department system or produced by our prober."""

    external_camera_id: str | None = Field(
        default=None, description="Use this when pushing by the department's own id."
    )
    camera_id: UUID | None = None
    status: CameraStatus
    observed_at: datetime | None = None
    latency_ms: int | None = None
    detail: dict = Field(default_factory=dict)


class HealthObservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    camera_id: UUID
    status: CameraStatus
    observed_at: datetime
    source: str
    latency_ms: int | None = None


class OfflineCamera(BaseModel):
    camera_id: UUID
    camera_uid: str
    name: str | None
    department_code: str | None
    latitude: float
    longitude: float
    status_since: datetime | None
    downtime_seconds: float


class HealthSummary(BaseModel):
    total: int
    online: int
    offline: int
    unknown: int
    maintenance: int
    offline_over_24h: int
    offline_over_7d: int
```

- [ ] **Step 3: Generate and run the migration**

Run:
```bash
alembic revision --autogenerate -m "camera health observations"
alembic upgrade head
```

- [ ] **Step 4: Write the failing test**

Create `tests/repositories/test_camera_health.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.camera import Camera
from app.models.camera_health import CameraHealth


@pytest.fixture
async def camera(session, seeded_department):
    cam = Camera(
        camera_uid="GJ-AMC-000001",
        department_id=seeded_department,
        external_camera_id="A-1",
        location="SRID=4326;POINT(72.5714 23.0225)",
    )
    session.add(cam)
    await session.commit()
    return cam


@pytest.mark.asyncio
async def test_observations_accumulate_without_overwriting(session, camera):
    base = datetime(2026, 9, 1, tzinfo=UTC)
    for offset, status in enumerate(["online", "offline", "online"]):
        session.add(
            CameraHealth(
                camera_id=camera.id,
                status=status,
                observed_at=base + timedelta(minutes=offset),
            )
        )
    await session.commit()

    rows = (
        (
            await session.execute(
                select(CameraHealth)
                .where(CameraHealth.camera_id == camera.id)
                .order_by(CameraHealth.observed_at)
            )
        )
        .scalars()
        .all()
    )
    assert [r.status for r in rows] == ["online", "offline", "online"]
```

- [ ] **Step 5: Run the test**

Run: `pytest tests/repositories/test_camera_health.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/models/camera_health.py app/schemas/health.py alembic tests/repositories/test_camera_health.py
git commit -m "feat: append-only camera health observation table"
```

---

## Task 2: HealthService and the state-change rule

The single most important behaviour in this plan: `status_since` must reflect when the
camera *entered* its current state, not when it was last observed. Getting this wrong makes
every downtime figure on the dashboard a lie.

**Files:**
- Create: `app/services/health.py`
- Test: `tests/services/test_health_service.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_health_service.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.enums import CameraStatus
from app.models.camera import Camera
from app.models.camera_health import CameraHealth
from app.schemas.health import HealthObservationIn
from app.services.health import HealthService


@pytest.fixture
async def camera(session, seeded_department):
    cam = Camera(
        camera_uid="GJ-AMC-000001",
        department_id=seeded_department,
        external_camera_id="A-1",
        location="SRID=4326;POINT(72.5714 23.0225)",
        current_status="unknown",
    )
    session.add(cam)
    await session.commit()
    return cam


@pytest.mark.asyncio
async def test_first_observation_sets_status_and_status_since(session, camera):
    at = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    await HealthService(session).record(
        camera, HealthObservationIn(status=CameraStatus.ONLINE, observed_at=at)
    )
    await session.refresh(camera)
    assert camera.current_status == "online"
    assert camera.status_since == at


@pytest.mark.asyncio
async def test_repeated_same_status_does_not_move_status_since(session, camera):
    service = HealthService(session)
    first = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    await service.record(
        camera, HealthObservationIn(status=CameraStatus.OFFLINE, observed_at=first)
    )
    for minutes in (5, 10, 15):
        await service.record(
            camera,
            HealthObservationIn(
                status=CameraStatus.OFFLINE, observed_at=first + timedelta(minutes=minutes)
            ),
        )
    await session.refresh(camera)

    # The camera has been down since 10:00, not since the most recent probe.
    assert camera.status_since == first
    assert camera.last_seen_at == first + timedelta(minutes=15)


@pytest.mark.asyncio
async def test_status_change_resets_status_since(session, camera):
    service = HealthService(session)
    first = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    recovery = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    await service.record(
        camera, HealthObservationIn(status=CameraStatus.OFFLINE, observed_at=first)
    )
    await service.record(
        camera, HealthObservationIn(status=CameraStatus.ONLINE, observed_at=recovery)
    )
    await session.refresh(camera)
    assert camera.current_status == "online"
    assert camera.status_since == recovery


@pytest.mark.asyncio
async def test_every_observation_is_logged_even_when_status_is_unchanged(session, camera):
    service = HealthService(session)
    base = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    for minutes in range(4):
        await service.record(
            camera,
            HealthObservationIn(
                status=CameraStatus.ONLINE, observed_at=base + timedelta(minutes=minutes)
            ),
        )
    count = (
        await session.execute(select(func.count()).select_from(CameraHealth))
    ).scalar_one()
    assert count == 4


@pytest.mark.asyncio
async def test_record_reports_whether_the_state_changed(session, camera):
    service = HealthService(session)
    base = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    first = await service.record(
        camera, HealthObservationIn(status=CameraStatus.ONLINE, observed_at=base)
    )
    second = await service.record(
        camera,
        HealthObservationIn(
            status=CameraStatus.ONLINE, observed_at=base + timedelta(minutes=1)
        ),
    )
    third = await service.record(
        camera,
        HealthObservationIn(
            status=CameraStatus.OFFLINE, observed_at=base + timedelta(minutes=2)
        ),
    )
    assert first.changed is True
    assert second.changed is False
    assert third.changed is True


@pytest.mark.asyncio
async def test_out_of_order_observation_does_not_rewind_last_seen(session, camera):
    service = HealthService(session)
    late = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    early = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    await service.record(
        camera, HealthObservationIn(status=CameraStatus.ONLINE, observed_at=late)
    )
    await service.record(
        camera, HealthObservationIn(status=CameraStatus.ONLINE, observed_at=early)
    )
    await session.refresh(camera)
    assert camera.last_seen_at == late
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/services/test_health_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.health'`

- [ ] **Step 3: Create `app/services/health.py`**

```python
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera
from app.models.camera_health import CameraHealth
from app.schemas.health import HealthObservationIn


@dataclass
class RecordOutcome:
    changed: bool
    previous_status: str
    new_status: str


class HealthService:
    """Writes the observation log and projects the current state onto the camera row.

    status_since marks when the camera ENTERED its current state, so a camera observed
    offline every five minutes since 10:00 still reports 10:00 — which is what the
    downtime column on the dashboard depends on.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self, camera: Camera, observation: HealthObservationIn, source: str = "probe"
    ) -> RecordOutcome:
        observed_at = observation.observed_at or datetime.now(UTC)
        status = observation.status.value

        self.session.add(
            CameraHealth(
                camera_id=camera.id,
                status=status,
                observed_at=observed_at,
                source=source,
                latency_ms=observation.latency_ms,
                detail=observation.detail,
            )
        )

        previous = camera.current_status
        changed = previous != status

        if changed:
            camera.current_status = status
            camera.status_since = observed_at

        # Guard against a delayed or replayed observation moving the clock backwards.
        if camera.last_seen_at is None or observed_at > camera.last_seen_at:
            camera.last_seen_at = observed_at

        await self.session.flush()
        return RecordOutcome(
            changed=changed, previous_status=previous, new_status=status
        )
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `pytest tests/services/test_health_service.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/health.py tests/services/test_health_service.py
git commit -m "feat: health service with correct status_since transition semantics"
```

---

## Task 3: Batch health push API

**Files:**
- Create: `app/api/v1/routers/health.py`, `app/repositories/health.py`
- Modify: `app/api/v1/router.py`
- Test: `tests/api/test_health_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_health_api.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from app.models.camera import Camera


@pytest.fixture
async def cameras(session, seeded_department):
    rows = []
    for index in range(1, 4):
        cam = Camera(
            camera_uid=f"GJ-AMC-00000{index}",
            department_id=seeded_department,
            external_camera_id=f"A-{index}",
            location=f"SRID=4326;POINT(72.5{index} 23.0{index})",
            current_status="unknown",
        )
        session.add(cam)
        rows.append(cam)
    await session.commit()
    return rows


@pytest.mark.asyncio
async def test_batch_push_by_external_id(api_client, cameras, seeded_department):
    response = await api_client.post(
        f"/api/v1/health/observations?department_id={seeded_department}",
        json=[
            {"external_camera_id": "A-1", "status": "online"},
            {"external_camera_id": "A-2", "status": "offline"},
        ],
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    assert body["unmatched"] == []


@pytest.mark.asyncio
async def test_unknown_external_id_is_reported_not_fatal(
    api_client, cameras, seeded_department
):
    response = await api_client.post(
        f"/api/v1/health/observations?department_id={seeded_department}",
        json=[
            {"external_camera_id": "A-1", "status": "online"},
            {"external_camera_id": "GHOST", "status": "offline"},
        ],
    )
    assert response.status_code == 202
    assert response.json()["accepted"] == 1
    assert response.json()["unmatched"] == ["GHOST"]


@pytest.mark.asyncio
async def test_offline_list_is_sorted_by_longest_downtime_first(
    api_client, session, cameras, seeded_department
):
    now = datetime.now(UTC)
    cameras[0].current_status = "offline"
    cameras[0].status_since = now - timedelta(days=9)
    cameras[1].current_status = "offline"
    cameras[1].status_since = now - timedelta(hours=3)
    await session.commit()

    response = await api_client.get("/api/v1/health/offline")
    items = response.json()["items"]

    assert [i["camera_uid"] for i in items] == ["GJ-AMC-000001", "GJ-AMC-000002"]
    assert items[0]["downtime_seconds"] > items[1]["downtime_seconds"]
    assert items[0]["downtime_seconds"] > 7 * 86400


@pytest.mark.asyncio
async def test_summary_counts_by_status_and_downtime_band(
    api_client, session, cameras
):
    now = datetime.now(UTC)
    cameras[0].current_status = "offline"
    cameras[0].status_since = now - timedelta(days=9)
    cameras[1].current_status = "online"
    cameras[2].current_status = "maintenance"
    await session.commit()

    summary = (await api_client.get("/api/v1/health/summary")).json()
    assert summary["total"] == 3
    assert summary["offline"] == 1
    assert summary["online"] == 1
    assert summary["maintenance"] == 1
    assert summary["offline_over_7d"] == 1
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/api/test_health_api.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Create `app/repositories/health.py`**

```python
from datetime import UTC, datetime, timedelta

from geoalchemy2.shape import to_shape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera
from app.models.department import Department
from app.schemas.health import HealthSummary, OfflineCamera


class HealthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def offline(self, limit: int = 200, offset: int = 0) -> list[OfflineCamera]:
        stmt = (
            select(Camera, Department.code)
            .join(Department, Department.id == Camera.department_id)
            .where(
                Camera.current_status == "offline",
                Camera.is_active,
                Camera.lifecycle_state == "active",
            )
            .order_by(Camera.status_since.asc().nulls_last())
            .limit(limit)
            .offset(offset)
        )
        now = datetime.now(UTC)
        results: list[OfflineCamera] = []
        for camera, dept_code in (await self.session.execute(stmt)).all():
            point = to_shape(camera.location)
            downtime = (
                (now - camera.status_since).total_seconds()
                if camera.status_since
                else 0.0
            )
            results.append(
                OfflineCamera(
                    camera_id=camera.id,
                    camera_uid=camera.camera_uid,
                    name=camera.name,
                    department_code=dept_code,
                    latitude=point.y,
                    longitude=point.x,
                    status_since=camera.status_since,
                    downtime_seconds=downtime,
                )
            )
        return results

    async def summary(self) -> HealthSummary:
        now = datetime.now(UTC)
        base = (Camera.is_active, Camera.lifecycle_state == "active")

        stmt = select(Camera.current_status, func.count()).where(*base).group_by(
            Camera.current_status
        )
        counts = dict((await self.session.execute(stmt)).all())

        async def offline_over(delta: timedelta) -> int:
            stmt = (
                select(func.count())
                .select_from(Camera)
                .where(
                    *base,
                    Camera.current_status == "offline",
                    Camera.status_since < now - delta,
                )
            )
            return (await self.session.execute(stmt)).scalar_one()

        return HealthSummary(
            total=sum(counts.values()),
            online=counts.get("online", 0),
            offline=counts.get("offline", 0),
            unknown=counts.get("unknown", 0),
            maintenance=counts.get("maintenance", 0),
            offline_over_24h=await offline_over(timedelta(hours=24)),
            offline_over_7d=await offline_over(timedelta(days=7)),
        )
```

`ORDER BY status_since ASC` is the whole trick — the oldest transition is the longest
downtime, and the `(current_status, status_since)` index from Plan 1 serves it directly.

- [ ] **Step 4: Create `app/api/v1/routers/health.py`**

```python
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.camera import Camera
from app.repositories.health import HealthRepository
from app.schemas.common import Page
from app.schemas.health import HealthObservationIn, HealthSummary, OfflineCamera
from app.services.health import HealthService

router = APIRouter(prefix="/health", tags=["health"])


class BatchAck(BaseModel):
    accepted: int
    changed: int
    unmatched: list[str]


@router.post(
    "/observations",
    response_model=BatchAck,
    status_code=202,
    summary="Push a batch of health observations",
    description=(
        "Accepts observations keyed by the department's own camera id. Unknown ids are "
        "reported in `unmatched` rather than failing the batch, so a departmental sync "
        "is never blocked by one stale row."
    ),
)
async def push_observations(
    observations: list[HealthObservationIn],
    department_id: UUID = Query(...),
    session: AsyncSession = Depends(get_session),
) -> BatchAck:
    external_ids = [o.external_camera_id for o in observations if o.external_camera_id]
    rows = (
        (
            await session.execute(
                select(Camera).where(
                    Camera.department_id == department_id,
                    Camera.external_camera_id.in_(external_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    by_external = {c.external_camera_id: c for c in rows}

    service = HealthService(session)
    accepted = changed = 0
    unmatched: list[str] = []

    for observation in observations:
        camera = by_external.get(observation.external_camera_id or "")
        if camera is None:
            if observation.external_camera_id:
                unmatched.append(observation.external_camera_id)
            continue
        outcome = await service.record(camera, observation, source="api")
        accepted += 1
        changed += int(outcome.changed)

    await session.commit()
    return BatchAck(accepted=accepted, changed=changed, unmatched=unmatched)


@router.get(
    "/offline",
    response_model=Page[OfflineCamera],
    summary="Currently offline cameras, longest down first",
)
async def offline(
    limit: int = Query(200, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[OfflineCamera]:
    items = await HealthRepository(session).offline(limit=limit, offset=offset)
    return Page(items=items, total=len(items), limit=limit, offset=offset)


@router.get("/summary", response_model=HealthSummary, summary="Fleet health counts")
async def summary(session: AsyncSession = Depends(get_session)) -> HealthSummary:
    return await HealthRepository(session).summary()


@router.get(
    "/cameras/{camera_id}/history",
    summary="Observation history for one camera",
)
async def history(
    camera_id: UUID,
    limit: int = Query(200, le=2000),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    from app.models.camera_health import CameraHealth

    if await session.get(Camera, camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    rows = (
        (
            await session.execute(
                select(CameraHealth)
                .where(CameraHealth.camera_id == camera_id)
                .order_by(CameraHealth.observed_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "status": r.status,
            "observed_at": r.observed_at.isoformat(),
            "source": r.source,
            "latency_ms": r.latency_ms,
        }
        for r in rows
    ]
```

- [ ] **Step 5: Register the router**

In `app/api/v1/router.py`:

```python
from app.api.v1.routers import cameras, health, onboarding, tiles

api_router.include_router(health.router)
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `pytest tests/api/test_health_api.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add app/repositories/health.py app/api/v1/routers/health.py app/api/v1/router.py tests/api/test_health_api.py
git commit -m "feat: health push API, offline-by-downtime list and fleet summary"
```

---

## Task 4: The probe ladder

**Files:**
- Create: `app/services/probe.py`, `app/workers/tasks.py`
- Test: `tests/services/test_probe.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_probe.py`:

```python
import httpx
import pytest

from app.core.enums import CameraStatus
from app.services.probe import HlsProbe


@pytest.mark.asyncio
async def test_serving_manifest_is_reported_online():
    manifest = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nseg1.ts\n"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=manifest)
    )
    result = await HlsProbe(transport=transport).check(
        "https://cctv.corp8.cloud/cam04/index.m3u8"
    )
    assert result.status is CameraStatus.ONLINE
    assert result.latency_ms is not None


@pytest.mark.asyncio
async def test_manifest_with_no_segments_is_reported_offline():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"#EXTM3U\n#EXT-X-VERSION:3\n")
    )
    result = await HlsProbe(transport=transport).check("https://x/index.m3u8")
    assert result.status is CameraStatus.OFFLINE
    assert "no segments" in result.detail["reason"]


@pytest.mark.asyncio
async def test_http_error_is_reported_offline():
    transport = httpx.MockTransport(lambda request: httpx.Response(502))
    result = await HlsProbe(transport=transport).check("https://x/index.m3u8")
    assert result.status is CameraStatus.OFFLINE
    assert result.detail["http_status"] == 502


@pytest.mark.asyncio
async def test_auth_redirect_is_unknown_not_offline():
    # A 302 to the login page means our session expired, not that the camera is down.
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "/auth/login"})
    )
    result = await HlsProbe(transport=transport).check("https://x/index.m3u8")
    assert result.status is CameraStatus.UNKNOWN


@pytest.mark.asyncio
async def test_connection_failure_is_reported_offline():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await HlsProbe(transport=httpx.MockTransport(boom)).check(
        "https://x/index.m3u8"
    )
    assert result.status is CameraStatus.OFFLINE
    assert result.detail["error"] == "ConnectError"
```

Distinguishing "our session expired" from "the camera is down" matters: without it, a
password change would paint the entire fleet red on the dashboard.

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/services/test_probe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.probe'`

- [ ] **Step 3: Create `app/services/probe.py`**

```python
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.enums import CameraStatus


@dataclass
class ProbeResult:
    status: CameraStatus
    latency_ms: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class HlsProbe:
    """Tier 2 of the probe ladder: fetch the HLS manifest and check it lists segments.

    Cheap (a few KB), works through the CDN on any network, and unlike a TCP connect it
    proves the gateway is actually producing media rather than merely accepting sockets.
    """

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 10.0,
        session_cookie: str | None = None,
    ) -> None:
        self.transport = transport
        self.timeout = timeout
        self.session_cookie = session_cookie

    async def check(self, url: str) -> ProbeResult:
        cookies = {"session": self.session_cookie} if self.session_cookie else None
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout,
                cookies=cookies,
                follow_redirects=False,
            ) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            return ProbeResult(
                status=CameraStatus.OFFLINE,
                detail={"error": type(exc).__name__, "message": str(exc)},
            )

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code in (301, 302, 303, 307, 308):
            return ProbeResult(
                status=CameraStatus.UNKNOWN,
                latency_ms=latency_ms,
                detail={
                    "reason": "redirected, likely an expired session",
                    "location": response.headers.get("location", ""),
                },
            )

        if response.status_code != 200:
            return ProbeResult(
                status=CameraStatus.OFFLINE,
                latency_ms=latency_ms,
                detail={"http_status": response.status_code},
            )

        body = response.text
        has_segments = "#EXTINF" in body
        if not has_segments:
            return ProbeResult(
                status=CameraStatus.OFFLINE,
                latency_ms=latency_ms,
                detail={"reason": "manifest returned with no segments"},
            )

        return ProbeResult(status=CameraStatus.ONLINE, latency_ms=latency_ms)
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `pytest tests/services/test_probe.py -v`
Expected: 5 passed

- [ ] **Step 5: Create `app/workers/tasks.py`**

```python
import asyncio

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.enums import StreamProtocol
from app.models.camera import Camera
from app.models.stream_endpoint import StreamEndpoint
from app.schemas.health import HealthObservationIn
from app.services.health import HealthService
from app.services.probe import HlsProbe

# Bounded so a large fleet cannot open thousands of sockets at once. At 80k cameras the
# design is a worker pool partitioned by department with staggered schedules; here we
# probe a capped sample, which is honest and demonstrable.
PROBE_CONCURRENCY = 20
PROBE_BATCH = 200


async def probe_cameras(ctx: dict) -> dict[str, int]:
    semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
    probe = HlsProbe(session_cookie=ctx.get("sentinel_cookie"))
    checked = changed = 0

    async with SessionLocal() as session:
        stmt = (
            select(Camera, StreamEndpoint)
            .join(StreamEndpoint, StreamEndpoint.camera_id == Camera.id)
            .where(
                StreamEndpoint.protocol == StreamProtocol.HLS.value,
                Camera.is_active,
                Camera.lifecycle_state == "active",
            )
            .order_by(Camera.last_seen_at.asc().nulls_first())
            .limit(PROBE_BATCH)
        )
        pairs = (await session.execute(stmt)).all()

        service = HealthService(session)

        async def run(camera: Camera, endpoint: StreamEndpoint) -> bool:
            async with semaphore:
                result = await probe.check(endpoint.url)
            outcome = await service.record(
                camera,
                HealthObservationIn(
                    status=result.status,
                    latency_ms=result.latency_ms,
                    detail=result.detail,
                ),
                source="probe",
            )
            return outcome.changed

        results = await asyncio.gather(*(run(c, e) for c, e in pairs))
        checked = len(results)
        changed = sum(results)
        await session.commit()

    return {"checked": checked, "changed": changed}


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [probe_cameras]
    cron_jobs = [cron(probe_cameras, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55})]
```

Ordering by `last_seen_at ASC NULLS FIRST` means the least recently checked cameras are
always probed first, so coverage rotates fairly across a fleet larger than one batch.

- [ ] **Step 6: Verify against the real sandbox**

Onboard the Sentinel cameras (Plan 1 Task 9), then run one probe pass by hand:

```bash
python -c "
import asyncio
from app.workers.tasks import probe_cameras
print(asyncio.run(probe_cameras({'sentinel_cookie': '<your-cookie>'})))
"
```
Expected: `{'checked': 30, 'changed': 30}` on the first run (every camera moves from
`unknown`), then `changed: 0` on the second unless a feed actually restarted.

Then: `curl -s localhost:8000/api/v1/health/summary | python -m json.tool`

- [ ] **Step 7: Commit**

```bash
git add app/services/probe.py app/workers/tasks.py tests/services/test_probe.py
git commit -m "feat: HLS probe ladder and scheduled health worker"
```

---

## Task 5: Health dashboard

**Files:**
- Create: `web/app/health/page.tsx`, `web/components/OfflineTable.tsx`

- [ ] **Step 1: Create `web/components/OfflineTable.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type OfflineCamera = {
  camera_id: string;
  camera_uid: string;
  name: string | null;
  department_code: string | null;
  downtime_seconds: number;
};

function formatDowntime(seconds: number): string {
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr`;
  return `${Math.floor(seconds / 86400)} days`;
}

function severity(seconds: number): string {
  if (seconds > 7 * 86400) return "bg-red-100 text-red-800";
  if (seconds > 86400) return "bg-amber-100 text-amber-800";
  return "bg-slate-100 text-slate-700";
}

export function OfflineTable() {
  const [rows, setRows] = useState<OfflineCamera[]>([]);

  useEffect(() => {
    const load = () =>
      fetch(`${API}/api/v1/health/offline`)
        .then((r) => r.json())
        .then((page) => setRows(page.items))
        .catch(() => setRows([]));
    load();
    const timer = setInterval(load, 30_000);
    return () => clearInterval(timer);
  }, []);

  return (
    <table className="w-full text-sm">
      <thead className="border-b text-left text-xs uppercase text-slate-500">
        <tr>
          <th className="py-2">Camera</th>
          <th>Department</th>
          <th>Location</th>
          <th className="text-right">Down for</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.camera_id} className="border-b last:border-0">
            <td className="py-2 font-mono text-xs">{row.camera_uid}</td>
            <td>{row.department_code ?? "—"}</td>
            <td className="text-slate-600">{row.name ?? "—"}</td>
            <td className="text-right">
              <span className={`rounded px-2 py-0.5 text-xs ${severity(row.downtime_seconds)}`}>
                {formatDowntime(row.downtime_seconds)}
              </span>
            </td>
          </tr>
        ))}
        {rows.length === 0 && (
          <tr>
            <td colSpan={4} className="py-6 text-center text-slate-400">
              No cameras currently offline.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 2: Create `web/app/health/page.tsx`**

```tsx
import { OfflineTable } from "@/components/OfflineTable";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Summary = {
  total: number;
  online: number;
  offline: number;
  unknown: number;
  maintenance: number;
  offline_over_24h: number;
  offline_over_7d: number;
};

async function getSummary(): Promise<Summary | null> {
  try {
    const response = await fetch(`${API}/api/v1/health/summary`, { cache: "no-store" });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

export default async function HealthPage() {
  const summary = await getSummary();

  const tiles: [string, number, string][] = summary
    ? [
        ["Total", summary.total, "text-slate-900"],
        ["Online", summary.online, "text-green-600"],
        ["Offline", summary.offline, "text-red-600"],
        ["Maintenance", summary.maintenance, "text-amber-600"],
        ["Down > 24h", summary.offline_over_24h, "text-red-700"],
        ["Down > 7d", summary.offline_over_7d, "text-red-800"],
      ]
    : [];

  return (
    <main className="mx-auto max-w-6xl p-8">
      <h1 className="mb-6 text-2xl font-semibold">Camera health</h1>

      {summary === null ? (
        <p className="mb-8 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          Registry API unreachable.
        </p>
      ) : (
        <div className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-6">
          {tiles.map(([label, value, colour]) => (
            <div key={label} className="rounded-lg border p-4">
              <p className="text-xs uppercase text-slate-500">{label}</p>
              <p className={`text-2xl font-semibold ${colour}`}>{value}</p>
            </div>
          ))}
        </div>
      )}

      <h2 className="mb-3 text-lg font-medium">Offline, longest first</h2>
      <OfflineTable />
    </main>
  );
}
```

- [ ] **Step 3: Verify manually**

Onboard the Sentinel cameras, run one probe pass, open `http://localhost:3000/health`.
Expected: 30 cameras counted, mostly online. Then confirm the transition is visible: mark
one camera offline by hand and reload.

```bash
curl -s -X POST "localhost:8000/api/v1/health/observations?department_id=<SEN_UUID>" \
  -H 'content-type: application/json' \
  -d '[{"external_camera_id":"cam04","status":"offline"}]'
```
Expected: it appears in the offline table with a downtime counter that grows on refresh.

- [ ] **Step 4: Commit**

```bash
git add web/app/health web/components/OfflineTable.tsx
git commit -m "feat: health dashboard with downtime-ranked offline table"
```

---

## Self-review against the spec

**Covered:** §7 `camera_health`, §8 health routes, §11 health monitoring in full including
the probe ladder and the fan-out design note, §14 page 8.

**Deferred:** the `camera.status_changed` webhook fires in Plan 6 — `HealthService.record`
already returns `RecordOutcome.changed`, which is the hook Plan 6 consumes, so no rework is
needed. Health sparklines in the camera drawer are wired in Plan 5 when the detail payload
is assembled.

**Accepted corner:** probing is capped at 200 cameras per five-minute pass rather than
covering the full fleet. At 80k cameras that is a 33-hour rotation, which is why the spec
documents partitioned worker pools as the scale path. State this openly in the HLD — the
rotation-by-staleness ordering makes it defensible rather than broken.
