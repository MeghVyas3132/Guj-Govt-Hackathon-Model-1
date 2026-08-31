import pytest
from geoalchemy2.shape import to_shape
from sqlalchemy import select

from app.models.camera import Camera
from app.models.department import Department


@pytest.mark.asyncio
async def test_camera_round_trips_with_geography(session):
    dept = Department(code="POL", name="Gujarat Police")
    session.add(dept)
    await session.flush()

    session.add(
        Camera(
            camera_uid="GJ-POL-000001",
            department_id=dept.id,
            external_camera_id="cam04",
            location="SRID=4326;POINT(72.5714 23.0225)",
            metadata_={"vendor_note": "installed under VISWAS phase II"},
        )
    )
    await session.commit()

    camera = (await session.execute(select(Camera))).scalar_one()
    point = to_shape(camera.location)
    assert round(point.x, 4) == 72.5714
    assert round(point.y, 4) == 23.0225
    assert camera.metadata_["vendor_note"].startswith("installed under")


@pytest.mark.asyncio
async def test_duplicate_external_id_in_same_department_is_rejected(session):
    from sqlalchemy.exc import IntegrityError

    dept = Department(code="AMC", name="Ahmedabad Municipal Corporation")
    session.add(dept)
    await session.flush()

    for uid in ("GJ-AMC-000001", "GJ-AMC-000002"):
        session.add(
            Camera(
                camera_uid=uid,
                department_id=dept.id,
                external_camera_id="DUP-1",
                location="SRID=4326;POINT(72.5 23.0)",
            )
        )

    with pytest.raises(IntegrityError):
        await session.commit()
