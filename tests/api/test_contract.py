import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client(super_admin_headers):
    # Authenticated, because every endpoint now requires a scope. The assertions
    # below are unchanged -- they are the published response shape, and the whole
    # point of this file is that it stays byte-stable while the system behind it
    # grows.
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=super_admin_headers
    ) as ac:
        yield ac


@pytest.mark.asyncio
async def test_openapi_exposes_versioned_camera_routes(client):
    spec = (await client.get("/openapi.json")).json()
    assert "/api/v1/cameras" in spec["paths"]
    assert "/api/v1/cameras/{camera_id}/streams" in spec["paths"]


@pytest.mark.asyncio
async def test_list_cameras_returns_a_page(api_client):
    # api_client (not the bare `client` fixture) overrides get_session with the
    # testcontainer, so this does not silently query a developer's local database.
    response = await api_client.get("/api/v1/cameras")
    assert response.status_code == 200
    body = response.json()
    assert {"items", "total", "limit", "offset"} <= body.keys()


@pytest.mark.asyncio
async def test_streams_endpoint_reports_reachability(client):
    response = await client.get(
        "/api/v1/cameras/00000000-0000-0000-0000-000000000001/streams"
    )
    assert response.status_code == 200
    protocols = {s["protocol"] for s in response.json()}
    assert {"rtsp", "hls", "whep"} <= protocols
    assert all("reachability" in s for s in response.json())
