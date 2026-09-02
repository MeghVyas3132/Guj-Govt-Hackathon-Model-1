"""The stream relay.

The security surface here is unusual for this codebase: `target` arrives from a
browser, and the service fetches whatever it names. The same-origin guard is
therefore load-bearing, not a nicety, and most of these tests are about it.
"""

import httpx
import pytest

from app.services.stream_proxy import (
    MAX_BODY,
    StreamProxy,
    UpstreamError,
    content_type_for,
    rewrite_manifest,
)

BASE = "https://gw.test/cam01/index.m3u8"
PREFIX = "/api/v1/cameras/abc/preview-segment"

MEDIA = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:8
#EXT-X-KEY:METHOD=AES-128,URI="/enc.key",IV=0x00
#EXTINF:7.92,
seg00000.ts
#EXTINF:6.00,
seg00001.ts
#EXT-X-ENDLIST
"""

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low/index.m3u8
"""


# ---- manifest rewriting ----

def test_segments_are_pointed_at_the_proxy():
    out = rewrite_manifest(MEDIA, BASE, PREFIX)
    # No bare relative line survives: every URI line now starts with the proxy.
    bare = [l for l in out.splitlines() if l and not l.startswith(("#", PREFIX))]
    assert bare == []
    assert f"{PREFIX}?target=https%3A%2F%2Fgw.test%2Fcam01%2Fseg00000.ts" in out


def test_the_decryption_key_is_pointed_at_the_proxy():
    """The key URI is absolute on this gateway. Left alone it is fetched
    cross-origin without the credential, and playback fails as a decrypt error."""
    out = rewrite_manifest(MEDIA, BASE, PREFIX)
    assert 'URI="/api/v1/cameras/abc/preview-segment?target=https%3A%2F%2Fgw.test%2Fenc.key"' in out


def test_the_key_method_and_iv_are_preserved():
    out = rewrite_manifest(MEDIA, BASE, PREFIX)
    assert "METHOD=AES-128" in out and "IV=0x00" in out


def test_a_variant_playlist_is_pointed_at_the_proxy():
    out = rewrite_manifest(MASTER, BASE, PREFIX)
    assert f"{PREFIX}?target=https%3A%2F%2Fgw.test%2Fcam01%2Flow%2Findex.m3u8" in out


def test_other_tags_are_untouched():
    out = rewrite_manifest(MEDIA, BASE, PREFIX)
    for tag in ("#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:8", "#EXTINF:7.92,"):
        assert tag in out


def test_the_segment_count_is_unchanged():
    """Rewriting must not add or drop a line, or the media sequence shifts."""
    assert rewrite_manifest(MEDIA, BASE, PREFIX).count(PREFIX) == 3  # key + 2 segments


def test_an_absolute_segment_url_is_still_rewritten():
    body = "#EXTM3U\n#EXTINF:4.0,\nhttps://gw.test/other/seg.ts\n"
    assert "target=https%3A%2F%2Fgw.test%2Fother%2Fseg.ts" in rewrite_manifest(body, BASE, PREFIX)


def test_an_empty_manifest_does_not_raise():
    assert rewrite_manifest("", BASE, PREFIX).strip() == ""


# ---- content types, because the gateway lies ----

@pytest.mark.parametrize("url,expected", [
    ("https://gw.test/a/seg00000.ts", "video/mp2t"),
    ("https://gw.test/a/seg.m4s", "video/iso.segment"),
    ("https://gw.test/a/init.mp4", "video/mp4"),
    ("https://gw.test/enc.key", "application/octet-stream"),
])
def test_the_type_comes_from_the_extension_not_the_gateway(url, expected):
    """The gateway serves .ts as text/vnd.trolltech.linguist -- it thinks they
    are Qt Linguist files. A player that trusts Content-Type refuses them."""
    assert content_type_for(url, "text/vnd.trolltech.linguist") == expected


def test_a_manifest_content_type_survives():
    assert content_type_for(
        "https://gw.test/x", "application/vnd.apple.mpegurl"
    ) == "application/vnd.apple.mpegurl"


# ---- the same-origin guard ----

def proxy(handler):
    return StreamProxy(transport=httpx.MockTransport(handler))


def ok(request):
    return httpx.Response(200, content=b"data")


@pytest.mark.parametrize("target", [
    "http://169.254.169.254/latest/meta-data/",   # cloud instance metadata
    "http://localhost:8000/api/v1/admin/api-keys",  # our own admin surface
    "http://127.0.0.1:5432/",
    "https://evil.test/exfil",
    "https://gw.test.evil.test/seg.ts",            # suffix confusion
    "file:///etc/passwd",
])
@pytest.mark.asyncio
async def test_a_target_off_the_cameras_own_host_is_refused(target):
    """`target` comes from the browser. Without this guard a crafted value uses
    the registry's own network position to read something it should not."""
    with pytest.raises(UpstreamError) as exc:
        await proxy(ok).fetch(target, allowed_origin=BASE)
    assert exc.value.status == 400


