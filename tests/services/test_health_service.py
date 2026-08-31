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
