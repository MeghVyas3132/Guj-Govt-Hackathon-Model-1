import pytest
from sqlalchemy import select

from app.api.v1.routers.cameras import _to_read
from app.models.camera import Camera


@pytest.mark.asyncio
async def test_serialises_a_camera_the_caller_still_holds(session, seeded_department):
    """Any code path that creates a camera and serialises it in the same session
    holds a strong reference, so location is still the assigned WKT string rather
    than the WKBElement a fresh load returns."""
    camera = Camera(
        camera_uid="GJ-AMC-000042",
        department_id=seeded_department,
        external_camera_id="A-42",
        location="SRID=4326;POINT(72.5714 23.0225)",
    )
    session.add(camera)
    await session.commit()

    held = (await session.execute(select(Camera))).scalar_one()  # strong reference
    result = _to_read(held)

    assert round(result.latitude, 4) == 23.0225
