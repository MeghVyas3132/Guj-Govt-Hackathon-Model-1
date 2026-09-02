"""Ageing-infrastructure reporting.

Boundary dates are the whole risk here: a camera installed exactly five years
ago either is or is not past its service life, and a report that flickers across
that line between two runs on the same day is worthless for procurement.
"""

from datetime import date, timedelta

import pytest

from app.models.camera import Camera
from app.models.department import Department
from app.services.lifecycle import LifecycleService

AS_OF = date(2026, 9, 2)
POINT = "SRID=4326;POINT(72.5 23.0)"


@pytest.fixture
async def dept(session):
    department = Department(code="AGE", name="Ageing Dept")
    session.add(department)
    await session.commit()
    return department


async def add(session, dept, uid, **kw):
    camera = Camera(
        camera_uid=uid, department_id=dept.id, external_camera_id=uid,
        name=uid, location=POINT, **kw,
    )
    session.add(camera)
    await session.commit()
    return camera


def years_ago(n: float) -> date:
    return AS_OF - timedelta(days=int(n * 365.25))


@pytest.mark.asyncio
async def test_an_empty_registry_reports_zeroes_not_a_crash(session):
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert report.total_cameras == 0
    assert report.needs_attention == 0
    assert all(b.share == 0.0 for b in report.bands)


@pytest.mark.asyncio
async def test_a_camera_past_its_service_life_is_counted(session, dept):
    await add(session, dept, "old", install_date=years_ago(7))
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert report.past_service_life == 1


@pytest.mark.asyncio
async def test_a_new_camera_is_not_counted(session, dept):
    await add(session, dept, "new", install_date=years_ago(1))
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert report.past_service_life == 0


@pytest.mark.asyncio
async def test_the_service_life_boundary_is_inclusive_and_stable(session, dept):
    """Exactly at the cutoff counts as past. Stated explicitly because the
    alternative is a camera that changes category depending on rounding."""
    await add(session, dept, "edge", install_date=years_ago(5))
    report = await LifecycleService(session).ageing(as_of=AS_OF, service_life_years=5)
    assert report.past_service_life == 1


@pytest.mark.asyncio
async def test_the_service_life_threshold_is_a_parameter(session, dept):
    await add(session, dept, "c", install_date=years_ago(7))
    service = LifecycleService(session)
    assert (await service.ageing(as_of=AS_OF, service_life_years=5)).past_service_life == 1
    assert (await service.ageing(as_of=AS_OF, service_life_years=10)).past_service_life == 0


@pytest.mark.asyncio
async def test_an_expired_amc_is_counted(session, dept):
    await add(session, dept, "c", amc_expiry_date=AS_OF - timedelta(days=1))
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert (report.amc_expired, report.amc_expiring_soon) == (1, 0)


@pytest.mark.asyncio
async def test_an_amc_expiring_today_is_expiring_soon_not_expired(session, dept):
    """A contract that runs to the end of today has not lapsed yet."""
    await add(session, dept, "c", amc_expiry_date=AS_OF)
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert (report.amc_expired, report.amc_expiring_soon) == (0, 1)


@pytest.mark.asyncio
async def test_an_amc_beyond_the_horizon_is_not_flagged(session, dept):
    await add(session, dept, "c", amc_expiry_date=AS_OF + timedelta(days=200))
    report = await LifecycleService(session).ageing(as_of=AS_OF, amc_horizon_days=90)
    assert report.amc_expiring_soon == 0


@pytest.mark.asyncio
async def test_the_amc_horizon_is_a_parameter(session, dept):
    await add(session, dept, "c", amc_expiry_date=AS_OF + timedelta(days=120))
    service = LifecycleService(session)
    assert (await service.ageing(as_of=AS_OF, amc_horizon_days=90)).amc_expiring_soon == 0
    assert (await service.ageing(as_of=AS_OF, amc_horizon_days=180)).amc_expiring_soon == 1


@pytest.mark.asyncio
async def test_retention_below_policy_is_counted(session, dept):
    await add(session, dept, "c", retention_days=7)
    report = await LifecycleService(session).ageing(as_of=AS_OF, min_retention_days=30)
    assert report.retention_below_policy == 1


@pytest.mark.asyncio
async def test_retention_exactly_at_policy_is_compliant(session, dept):
    await add(session, dept, "c", retention_days=30)
    report = await LifecycleService(session).ageing(as_of=AS_OF, min_retention_days=30)
    assert report.retention_below_policy == 0


@pytest.mark.asyncio
async def test_an_unknown_retention_is_not_counted_as_a_breach(session, dept):
    """Absent is not the same as non-compliant. Counting nulls as breaches would
    make the report a measure of data entry rather than of infrastructure."""
    await add(session, dept, "c", retention_days=None)
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert report.retention_below_policy == 0


@pytest.mark.asyncio
async def test_an_unknown_install_date_is_reported_separately(session, dept):
    """These are the cameras nobody can plan around, which is itself the finding."""
    await add(session, dept, "c", install_date=None)
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert (report.unknown_install_date, report.past_service_life) == (1, 0)


