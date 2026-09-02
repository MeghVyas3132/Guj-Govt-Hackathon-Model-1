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


# ---- a timeout is not an outage ---------------------------------------------

@pytest.mark.asyncio
async def test_a_timeout_is_unknown_not_offline():
    """The bug this fixes: the probe ran twenty at a time against a gateway that
    slows under concurrency, timed out, and recorded six healthy cameras as
    offline. A timeout is a fact about our request, not about the camera --
    exactly the reasoning already applied to a redirect."""
    def slow(request):
        raise httpx.ReadTimeout("")

    result = await HlsProbe(transport=httpx.MockTransport(slow), retries=0).check(
        "https://gw.test/i.m3u8"
    )
    assert result.status is CameraStatus.UNKNOWN
    assert "may be fine" in result.detail["reason"]


@pytest.mark.asyncio
async def test_a_refused_connection_is_offline():
    """This one *is* evidence about the endpoint, so it stays an outage."""
    def refused(request):
        raise httpx.ConnectError("connection refused")

    result = await HlsProbe(transport=httpx.MockTransport(refused)).check(
        "https://gw.test/i.m3u8"
    )
    assert result.status is CameraStatus.OFFLINE


@pytest.mark.asyncio
async def test_a_timeout_is_retried_before_giving_up():
    """A single slow response is the normal case on this gateway."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("")
        return httpx.Response(200, text="#EXTM3U\n#EXTINF:4,\ns.ts\n")

    result = await HlsProbe(transport=httpx.MockTransport(handler), retries=1).check(
        "https://gw.test/i.m3u8"
    )
    assert result.status is CameraStatus.ONLINE
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_retries_are_bounded():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("")

    result = await HlsProbe(transport=httpx.MockTransport(handler), retries=1).check(
        "https://gw.test/i.m3u8"
    )
    assert result.status is CameraStatus.UNKNOWN
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_an_empty_manifest_is_still_offline():
    """The gateway answered and served nothing: the encoder has stopped. That is
    a real outage and must not be softened into unknown along with the rest."""
    handler = lambda r: httpx.Response(200, text="#EXTM3U\n#EXT-X-ENDLIST\n")
    result = await HlsProbe(transport=httpx.MockTransport(handler)).check(
        "https://gw.test/i.m3u8"
    )
    assert result.status is CameraStatus.OFFLINE


@pytest.mark.asyncio
async def test_the_default_timeout_fits_this_gateway():
    """216KB manifests that take 1-3s unloaded and up to 17.5s under load."""
    assert HlsProbe().timeout >= 30


def test_probe_concurrency_is_low_enough_not_to_cause_timeouts():
    """A high fan-out makes the probe manufacture the timeouts it then reports."""
    from app.workers.tasks import PROBE_CONCURRENCY

    assert PROBE_CONCURRENCY <= 8
