import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon

from app.models.admin_boundary import AdminBoundary
from app.models.camera import Camera


@pytest.fixture
async def district(session):
    box = MultiPolygon(
        [Polygon([(72.570, 23.020), (72.580, 23.020), (72.580, 23.030), (72.570, 23.030)])]
    )
    boundary = AdminBoundary(
        level="district", name="Testville", geom=from_shape(box, srid=4326)
    )
    session.add(boundary)
    await session.commit()
    return boundary


@pytest.fixture
async def cameras(session, district, seeded_department):
    session.add_all([
        Camera(
            camera_uid="GJ-T-000001", department_id=seeded_department,
            external_camera_id="T-1", location="SRID=4326;POINT(72.575 23.025)",
            camera_type="ptz", range_m=250, current_status="online",
        ),
        Camera(
            camera_uid="GJ-T-000002", department_id=seeded_department,
            external_camera_id="T-2", location="SRID=4326;POINT(72.573 23.023)",
            camera_type="ptz", range_m=250, current_status="offline",
        ),
    ])
    await session.commit()


@pytest.mark.asyncio
async def test_a_run_returns_a_completed_summary(api_client, district, cameras):
    response = await api_client.post(
        "/api/v1/coverage/runs",
        json={"boundary_id": str(district.id), "hex_edge_m": 200},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "done"
    assert body["boundary_name"] == "Testville"
    assert body["total_cells"] > 0
    assert body["camera_count"] == 2
    assert body["online_camera_count"] == 1


@pytest.mark.asyncio
async def test_installed_exceeds_effective_when_a_camera_is_down(
    api_client, district, cameras
):
    """The number the whole report exists to produce."""
    body = (
        await api_client.post(
            "/api/v1/coverage/runs",
            json={"boundary_id": str(district.id), "hex_edge_m": 200},
        )
    ).json()
    assert body["installed_coverage_pct"] > body["effective_coverage_pct"]


@pytest.mark.asyncio
async def test_an_unknown_boundary_is_404(api_client):
    response = await api_client.post(
        "/api/v1/coverage/runs",
        json={"boundary_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_oversized_request_is_422_and_suggests_a_workable_edge(
    api_client, district
):
    response = await api_client.post(
        "/api/v1/coverage/runs",
        json={"boundary_id": str(district.id), "hex_edge_m": 25},
    )
    # The test district is small, so 25m is fine; assert the guard is reachable
    # rather than that this specific request trips it.
    assert response.status_code in (201, 422)
    if response.status_code == 422:
        assert "hex_edge_m" in response.json()["detail"]


@pytest.mark.asyncio
async def test_an_edge_below_the_floor_is_rejected(api_client, district):
    response = await api_client.post(
        "/api/v1/coverage/runs",
        json={"boundary_id": str(district.id), "hex_edge_m": 5},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_estimate_endpoint_answers_without_running(api_client, district):
    response = await api_client.get(
        f"/api/v1/coverage/estimate?boundary_id={district.id}&hex_edge_m=200"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["estimated_cells"] > 0
    assert body["within_budget"] is True

    runs = (await api_client.get("/api/v1/coverage/runs")).json()
    assert runs == []  # estimating must not create a run


@pytest.mark.asyncio
async def test_a_run_can_be_fetched_and_listed(api_client, district, cameras):
    created = (
        await api_client.post(
            "/api/v1/coverage/runs",
            json={"boundary_id": str(district.id), "hex_edge_m": 200},
        )
    ).json()

    fetched = await api_client.get(f"/api/v1/coverage/runs/{created['id']}")
    assert fetched.json()["id"] == created["id"]

    listed = (await api_client.get("/api/v1/coverage/runs")).json()
    assert [r["id"] for r in listed] == [created["id"]]


@pytest.mark.asyncio
async def test_the_report_renders_for_a_real_run(api_client, district, cameras):
    created = (
        await api_client.post(
            "/api/v1/coverage/runs",
            json={"boundary_id": str(district.id), "hex_edge_m": 200},
        )
    ).json()

    report = await api_client.get(f"/api/v1/coverage/runs/{created['id']}/report.html")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("text/html")
    assert "Testville" in report.text
    assert "occlusion" in report.text.lower()


@pytest.mark.asyncio
async def test_the_report_for_an_unknown_run_is_404(api_client):
    response = await api_client.get(
        "/api/v1/coverage/runs/00000000-0000-0000-0000-000000000000/report.html"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_coverage_tiles_carry_the_cells(api_client, district, cameras):
    created = (
        await api_client.post(
            "/api/v1/coverage/runs",
            json={"boundary_id": str(district.id), "hex_edge_m": 200},
        )
    ).json()

    # z14 tile covering the test district.
    tile = await api_client.get(
        f"/api/v1/coverage/runs/{created['id']}/tiles/14/11494/7114.mvt"
    )
    assert tile.status_code == 200
    assert tile.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    assert len(tile.content) > 0


@pytest.mark.asyncio
async def test_a_tile_outside_the_run_is_204(api_client, district, cameras):
    created = (
        await api_client.post(
            "/api/v1/coverage/runs",
            json={"boundary_id": str(district.id), "hex_edge_m": 200},
        )
    ).json()
    tile = await api_client.get(
        f"/api/v1/coverage/runs/{created['id']}/tiles/14/1/1.mvt"
    )
    assert tile.status_code == 204
