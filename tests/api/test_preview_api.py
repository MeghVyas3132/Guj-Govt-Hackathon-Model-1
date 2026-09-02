"""Camera preview over HTTP.

The proxy is the only part of the registry that fetches a URL supplied by the
caller, so the authorisation and origin checks are tested here as well as at the
service level.
"""

import pytest

from app.models.camera import Camera
from app.models.department import Department
from app.models.stream_endpoint import StreamEndpoint


@pytest.fixture
async def camera(session):
    department = Department(code="PRV", name="Preview Dept")
    session.add(department)
    await session.flush()
    row = Camera(
        camera_uid="GJ-PRV-000001", department_id=department.id,
        external_camera_id="cam01", name="Preview camera",
        location="SRID=4326;POINT(72.5 23.0)",
    )
    session.add(row)
    await session.flush()
    session.add(
        StreamEndpoint(
            camera_id=row.id, protocol="hls",
            url="https://gw.test/cam01/index.m3u8",
            reachability="public_cdn", is_primary=True,
        )
    )
    await session.commit()
    return row


@pytest.mark.asyncio
async def test_an_unknown_camera_is_404(api_client):
    from uuid import uuid4

    assert (await api_client.get(f"/api/v1/cameras/{uuid4()}/preview.m3u8")).status_code == 404


@pytest.mark.asyncio
async def test_a_camera_without_an_hls_endpoint_says_so(api_client, session):
    department = Department(code="NOH", name="No HLS")
    session.add(department)
    await session.flush()
    row = Camera(
        camera_uid="GJ-NOH-000001", department_id=department.id,
        external_camera_id="x", name="No HLS",
        location="SRID=4326;POINT(72.5 23.0)",
    )
    session.add(row)
    await session.flush()
    session.add(
        StreamEndpoint(
            camera_id=row.id, protocol="rtsp", url="rtsp://gw.test/x",
            reachability="direct_ip",
        )
    )
    await session.commit()

    response = await api_client.get(f"/api/v1/cameras/{row.id}/preview.m3u8")
    assert response.status_code == 404
    assert "no HLS endpoint" in response.json()["detail"]


@pytest.mark.asyncio
async def test_preview_requires_a_read_scope(session, camera):
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/v1/cameras/{camera.id}/preview.m3u8")
    app.dependency_overrides.clear()
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_segment_target_off_the_cameras_host_is_refused(api_client, camera):
    """The SSRF guard, reached through the real route. `target` is attacker-
    controlled: it arrives from the browser."""
    response = await api_client.get(
        f"/api/v1/cameras/{camera.id}/preview-segment",
        params={"target": "http://169.254.169.254/latest/meta-data/"},
    )
    assert response.status_code == 400
    assert "not on the camera's own stream host" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_segment_target_pointing_at_our_own_api_is_refused(api_client, camera):
    response = await api_client.get(
        f"/api/v1/cameras/{camera.id}/preview-segment",
        params={"target": "http://localhost:8000/api/v1/admin/api-keys"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_segment_request_without_a_target_is_422(api_client, camera):
    assert (
        await api_client.get(f"/api/v1/cameras/{camera.id}/preview-segment")
    ).status_code == 422
