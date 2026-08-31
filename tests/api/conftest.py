"""Fixtures for the API tests.

`tests/api/test_contract.py` is the guard on the published response shape: other teams
are coding against it, so it is kept byte-for-byte stable from plan to plan and any
need to edit it is the signal that the contract has drifted.

That test drives the app through its own `client` fixture, which deliberately does not
override `get_session` -- it never needed a database, because until now the streams
route returned a hard-coded stub list. Plan 2 replaced that stub with a real query
against `stream_endpoints`, so the same assertions now need a session and a camera to
read back. Supplying both from here is what lets the contract file stay untouched
while the data behind it becomes real: the shape assertions are unchanged, but they
are now checking rows the ingestion pipeline actually wrote.
"""

from uuid import UUID

import pytest

# The reference camera the published examples and the stub both used: Sentinel
# sandbox cam04, reachable over all three protocols.
REFERENCE_CAMERA_ID = UUID("00000000-0000-0000-0000-000000000001")

_CATALOGUE_ENTRY = {
    "id": "cam04",
    "name": "Nehru Bridge",
    "lat": 23.0225,
    "lon": 72.5714,
    "codec": "h264",
    "resolution": "1920x1080",
    "hls": "https://cctv.corp8.cloud/cam04/index.m3u8",
    "rtsp": "rtsp://103.250.160.189:8554/stream/cam04",
    "whep": "http://103.250.160.189:8889/stream/cam04/whep",
}


@pytest.fixture(autouse=True)
async def reference_camera(request, session):
    """Give the bare `client` fixture a database and the reference camera.

    Scoped to tests that actually use `client`: seeding unconditionally would add a
    camera to every API test and break the ones that assert an empty registry.
    """
    if "client" not in request.fixturenames:
        yield
        return

    from app.adapters.sentinel_adapter import SentinelAdapter
    from app.core.db import get_session
    from app.main import app
    from app.models.camera import Camera
    from app.models.department import Department
    from app.services.ingestion import IngestionService

    department = Department(code="SEN", name="Sentinel Sandbox")
    session.add(department)
    await session.flush()
    session.add(
        Camera(
            id=REFERENCE_CAMERA_ID,
            camera_uid="GJ-SEN-000001",
            department_id=department.id,
            external_camera_id="cam04",
            name="Nehru Bridge",
            location="SRID=4326;POINT(72.5714 23.0225)",
        )
    )
    await session.flush()

    # Written by the production writer from the production adapter's own mapping, so
    # the protocols and reachability values under test are the real ones rather than
    # a second hand-maintained copy of them.
    adapter = SentinelAdapter(catalogue_url="https://example.invalid/cameras.json")
    await IngestionService(session)._sync_endpoints(
        await session.get(Camera, REFERENCE_CAMERA_ID),
        adapter.endpoints_for(_CATALOGUE_ENTRY),
    )
    await session.commit()

    app.dependency_overrides[get_session] = lambda: session
    yield
    app.dependency_overrides.clear()
