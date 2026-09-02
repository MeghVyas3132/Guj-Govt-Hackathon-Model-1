import httpx
import pytest

from app.core.enums import CameraStatus
from app.services.probe import HlsProbe


@pytest.mark.asyncio
async def test_serving_manifest_is_reported_online():
    manifest = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nseg1.ts\n"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=manifest))
    result = await HlsProbe(transport=transport).check(
        "https://cctv.corp8.cloud/cam04/index.m3u8"
    )
    assert result.status is CameraStatus.ONLINE
    assert result.latency_ms is not None


@pytest.mark.asyncio
async def test_manifest_with_no_segments_is_reported_offline():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"#EXTM3U\n#EXT-X-VERSION:3\n")
    )
    result = await HlsProbe(transport=transport).check("https://x/index.m3u8")
    assert result.status is CameraStatus.OFFLINE
    assert "no segments" in result.detail["reason"]


@pytest.mark.asyncio
async def test_http_error_is_reported_offline():
    transport = httpx.MockTransport(lambda request: httpx.Response(502))
    result = await HlsProbe(transport=transport).check("https://x/index.m3u8")
    assert result.status is CameraStatus.OFFLINE
    assert result.detail["http_status"] == 502


@pytest.mark.asyncio
async def test_auth_redirect_is_unknown_not_offline():
    # A 302 to the login page means our session expired, not that the camera is down.
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "/auth/login"})
    )
    result = await HlsProbe(transport=transport).check("https://x/index.m3u8")
    assert result.status is CameraStatus.UNKNOWN
    # Reading the Location back off the 3xx proves the probe saw the redirect itself
    # rather than following it to a 200 login page and inferring something.
    assert result.detail["location"] == "/auth/login"


@pytest.mark.asyncio
async def test_connection_failure_is_reported_offline():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await HlsProbe(transport=httpx.MockTransport(boom)).check(
        "https://x/index.m3u8"
    )
    assert result.status is CameraStatus.OFFLINE
    assert result.detail["error"] == "ConnectError"


@pytest.mark.asyncio
async def test_the_probe_identifies_itself():
    """Without a browser-shaped agent this gateway answers 403, which would be
    recorded as a fleet-wide outage rather than as our request being refused."""
    from app.core.config import DEFAULT_USER_AGENT

    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text="#EXTM3U\n#EXTINF:4,\ns.ts\n")

    await HlsProbe(transport=httpx.MockTransport(handler)).check("https://gw.test/i.m3u8")
    assert seen["ua"] == DEFAULT_USER_AGENT
