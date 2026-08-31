import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon

from app.core.enums import CameraStatus, CameraType
from app.models.admin_boundary import AdminBoundary
from app.models.camera import Camera
from app.repositories.camera import CameraRepository
from app.schemas.filters import CameraFilter


@pytest.fixture
async def cameras(session, seeded_department):
    rows = [
        ("GJ-AMC-000001", "A-1", 72.5714, 23.0225, CameraType.FIXED, CameraStatus.ONLINE),
        ("GJ-AMC-000002", "A-2", 72.5800, 23.0300, CameraType.PTZ, CameraStatus.OFFLINE),
        ("GJ-AMC-000003", "A-3", 70.8000, 22.3000, CameraType.FIXED, CameraStatus.ONLINE),
    ]
    for uid, ext, lon, lat, kind, status in rows:
        session.add(
            Camera(
                camera_uid=uid,
                department_id=seeded_department,
                external_camera_id=ext,
                location=f"SRID=4326;POINT({lon} {lat})",
                camera_type=kind,
                current_status=status,
                name=f"Junction {ext}",
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_status_filter(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(CameraFilter(statuses=[CameraStatus.OFFLINE]))
    assert [c.camera_uid for c in found] == ["GJ-AMC-000002"]


@pytest.mark.asyncio
async def test_type_filter(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(CameraFilter(camera_types=[CameraType.FIXED]))
    assert len(found) == 2


@pytest.mark.asyncio
async def test_radius_search_uses_real_metres(session, cameras):
    repo = CameraRepository(session)
    # A-1 and A-2 are ~1 km apart; A-3 is in Rajkot, ~200 km away.
    found = await repo.list(
        CameraFilter(near_lat=23.0225, near_lon=72.5714, radius_m=2000)
    )
    assert {c.camera_uid for c in found} == {"GJ-AMC-000001", "GJ-AMC-000002"}


@pytest.mark.asyncio
async def test_tight_radius_excludes_the_neighbour(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(
        CameraFilter(near_lat=23.0225, near_lon=72.5714, radius_m=100)
    )
    assert {c.camera_uid for c in found} == {"GJ-AMC-000001"}


@pytest.mark.asyncio
async def test_free_text_matches_name_case_insensitively(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(CameraFilter(q="junction a-3"))
    assert [c.camera_uid for c in found] == ["GJ-AMC-000003"]


@pytest.mark.asyncio
async def test_combined_filters_intersect(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(
        CameraFilter(
            camera_types=[CameraType.FIXED],
            near_lat=23.0225,
            near_lon=72.5714,
            radius_m=2000,
        )
    )
    assert [c.camera_uid for c in found] == ["GJ-AMC-000001"]


@pytest.fixture
async def ahmedabad(session):
    """A box around A-1 and A-2 that excludes A-3 in Rajkot."""
    box = MultiPolygon([Polygon([(72.4, 22.9), (72.7, 22.9), (72.7, 23.2), (72.4, 23.2)])])
    boundary = AdminBoundary(level="district", name="Ahmedabad", geom=from_shape(box, srid=4326))
    session.add(boundary)
    await session.commit()
    return boundary


@pytest.mark.asyncio
async def test_district_filter_selects_only_cameras_inside_the_polygon(
    session, cameras, ahmedabad
):
    """The Task 2 boundary reaching the Task 3 filter -- "which cameras in this
    district", answered through the same object the map and the CSV use."""
    repo = CameraRepository(session)
    found = await repo.list(CameraFilter(district_id=ahmedabad.id))
    assert {c.camera_uid for c in found} == {"GJ-AMC-000001", "GJ-AMC-000002"}


@pytest.mark.asyncio
async def test_count_agrees_with_list_for_the_same_filter(session, cameras):
    """The Page envelope's `total` and its `items` must not disagree: they are the
    number the table shows and the rows the map draws."""
    repo = CameraRepository(session)
    filters = CameraFilter(camera_types=[CameraType.FIXED])
    assert await repo.count(filters) == len(await repo.list(filters))


@pytest.mark.asyncio
async def test_a_half_specified_radius_is_rejected(session):
    """Silently dropping the predicate would return the whole registry and look like
    a legitimate answer to a proximity question."""
    with pytest.raises(ValueError, match="must be supplied together"):
        CameraFilter(near_lat=23.0225, radius_m=2000)
