import math

import pytest
from sqlalchemy import text


async def area_m2(session, sql: str, params: dict) -> float:
    result = await session.execute(text(f"SELECT ST_Area({sql})"), params)
    return result.scalar_one()


@pytest.mark.asyncio
async def test_ptz_footprint_is_a_full_circle(session):
    area = await area_m2(
        session,
        "camera_footprint(ST_GeogFromText(:p), 'ptz', NULL, NULL, 250)",
        {"p": "POINT(72.5714 23.0225)"},
    )
    expected = math.pi * 250**2
    assert abs(area - expected) / expected < 0.02


@pytest.mark.asyncio
async def test_fixed_camera_with_90_degree_fov_is_a_quarter_circle(session):
    area = await area_m2(
        session,
        "camera_footprint(ST_GeogFromText(:p), 'fixed', 90, 90, 100)",
        {"p": "POINT(72.5714 23.0225)"},
    )
    expected = math.pi * 100**2 / 4
    assert abs(area - expected) / expected < 0.03


@pytest.mark.asyncio
async def test_fixed_camera_without_azimuth_falls_back_to_a_circle(session):
    area = await area_m2(
        session,
        "camera_footprint(ST_GeogFromText(:p), 'fixed', NULL, 90, 100)",
        {"p": "POINT(72.5714 23.0225)"},
    )
    expected = math.pi * 100**2
    assert abs(area - expected) / expected < 0.02


@pytest.mark.asyncio
async def test_sector_points_in_the_direction_of_its_azimuth(session):
    # Azimuth 0 is due north, so the sector centroid must sit north of the apex.
    result = await session.execute(
        text(
            """
            SELECT ST_Y(ST_Centroid(camera_footprint(
                ST_GeogFromText('POINT(72.5714 23.0225)'), 'fixed', 0, 60, 200
            )::geometry)) AS lat
            """
        )
    )
    assert result.scalar_one() > 23.0225


@pytest.mark.asyncio
async def test_east_facing_sector_extends_east(session):
    result = await session.execute(
        text(
            """
            SELECT ST_X(ST_Centroid(camera_footprint(
                ST_GeogFromText('POINT(72.5714 23.0225)'), 'fixed', 90, 60, 200
            )::geometry)) AS lon
            """
        )
    )
    assert result.scalar_one() > 72.5714


@pytest.mark.asyncio
async def test_null_range_uses_the_type_default(session):
    fixed = await area_m2(
        session,
        "camera_footprint(ST_GeogFromText(:p), 'fixed', NULL, NULL, NULL)",
        {"p": "POINT(72.5714 23.0225)"},
    )
    ptz = await area_m2(
        session,
        "camera_footprint(ST_GeogFromText(:p), 'ptz', NULL, NULL, NULL)",
        {"p": "POINT(72.5714 23.0225)"},
    )
    # Defaults are 100 m for fixed and 250 m for PTZ, so PTZ covers ~6.25x the area.
    assert ptz / fixed > 5