@pytest.mark.asyncio
async def test_a_different_port_on_the_same_host_is_refused():
    with pytest.raises(UpstreamError):
        await proxy(ok).fetch("https://gw.test:8443/seg.ts", allowed_origin=BASE)


@pytest.mark.asyncio
async def test_a_different_scheme_on_the_same_host_is_refused():
    with pytest.raises(UpstreamError):
        await proxy(ok).fetch("http://gw.test/seg.ts", allowed_origin=BASE)


@pytest.mark.asyncio
async def test_the_cameras_own_host_is_allowed():
    body, _ = await proxy(ok).fetch("https://gw.test/cam01/seg.ts", allowed_origin=BASE)
    assert body == b"data"


# ---- upstream failures ----

@pytest.mark.asyncio
async def test_a_redirect_is_refused_rather_than_followed():
    """Following it hands the player an HTML login page, which presents as a
    corrupt stream rather than as an expired session."""
    handler = lambda r: httpx.Response(302, headers={"location": "/login"})
    with pytest.raises(UpstreamError, match="session expired"):
        await proxy(handler).fetch("https://gw.test/a.ts", allowed_origin=BASE)


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
@pytest.mark.asyncio
async def test_an_upstream_error_becomes_a_502(status):
    with pytest.raises(UpstreamError) as exc:
        await proxy(lambda r: httpx.Response(status)).fetch(
            "https://gw.test/a.ts", allowed_origin=BASE
        )
    assert exc.value.status == 502


@pytest.mark.asyncio
async def test_a_network_failure_becomes_a_502():
    def boom(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(UpstreamError) as exc:
        await proxy(boom).fetch("https://gw.test/a.ts", allowed_origin=BASE)
    assert exc.value.status == 502


@pytest.mark.asyncio
async def test_an_oversized_body_is_refused():
    """So this cannot be turned into a general-purpose file relay."""
    handler = lambda r: httpx.Response(200, content=b"x" * (MAX_BODY + 1))
    with pytest.raises(UpstreamError, match="size limit"):
        await proxy(handler).fetch("https://gw.test/a.ts", allowed_origin=BASE)


# ---- credentials reach the gateway ----

@pytest.mark.asyncio
async def test_a_cookie_credential_is_sent():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, content=b"x")

    await proxy(handler).fetch(
        "https://gw.test/a.ts", allowed_origin=BASE, secret="s", cookie_name="sentinel"
    )
    assert seen["cookie"] == "sentinel=s"


@pytest.mark.asyncio
async def test_a_header_credential_is_sent():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-key")
        return httpx.Response(200, content=b"x")

    await proxy(handler).fetch(
        "https://gw.test/a.ts", allowed_origin=BASE, secret="s", header_name="X-Key"
    )
    assert seen["key"] == "s"


@pytest.mark.asyncio
async def test_no_credential_is_sent_when_none_is_configured():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, content=b"x")

    await proxy(handler).fetch("https://gw.test/a.ts", allowed_origin=BASE)
    assert seen["cookie"] is None


# ---- the gateway refuses non-browser clients ---------------------------------

@pytest.mark.asyncio
async def test_a_self_identifying_user_agent_is_sent():
    """The Sentinel gateway answers 403 "browser required" to any User-Agent
    without a Mozilla/ prefix, including a bare "sentinel-registry/1.0".
    Verified live: the honest "Mozilla/5.0 (compatible; ...)" form is accepted,
    so nothing here pretends to be a browser it is not."""
    from app.core.config import DEFAULT_USER_AGENT

    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, content=b"x")

    await proxy(handler).fetch("https://gw.test/a.ts", allowed_origin=BASE)
    assert seen["ua"] == DEFAULT_USER_AGENT
    assert seen["ua"].startswith("Mozilla/5.0 (compatible;")
    assert "SentinelRegistry" in seen["ua"]


@pytest.mark.asyncio
async def test_a_header_credential_does_not_displace_the_user_agent():
    """Both are headers; setting the credential must not overwrite the agent."""
    from app.core.config import DEFAULT_USER_AGENT

    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent")
        seen["key"] = request.headers.get("x-key")
        return httpx.Response(200, content=b"x")

    await proxy(handler).fetch(
        "https://gw.test/a.ts", allowed_origin=BASE, secret="s", header_name="X-Key"
    )
    assert seen["ua"] == DEFAULT_USER_AGENT
    assert seen["key"] == "s"