@pytest.mark.asyncio
async def test_needs_attention_counts_each_camera_once(session, dept):
    """A camera both past its service life and out of AMC is one replacement, not
    two. Summing the columns would overstate the budget ask."""
    await add(
        session, dept, "both",
        install_date=years_ago(9), amc_expiry_date=AS_OF - timedelta(days=30),
        retention_days=3,
    )
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert report.past_service_life == 1 and report.amc_expired == 1
    assert report.needs_attention == 1


@pytest.mark.asyncio
async def test_a_healthy_camera_needs_no_attention(session, dept):
    await add(
        session, dept, "fine", install_date=years_ago(1),
        amc_expiry_date=AS_OF + timedelta(days=500), retention_days=90,
    )
    assert (await LifecycleService(session).ageing(as_of=AS_OF)).needs_attention == 0


@pytest.mark.asyncio
async def test_an_inactive_camera_is_excluded(session, dept):
    """Decommissioned stock is not a replacement liability."""
    await add(session, dept, "gone", install_date=years_ago(9), is_active=False)
    assert (await LifecycleService(session).ageing(as_of=AS_OF)).total_cameras == 0


@pytest.mark.asyncio
async def test_bands_partition_every_dated_camera_exactly_once(session, dept):
    """The bands are a breakdown, so they must sum to the dated population --
    no camera in two bands, none in none."""
    for i, age in enumerate([0.5, 2, 4, 6, 12]):
        await add(session, dept, f"c{i}", install_date=years_ago(age))
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert sum(b.count for b in report.bands) == 5
    assert [b.count for b in report.bands] == [1, 1, 1, 1, 1]


@pytest.mark.asyncio
async def test_undated_cameras_are_absent_from_the_bands(session, dept):
    await add(session, dept, "dated", install_date=years_ago(2))
    await add(session, dept, "undated", install_date=None)
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert sum(b.count for b in report.bands) == 1
    assert report.total_cameras == 2


@pytest.mark.asyncio
async def test_band_shares_are_of_the_whole_fleet(session, dept):
    for i in range(4):
        await add(session, dept, f"c{i}", install_date=years_ago(2))
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert next(b for b in report.bands if b.label == "1-3 years").share == 100.0


@pytest.mark.asyncio
async def test_departments_are_ordered_worst_first(session):
    """The table is a worklist, so the department with the most ageing stock is
    the one an officer should read first."""
    light = Department(code="LGT", name="Light")
    heavy = Department(code="HVY", name="Heavy")
    session.add_all([light, heavy])
    await session.commit()
    await add(session, light, "l1", install_date=years_ago(9))
    for i in range(3):
        await add(session, heavy, f"h{i}", install_date=years_ago(9))

    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert [d.department_code for d in report.departments] == ["HVY", "LGT"]


@pytest.mark.asyncio
async def test_the_oldest_install_date_is_reported_per_department(session, dept):
    await add(session, dept, "a", install_date=date(2015, 3, 1))
    await add(session, dept, "b", install_date=date(2020, 3, 1))
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert report.departments[0].oldest_install_date == date(2015, 3, 1)


@pytest.mark.asyncio
async def test_a_department_filter_narrows_every_section(session):
    a = Department(code="AAA", name="A")
    b = Department(code="BBB", name="B")
    session.add_all([a, b])
    await session.commit()
    await add(session, a, "a1", install_date=years_ago(9))
    await add(session, b, "b1", install_date=years_ago(9))

    report = await LifecycleService(session).ageing(as_of=AS_OF, department_id=str(a.id))
    assert report.total_cameras == 1
    assert [d.department_code for d in report.departments] == ["AAA"]


@pytest.mark.asyncio
async def test_department_totals_reconcile_with_the_fleet_total(session):
    a = Department(code="AAA", name="A")
    b = Department(code="BBB", name="B")
    session.add_all([a, b])
    await session.commit()
    for i in range(3):
        await add(session, a, f"a{i}", install_date=years_ago(6))
    await add(session, b, "b0", install_date=years_ago(1))

    report = await LifecycleService(session).ageing(as_of=AS_OF)
    assert sum(d.total for d in report.departments) == report.total_cameras
    assert sum(d.past_service_life for d in report.departments) == report.past_service_life


@pytest.mark.asyncio
async def test_the_report_is_reproducible_for_a_past_date(session, dept):
    """as_of exists so a report attached to a budget paper can be regenerated
    later and still say the same thing."""
    await add(session, dept, "c", install_date=date(2019, 1, 1))
    service = LifecycleService(session)
    assert (await service.ageing(as_of=date(2022, 1, 1))).past_service_life == 0
    assert (await service.ageing(as_of=date(2026, 1, 1))).past_service_life == 1


@pytest.mark.asyncio
async def test_the_serialised_form_is_json_safe(session, dept):
    import json

    await add(session, dept, "c", install_date=years_ago(9))
    report = await LifecycleService(session).ageing(as_of=AS_OF)
    body = json.loads(json.dumps(report.to_dict()))
    assert body["thresholds"]["service_life_years"] == 5
    assert body["departments"][0]["oldest_install_date"].startswith("20")
