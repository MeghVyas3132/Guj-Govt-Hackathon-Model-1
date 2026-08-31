"""Spatial containment: which cameras fall inside a district polygon.

The polygons here are synthetic boxes, not the real Gujarat shapes. That is
deliberate -- the assertions are about the PostGIS predicate wiring, and a test
that depended on `data/gujarat_districts.geojson` would fail on a checkout that
has not run the seed.
"""

import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select

from app.models.admin_boundary import AdminBoundary
from app.models.camera import Camera


@pytest.fixture
async def ahmedabad(session):
    box = MultiPolygon([Polygon([(72.4, 22.9), (72.7, 22.9), (72.7, 23.2), (72.4, 23.2)])])
    boundary = AdminBoundary(level="district", name="Ahmedabad", geom=from_shape(box, srid=4326))
    session.add(boundary)
    await session.commit()
    return boundary


@pytest.mark.asyncio
async def test_point_inside_the_district_is_matched(session, ahmedabad, seeded_department):
    session.add(
        Camera(
            camera_uid="GJ-AMC-000001",
            department_id=seeded_department,
            external_camera_id="A-1",
            location="SRID=4326;POINT(72.5714 23.0225)",
        )
    )
    await session.commit()

    stmt = (
        select(func.count())
        .select_from(Camera)
        .join(AdminBoundary, func.ST_Intersects(Camera.location, AdminBoundary.geom))
        .where(AdminBoundary.id == ahmedabad.id)
    )
    assert (await session.execute(stmt)).scalar_one() == 1


@pytest.mark.asyncio
async def test_point_outside_the_district_is_not_matched(
    session, ahmedabad, seeded_department
):
    session.add(
        Camera(
            camera_uid="GJ-AMC-000002",
            department_id=seeded_department,
            external_camera_id="A-2",
            location="SRID=4326;POINT(70.8 22.3)",  # Rajkot
        )
    )
    await session.commit()

    stmt = (
        select(func.count())
        .select_from(Camera)
        .join(AdminBoundary, func.ST_Intersects(Camera.location, AdminBoundary.geom))
        .where(AdminBoundary.id == ahmedabad.id)
    )
    assert (await session.execute(stmt)).scalar_one() == 0
