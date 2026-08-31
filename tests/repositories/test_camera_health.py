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
