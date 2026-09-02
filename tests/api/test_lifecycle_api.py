"""The ageing report over HTTP."""

from datetime import date, timedelta

import pytest

from app.models.camera import Camera
from app.models.department import Department

AS_OF = "2026-09-02"


@pytest.fixture
async def ageing_fleet(session):
    department = Department(code="AGE", name="Ageing Dept")
    session.add(department)
    await session.flush()
    session.add_all([
        Camera(
            camera_uid=f"GJ-AGE-{i:06d}", department_id=department.id,
            external_camera_id=f"c{i}", name=f"Camera {i}",
            location="SRID=4326;POINT(72.5 23.0)",
            install_date=date(2016, 1, 1) if i < 2 else date(2025, 1, 1),
            amc_expiry_date=date(2026, 1, 1) if i == 0 else date(2028, 1, 1),
            retention_days=7 if i == 1 else 90,
        )
        for i in range(4)
    ])
    await session.commit()
    return department


@pytest.mark.asyncio
async def test_the_report_returns_totals_bands_and_departments(api_client, ageing_fleet):
    response = await api_client.get(f"/api/v1/lifecycle/ageing?as_of={AS_OF}")
    assert response.status_code == 200
    body = response.json()
    assert body["totals"]["cameras"] == 4
    assert body["totals"]["past_service_life"] == 2
    assert body["totals"]["amc_expired"] == 1
    assert body["totals"]["retention_below_policy"] == 1
    assert len(body["bands"]) == 5
    assert body["departments"][0]["department_code"] == "AGE"


@pytest.mark.asyncio
async def test_needs_attention_does_not_double_count(api_client, ageing_fleet):
    body = (await api_client.get(f"/api/v1/lifecycle/ageing?as_of={AS_OF}")).json()
    assert body["totals"]["needs_attention"] == 2


@pytest.mark.asyncio
async def test_thresholds_are_echoed_so_a_report_is_self_describing(api_client, ageing_fleet):
    """A printed report has to say what "ageing" meant when it was produced."""
    body = (await api_client.get(
        f"/api/v1/lifecycle/ageing?as_of={AS_OF}&service_life_years=12"
    )).json()
    assert body["thresholds"]["service_life_years"] == 12
    assert body["totals"]["past_service_life"] == 0


@pytest.mark.asyncio
async def test_an_empty_registry_returns_a_report_not_an_error(api_client):
    body = (await api_client.get(f"/api/v1/lifecycle/ageing?as_of={AS_OF}")).json()
    assert body["totals"]["cameras"] == 0
    assert body["departments"] == []


@pytest.mark.parametrize("query,status", [
    ("service_life_years=0", 422),
    ("service_life_years=99", 422),
    ("amc_horizon_days=-1", 422),
    ("min_retention_days=-5", 422),
    ("as_of=not-a-date", 422),
    ("service_life_years=1", 200),
    ("amc_horizon_days=0", 200),
])
@pytest.mark.asyncio
async def test_parameters_are_validated(api_client, query, status):
    assert (await api_client.get(f"/api/v1/lifecycle/ageing?{query}")).status_code == status


@pytest.mark.asyncio
async def test_the_csv_export_carries_a_header_and_a_row_per_department(
    api_client, ageing_fleet
):
    response = await api_client.get(f"/api/v1/lifecycle/ageing.csv?as_of={AS_OF}")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].lstrip("﻿").startswith("department_code")
    assert len(lines) == 2


@pytest.mark.asyncio
async def test_the_csv_opens_correctly_in_excel(api_client, ageing_fleet):
    """A BOM, for the same reason the importer strips one."""
    response = await api_client.get(f"/api/v1/lifecycle/ageing.csv?as_of={AS_OF}")
    assert response.content.startswith(b"\xef\xbb\xbf")


@pytest.mark.asyncio
async def test_the_report_requires_a_read_scope(session, ageing_fleet):
    from tests.api.test_rbac import client_for, headers_for, make_user

    nobody = await make_user(session, "viewer")
    async with await client_for(session, headers_for(nobody)) as client:
        assert (await client.get("/api/v1/lifecycle/ageing")).status_code == 200


@pytest.mark.asyncio
async def test_the_csv_export_requires_the_export_scope(session, ageing_fleet):
    """Same rule as the camera export: bulk extraction is its own permission."""
    from tests.api.test_rbac import client_for, headers_for, make_user

    viewer = await make_user(session, "viewer")
    async with await client_for(session, headers_for(viewer)) as client:
        assert (await client.get("/api/v1/lifecycle/ageing.csv")).status_code == 403
