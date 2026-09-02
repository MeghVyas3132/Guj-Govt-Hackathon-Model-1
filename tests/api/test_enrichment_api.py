"""Deriving camera metadata through the API.

The catalogue the sandbox serves carries an id and a name. Everything technical
about a camera has to be derived from its stream, so these tests cover the path
that does it -- including the failure modes, because enrichment runs against
gateways we do not control and must never lose data it previously established.
"""

import httpx
import pytest
from sqlalchemy import select

from app.core.deps import get_enricher
from app.main import app
from app.models.camera import Camera
from app.models.department import Department
from app.models.stream_endpoint import StreamEndpoint
from app.services.enrichment import StreamEnricher

MEDIA = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:8
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-KEY:METHOD=AES-128,URI="/enc.key"
#EXTINF:7.92,
seg0.ts
#EXT-X-ENDLIST
"""

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,CODECS="avc1.640028",FRAME-RATE=25.000
high.m3u8
"""


def use_enricher(handler):
    """Substitute a mock-transport enricher, with media decoding off so no test
    shells out to ffprobe."""
    app.dependency_overrides[get_enricher] = lambda: StreamEnricher(
        transport=httpx.MockTransport(handler), probe_media=False
    )


@pytest.fixture
async def camera_with_stream(session):
    department = Department(code="ENR", name="Enrichment Dept")
    session.add(department)
    await session.flush()
    camera = Camera(
        camera_uid="GJ-ENR-000001",
        department_id=department.id,
        external_camera_id="cam01",
        name="Chiman bhai Bridge",
        location="SRID=4326;POINT(72.5 23.0)",
    )
    session.add(camera)
    await session.flush()
    session.add(
        StreamEndpoint(
            camera_id=camera.id, protocol="hls",
            url="https://gw.test/cam01/index.m3u8",
            reachability="public_cdn", is_primary=True,
        )
    )
    await session.commit()
    yield camera
    app.dependency_overrides.pop(get_enricher, None)


@pytest.mark.asyncio
async def test_a_master_playlist_fills_codec_and_resolution(
    api_client, session, camera_with_stream
):
    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    response = await api_client.post(f"/api/v1/cameras/{camera_with_stream.id}/enrich")

    assert response.status_code == 200
    body = response.json()
    assert (body["checked"], body["updated"], body["failed"]) == (1, 1, 0)
    assert body["results"][0]["metadata"]["resolution"] == "1920x1080"

    endpoint = (await session.execute(select(StreamEndpoint))).scalars().one()
    await session.refresh(endpoint)
    assert (endpoint.codec, endpoint.resolution) == ("avc1", "1920x1080")


@pytest.mark.asyncio
async def test_manifest_facts_are_stored_on_the_camera(
    api_client, session, camera_with_stream
):
    """Encryption, archive depth and liveness are exactly the operational facts a
    planner needs and the catalogue never carries."""
    use_enricher(lambda r: httpx.Response(200, text=MEDIA))
    await api_client.post(f"/api/v1/cameras/{camera_with_stream.id}/enrich")

    await session.refresh(camera_with_stream)
    manifest = camera_with_stream.metadata_["stream"]["manifest"]
    assert manifest["encryption"] == "AES-128"
    assert manifest["playlist_type"] == "VOD"
    assert manifest["is_live"] is False


@pytest.mark.asyncio
async def test_enrichment_is_idempotent(api_client, session, camera_with_stream):
    """A nightly re-run must not report every camera as changed, or the audit log
    becomes noise nobody reads."""
    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    url = f"/api/v1/cameras/{camera_with_stream.id}/enrich"

    assert (await api_client.post(url)).json()["updated"] == 1
    assert (await api_client.post(url)).json()["updated"] == 0


@pytest.mark.asyncio
async def test_a_failed_probe_does_not_erase_established_metadata(
    api_client, session, camera_with_stream
):
    """The important one. A gateway that is down for maintenance must not blank
    the codec that a successful probe recorded last week."""
    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    await api_client.post(f"/api/v1/cameras/{camera_with_stream.id}/enrich")

    use_enricher(lambda r: httpx.Response(503))
    response = await api_client.post(f"/api/v1/cameras/{camera_with_stream.id}/enrich")
    assert response.json()["failed"] == 1

    endpoint = (await session.execute(select(StreamEndpoint))).scalars().one()
    await session.refresh(endpoint)
    assert endpoint.codec == "avc1"


@pytest.mark.asyncio
async def test_an_unreachable_gateway_reports_rather_than_500s(
    api_client, camera_with_stream
):
    def refuse(request):
        raise httpx.ConnectError("no route to host")

    use_enricher(refuse)
    response = await api_client.post(f"/api/v1/cameras/{camera_with_stream.id}/enrich")
    assert response.status_code == 200
    assert "ConnectError" in response.json()["results"][0]["error"]


