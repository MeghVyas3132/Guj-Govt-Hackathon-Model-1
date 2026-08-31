"""Pull a source catalogue over HTTP through the same pipeline as a CSV upload.

The live Sentinel catalogue sits behind a session cookie, so every test here serves
the catalogue from an httpx.MockTransport injected over the router's module-level
`_ADAPTER_TRANSPORT`. Nothing in this file touches the network.
"""

import httpx
import pytest
from sqlalchemy import select

from app.models.camera import Camera
from app.models.stream_endpoint import StreamEndpoint

CATALOGUE = [
    {
        "id": "cam04",
        "name": "Nehru Bridge",
        "lat": 23.0225,
        "lon": 72.5714,
        "codec": "h264",
        "resolution": "1920x1080",
        "live": True,
        "hls": "https://cctv.corp8.cloud/cam04/index.m3u8",
        "rtsp": "rtsp://103.250.160.189:8554/stream/cam04",
        "whep": "http://103.250.160.189:8889/stream/cam04/whep",
    }
]


@pytest.fixture(autouse=True)
def mock_catalogue(monkeypatch):
    import app.api.v1.routers.onboarding as onboarding

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=CATALOGUE))
    monkeypatch.setattr(onboarding, "_ADAPTER_TRANSPORT", transport)


@pytest.fixture
async def sentinel_department(session):
    from app.models.department import Department
    from app.models.field_mapping import FieldMapping

    dept = Department(code="SEN", name="Sentinel Sandbox")
    session.add(dept)
    await session.flush()
    session.add(
        FieldMapping(
            department_id=dept.id,
            version=1,
            config={
                "column_map": {
                    "id": "external_camera_id",
                    "lat": "latitude",
                    "lon": "longitude",
                    "name": "name",
                }
            },
        )
    )
    await session.commit()
    return dept


@pytest.mark.asyncio
async def test_sync_onboards_catalogue_cameras(api_client, session, sentinel_department):
    response = await api_client.post(
        f"/api/v1/onboarding/adapters/sentinel/sync?department_id={sentinel_department.id}"
    )
    assert response.status_code == 200
    assert response.json()["created"] == 1

    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.external_camera_id == "cam04"


@pytest.mark.asyncio
async def test_sync_writes_the_stream_endpoints_too(
    api_client, session, sentinel_department
):
    """The reason this route exists rather than just the CSV upload: the catalogue
    carries the stream URLs, and Models 2-4 need them."""
    await api_client.post(
        f"/api/v1/onboarding/adapters/sentinel/sync?department_id={sentinel_department.id}"
    )
    rows = (await session.execute(select(StreamEndpoint))).scalars().all()
    assert {r.protocol for r in rows} == {"hls", "rtsp", "whep"}
    assert next(r for r in rows if r.protocol == "hls").reachability == "public_cdn"


@pytest.mark.asyncio
async def test_second_sync_is_idempotent(api_client, sentinel_department):
    url = (
        "/api/v1/onboarding/adapters/sentinel/sync"
        f"?department_id={sentinel_department.id}"
    )
    await api_client.post(url)
    second = (await api_client.post(url)).json()
    assert second["created"] == 0
    assert second["skipped"] == 1


@pytest.mark.asyncio
async def test_unknown_adapter_code_is_404(api_client, sentinel_department):
    response = await api_client.post(
        f"/api/v1/onboarding/adapters/nosuch/sync?department_id={sentinel_department.id}"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unknown_department_is_404(api_client):
    response = await api_client.post(
        "/api/v1/onboarding/adapters/sentinel/sync"
        "?department_id=00000000-0000-0000-0000-0000000000ff"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_unreachable_catalogue_is_502_not_500(
    api_client, sentinel_department, monkeypatch
):
    """On demo day a 502 says "the cookie expired"; a 500 says "your code broke".
    Telling those apart in the first five seconds is the whole point."""
    import app.api.v1.routers.onboarding as onboarding

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(onboarding, "_ADAPTER_TRANSPORT", httpx.MockTransport(refuse))
    response = await api_client.post(
        "/api/v1/onboarding/adapters/sentinel/sync"
        f"?department_id={sentinel_department.id}"
    )
    assert response.status_code == 502
    assert "catalogue" in response.json()["detail"]
