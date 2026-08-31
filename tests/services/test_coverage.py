import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select

from app.models.admin_boundary import AdminBoundary
from app.models.camera import Camera
from app.models.coverage import CoverageCell
from app.schemas.coverage import CoverageRunRequest
from app.services.coverage import CoverageService


@pytest.fixture
async def small_district(session):
    # ~1.1 km x 1.1 km near Ahmedabad. Deliberately tiny: at a 150 m edge this
    # tessellates to a few dozen hexagons, where a real district is six figures.
    # Running the engine over a real district is a manual check, not a unit test.
    box = MultiPolygon(
        [Polygon([(72.570, 23.020), (72.580, 23.020), (72.580, 23.030), (72.570, 23.030)])]
    )
    boundary = AdminBoundary(
        level="district", name="Testville", geom=from_shape(box, srid=4326)
    )
    session.add(boundary)
    await session.commit()
    return boundary


async def add_camera(session, dept_id, uid, lon, lat, *, status="online", kind="ptz"):
    session.add(
        Camera(
            camera_uid=uid,
            department_id=dept_id,
            external_camera_id=uid,
            location=f"SRID=4326;POINT({lon} {lat})",
            camera_type=kind,
            range_m=250,
            current_status=status,
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_district_with_no_cameras_is_zero_percent(
    session, small_district, seeded_department
):
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    assert run.status == "done"
    assert run.total_cells > 0
    assert run.installed_coverage_pct == 0.0
    assert run.camera_count == 0


@pytest.mark.asyncio
async def test_the_test_aoi_tessellates_to_a_sane_number_of_cells(
    session, small_district, seeded_department
):
    """Guards the metres-to-degrees conversion.

    ST_HexagonGrid's edge argument is in the units of the input geometry, and the
    boundary is stored in degrees. Passing 150 straight through would ask for
    hexagons 150 degrees across; dividing by the wrong constant by a factor of a
    thousand would ask for millions of cells and hang. A ~1.2 km^2 box at a 150 m
    edge is about 20 hexagons of 58,000 m^2 each, plus a fringe of clipped ones.
    """
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    assert 10 < run.total_cells < 100


@pytest.mark.asyncio
async def test_one_ptz_camera_produces_partial_coverage(
    session, small_district, seeded_department
):
    await add_camera(session, seeded_department, "C-1", 72.575, 23.025)
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    assert 0 < run.installed_coverage_pct < 100
    assert run.camera_count == 1


@pytest.mark.asyncio
async def test_offline_camera_counts_for_installed_but_not_effective(
    session, small_district, seeded_department
):
    """The differentiator: the delta between the two figures is coverage lost to
    outages, which is the number that says "restoring these cameras recovers this
    much coverage without buying anything"."""
    await add_camera(session, seeded_department, "C-1", 72.575, 23.025, status="offline")
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    assert run.installed_coverage_pct > 0
    assert run.effective_coverage_pct == 0.0
    assert run.camera_count == 1
    assert run.online_camera_count == 0


@pytest.mark.asyncio
async def test_bringing_the_camera_online_closes_the_outage_delta(
    session, small_district, seeded_department
):
    """The paired half of the test above, so neither can pass vacuously.

    If effective coverage were hardwired to zero -- or installed and effective were
    accidentally computed from the same camera set -- one of these two tests fails.
    """
    await add_camera(session, seeded_department, "C-1", 72.575, 23.025, status="online")
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    assert run.installed_coverage_pct > 0
    assert run.effective_coverage_pct == run.installed_coverage_pct
    assert run.online_camera_count == 1


@pytest.mark.asyncio
async def test_cells_are_classified_into_three_bands(
    session, small_district, seeded_department
):
    await add_camera(session, seeded_department, "C-1", 72.575, 23.025)
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    classes = (
        (
            await session.execute(
                select(CoverageCell.classification)
                .where(CoverageCell.run_id == run.id)
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    assert set(classes) <= {"covered", "partial", "gap"}
    assert "gap" in classes  # corners of the district are unreachable from one camera


@pytest.mark.asyncio
async def test_fixed_camera_without_azimuth_is_counted_as_assumed_omnidirectional(
    session, small_district, seeded_department
):
    await add_camera(session, seeded_department, "C-1", 72.575, 23.025, kind="fixed")
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    assert run.assumed_omnidirectional_count == 1


@pytest.mark.asyncio
async def test_cell_count_matches_persisted_rows(
    session, small_district, seeded_department
):
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    stored = (
        await session.execute(
            select(func.count()).select_from(CoverageCell).where(CoverageCell.run_id == run.id)
        )
    ).scalar_one()
    assert stored == run.total_cells


@pytest.mark.asyncio
async def test_a_camera_just_outside_the_boundary_still_contributes(
    session, small_district, seeded_department
):
    """The camera set is drawn with a 2 km buffer beyond the AOI on purpose: a camera
    across the district line still sees the coverage it genuinely provides inside it.
    This one sits ~200 m west of the western edge with a 250 m range."""
    await add_camera(session, seeded_department, "C-OUT", 72.5680, 23.025)
    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    assert run.camera_count == 1
    assert run.installed_coverage_pct > 0


@pytest.mark.asyncio
async def test_decommissioned_cameras_are_excluded(
    session, small_district, seeded_department
):
    await add_camera(session, seeded_department, "C-1", 72.575, 23.025)
    camera = (await session.execute(select(Camera))).scalar_one()
    camera.lifecycle_state = "decommissioned"
    await session.commit()

    run = await CoverageService(session).run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    assert run.camera_count == 0
    assert run.installed_coverage_pct == 0.0


@pytest.mark.asyncio
async def test_an_unknown_boundary_is_rejected(session):
    from uuid import UUID

    with pytest.raises(ValueError, match="not found"):
        await CoverageService(session).run(
            CoverageRunRequest(
                boundary_id=UUID("00000000-0000-0000-0000-000000000000")
            )
        )


@pytest.mark.asyncio
async def test_worst_cells_returns_the_emptiest_gaps_first(
    session, small_district, seeded_department
):
    await add_camera(session, seeded_department, "C-1", 72.575, 23.025)
    service = CoverageService(session)
    run = await service.run(
        CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
    )
    worst = await service.worst_cells(run.id, limit=5)
    assert worst
    fractions = [cell["installed_fraction"] for cell in worst]
    assert fractions == sorted(fractions)
    assert all(cell["installed_fraction"] < 0.20 for cell in worst)


@pytest.mark.asyncio
async def test_a_failed_run_is_recorded_rather_than_vanishing(
    session, small_district, seeded_department, monkeypatch
):
    """A failing compute statement poisons the transaction, so recording the failure
    needs a rollback first. Without one this path raises a second, misleading error
    instead of the real one, and no run row survives to explain what happened."""
    from sqlalchemy import text as sql_text

    from app.models.coverage import CoverageRun

    monkeypatch.setattr(
        "app.services.coverage._COMPUTE", sql_text("SELECT no_such_function()")
    )
    with pytest.raises(Exception, match="no_such_function"):
        await CoverageService(session).run(
            CoverageRunRequest(boundary_id=small_district.id, hex_edge_m=150)
        )

    run = (await session.execute(select(CoverageRun))).scalar_one()
    assert run.status == "failed"
    assert "no_such_function" in run.error
    assert run.finished_at is not None