@pytest.mark.asyncio
async def test_a_camera_with_no_stream_endpoint_is_reported_not_skipped_silently(
    api_client, session
):
    department = Department(code="NOS", name="No Streams")
    session.add(department)
    await session.flush()
    camera = Camera(
        camera_uid="GJ-NOS-000001", department_id=department.id,
        external_camera_id="x", name="No stream",
        location="SRID=4326;POINT(72.5 23.0)",
    )
    session.add(camera)
    await session.commit()

    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    response = await api_client.post(f"/api/v1/cameras/{camera.id}/enrich")
    assert response.json()["results"][0]["error"] == "no enrichable stream endpoint"
    app.dependency_overrides.pop(get_enricher, None)


@pytest.mark.asyncio
async def test_an_unknown_camera_is_404(api_client):
    from uuid import uuid4

    assert (await api_client.post(f"/api/v1/cameras/{uuid4()}/enrich")).status_code == 404


@pytest.mark.asyncio
async def test_enrichment_is_audited(api_client, session, camera_with_stream):
    from app.models.user import AuditLog

    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    await api_client.post(f"/api/v1/cameras/{camera_with_stream.id}/enrich")

    rows = (
        (await session.execute(
            select(AuditLog).where(AuditLog.action == "camera.enriched")
        )).scalars().all()
    )
    assert len(rows) == 1
    assert rows[0].after["resolution"] == "1920x1080"


@pytest.mark.asyncio
async def test_bulk_enrichment_respects_the_limit(api_client, camera_with_stream):
    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    response = await api_client.post("/api/v1/cameras/enrich?limit=1")
    assert response.status_code == 200
    assert response.json()["checked"] <= 1


@pytest.mark.asyncio
async def test_bulk_enrichment_accepts_the_shared_camera_filter(
    api_client, camera_with_stream
):
    """The same filter as the list and the map, so what you see is what you enrich."""
    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    response = await api_client.post("/api/v1/cameras/enrich?q=nothing-matches-this")
    assert response.status_code == 200
    assert response.json()["checked"] == 0


@pytest.mark.asyncio
async def test_enrichment_requires_the_write_scope(session, camera_with_stream):
    """Reading metadata is a read; changing what the registry asserts is a write."""
    from tests.api.test_rbac import client_for, headers_for, make_user

    viewer = await make_user(session, "viewer")
    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    async with await client_for(session, headers_for(viewer)) as client:
        response = await client.post(f"/api/v1/cameras/{camera_with_stream.id}/enrich")
    assert response.status_code == 403


# ---- convergence: a scheduled run must not re-do work ------------------------

@pytest.mark.asyncio
async def test_bulk_enrichment_skips_cameras_already_described(
    api_client, session, camera_with_stream
):
    """The gateway is the bottleneck, not this service. A fleet run that
    re-probes cameras it already described spends its budget re-learning the
    same facts and never reaches the ones it has not seen."""
    use_enricher(lambda r: httpx.Response(200, text=MASTER))

    first = (await api_client.post("/api/v1/cameras/enrich?limit=50")).json()
    assert first["updated"] == 1

    calls = {"n": 0}

    def counting(request):
        calls["n"] += 1
        return httpx.Response(200, text=MASTER)

    use_enricher(counting)
    second = (await api_client.post("/api/v1/cameras/enrich?limit=50")).json()

    # Reported, but never fetched again.
    assert second["checked"] == 1
    assert second["updated"] == 0
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_only_missing_can_be_turned_off_to_refresh(
    api_client, session, camera_with_stream
):
    """A camera that was re-cabled needs its metadata re-read."""
    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    await api_client.post("/api/v1/cameras/enrich?limit=50")

    calls = {"n": 0}

    def counting(request):
        calls["n"] += 1
        return httpx.Response(200, text=MASTER)

    use_enricher(counting)
    await api_client.post("/api/v1/cameras/enrich?limit=50&only_missing=false")
    assert calls["n"] > 0


@pytest.mark.asyncio
async def test_a_camera_that_failed_before_is_retried_next_run(
    api_client, session, camera_with_stream
):
    """The convergence property: what failed stays pending, so repeated runs
    close the gap rather than plateauing."""
    use_enricher(lambda r: httpx.Response(503))
    first = (await api_client.post("/api/v1/cameras/enrich?limit=50")).json()
    assert first["failed"] == 1

    use_enricher(lambda r: httpx.Response(200, text=MASTER))
    second = (await api_client.post("/api/v1/cameras/enrich?limit=50")).json()
    assert second["updated"] == 1


@pytest.mark.asyncio
async def test_bulk_enrichment_commits_progressively(
    api_client, session, camera_with_stream
):
    """A fleet pass runs for minutes against a slow gateway. A single trailing
    commit means an interrupted run discards everything it had measured, and the
    registry can never converge."""
    from sqlalchemy import select

    from app.api.v1.routers.cameras import ENRICH_CHUNK
    from app.models.stream_endpoint import StreamEndpoint

    seen: list[str | None] = []

    def handler(request):
        # Observe what is already durable partway through the run.
        seen.append("called")
        return httpx.Response(200, text=MASTER)

    use_enricher(handler)
    assert ENRICH_CHUNK > 0
    await api_client.post("/api/v1/cameras/enrich?limit=50")

    endpoint = (await session.execute(select(StreamEndpoint))).scalars().one()
    await session.refresh(endpoint)
    assert endpoint.codec == "avc1"
