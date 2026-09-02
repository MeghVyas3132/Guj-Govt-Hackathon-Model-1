"""Stream metadata derivation.

parse_manifest is pure, so most of this needs no network and no ffmpeg. The
manifests below are real shapes: the Sentinel gateway's media playlist, a
standard master playlist, and the malformed ones a camera emits while restarting.
"""

import httpx
import pytest

from app.services.enrichment import StreamEnricher, _fps, parse_manifest

SENTINEL = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-TARGETDURATION:8
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-KEY:METHOD=AES-128,URI="/enc.key",IV=0x00000000000000000000000000000000
#EXTINF:7.920000,
seg00000.ts
#EXTINF:6.006000,
seg00001.ts
#EXT-X-ENDLIST
"""

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360,CODECS="avc1.4d401e,mp4a.40.2",FRAME-RATE=25.000
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,CODECS="avc1.640028",FRAME-RATE=30.000
high.m3u8
"""

LIVE = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:4
#EXTINF:4.000,
s1.ts
"""


# ---- manifest parsing ----

def test_the_sentinel_manifest_is_read_correctly():
    m = parse_manifest(SENTINEL)
    assert (m.version, m.target_duration, m.playlist_type) == (6, 8.0, "VOD")
    assert m.segment_count == 2
    assert m.total_duration_s == pytest.approx(13.926)
    assert (m.encryption, m.key_uri) == ("AES-128", "/enc.key")


def test_a_vod_playlist_is_not_live():
    """The sandbox serves recorded loops. Reporting them as live would put a
    freshness claim on the map that nothing backs."""
    assert parse_manifest(SENTINEL).is_live is False


def test_a_playlist_without_an_endlist_is_live():
    assert parse_manifest(LIVE).is_live is True


def test_a_finished_event_playlist_is_not_live():
    assert parse_manifest(LIVE + "#EXT-X-ENDLIST\n").is_live is False


def test_an_empty_manifest_yields_no_liveness_claim():
    """Nothing observed means nothing asserted, not a default of False."""
    assert parse_manifest("#EXTM3U\n").is_live is None


def test_a_master_playlist_lists_its_variants():
    m = parse_manifest(MASTER)
    assert m.is_master and len(m.variants) == 2
    assert m.variants[1] == {
        "resolution": "1920x1080", "codecs": "avc1.640028",
        "bandwidth": 4000000, "frame_rate": 30.0, "uri": "high.m3u8",
    }


def test_variant_uris_are_captured():
    assert [v["uri"] for v in parse_manifest(MASTER).variants] == ["low.m3u8", "high.m3u8"]


def test_a_master_playlist_has_no_segments():
    """Variant URIs must not be miscounted as media segments."""
    assert parse_manifest(MASTER).segment_count == 0


def test_method_none_means_unencrypted():
    m = parse_manifest("#EXTM3U\n#EXT-X-KEY:METHOD=NONE\n#EXTINF:4.0,\ns.ts\n")
    assert m.encryption is None


def test_a_manifest_with_no_key_tag_reports_no_encryption():
    assert parse_manifest(LIVE).encryption is None


def test_sample_aes_encryption_is_reported():
    m = parse_manifest('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="k"\n')
    assert m.encryption == "SAMPLE-AES"


@pytest.mark.parametrize("text", ["", "\n\n", "not a manifest at all", "<html>login</html>"])
def test_junk_input_does_not_raise(text):
    """A gateway that returns a login page must yield an empty reading, not a
    traceback that aborts a batch of 200 cameras."""
    assert parse_manifest(text).segment_count >= 0


def test_a_non_numeric_version_is_ignored_rather_than_fatal():
    assert parse_manifest("#EXTM3U\n#EXT-X-VERSION:six\n").version is None


def test_a_non_numeric_duration_is_ignored():
    m = parse_manifest("#EXTM3U\n#EXTINF:abc,\ns.ts\n#EXTINF:4.0,\nt.ts\n")
    assert m.total_duration_s == 4.0


def test_a_non_numeric_bandwidth_is_dropped_but_the_variant_survives():
    m = parse_manifest('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=fast,RESOLUTION=640x360\nlow.m3u8\n')
    assert m.variants[0] == {"resolution": "640x360", "uri": "low.m3u8"}


def test_crlf_line_endings_are_handled():
    """Windows-hosted VMS gateways emit CRLF."""
    assert parse_manifest(SENTINEL.replace("\n", "\r\n")).segment_count == 2


def test_leading_whitespace_is_tolerated():
    assert parse_manifest("  #EXTM3U\n  #EXT-X-VERSION:4\n").version == 4


def test_a_long_archive_totals_correctly():
    body = "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n" + "#EXTINF:6.0,\ns.ts\n" * 7200
    m = parse_manifest(body)
    assert m.segment_count == 7200 and m.total_duration_s == 43200.0


# ---- frame-rate rationals ----

@pytest.mark.parametrize("value,expected", [
    ("30/1", 30.0), ("25/1", 25.0), ("30000/1001", 29.97),
    ("0/0", None), ("", None), (None, None), ("30", None), ("a/b", None),
])
def test_frame_rate_rationals(value, expected):
    assert _fps(value) == expected


# ---- the enricher over a mocked transport ----

def enricher(handler, **kw):
    kw.setdefault("probe_media", False)
    return StreamEnricher(transport=httpx.MockTransport(handler), **kw)


@pytest.mark.asyncio
async def test_a_master_playlist_needs_no_media_decode():
    """Resolution and codec come free from the manifest, so the expensive tier
    is skipped entirely."""
    e = enricher(lambda r: httpx.Response(200, text=MASTER), probe_media=True, ffprobe_path=None)
    m = await e.enrich("https://x.test/i.m3u8")
    assert (m.codec, m.resolution, m.frame_rate) == ("avc1", "1920x1080", 30.0)


@pytest.mark.asyncio
async def test_the_highest_bandwidth_variant_is_chosen():
    m = await enricher(lambda r: httpx.Response(200, text=MASTER)).enrich("https://x.test/i.m3u8")
    assert m.resolution == "1920x1080"


@pytest.mark.asyncio
async def test_a_media_playlist_still_yields_manifest_facts_without_ffprobe():
    """A deployment with no ffmpeg installed must still get the cheap tier."""
    e = StreamEnricher(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=SENTINEL)),
                       ffprobe_path=None)
    m = await e.enrich("https://x.test/i.m3u8")
    assert m.codec is None
    assert m.manifest.encryption == "AES-128"
    assert m.manifest.segment_count == 2


@pytest.mark.parametrize("status", [401, 403, 404, 500, 502])
@pytest.mark.asyncio
async def test_an_error_status_is_recorded_not_raised(status):
    """Enrichment is best-effort: one unreachable camera must not fail a batch."""
    m = await enricher(lambda r: httpx.Response(status)).enrich("https://x.test/i.m3u8")
    # The message names the status and the URL, so an operator can tell a dead
    # camera from a mistyped connector without reading logs.
    assert m.error.startswith(f"HTTP {status}") and m.codec is None


@pytest.mark.asyncio
async def test_a_redirect_is_recorded_rather_than_followed():
    """Following it would parse a login page as a manifest."""
    handler = lambda r: httpx.Response(302, headers={"location": "/login"})
    m = await enricher(handler).enrich("https://x.test/i.m3u8")
    assert m.error.startswith("HTTP 302")


@pytest.mark.asyncio
async def test_a_network_failure_is_recorded_with_its_type():
    def boom(request):
        raise httpx.ConnectError("refused")

    m = await enricher(boom).enrich("https://x.test/i.m3u8")
    # Names the stage: "ConnectError fetching manifest", not a bare repr.
    assert "ConnectError" in m.error and "manifest" in m.error


@pytest.mark.asyncio
async def test_a_cookie_secret_is_sent():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, text=SENTINEL)

    await enricher(handler).enrich("https://x.test/i.m3u8", secret="s", cookie_name="sentinel")
    assert seen["cookie"] == "sentinel=s"


@pytest.mark.asyncio
async def test_a_header_secret_is_sent():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-key")
        return httpx.Response(200, text=SENTINEL)

    await enricher(handler).enrich("https://x.test/i.m3u8", secret="s", header_name="X-Key")
    assert seen["key"] == "s"


# ---- serialisation, which is what reaches the database ----

@pytest.mark.asyncio
async def test_absent_fields_are_omitted_rather_than_stored_as_null():
    """A wall of nulls in metadata is indistinguishable from a probe that ran and
    found nothing. Omission keeps "not measured" honest."""
    m = await enricher(lambda r: httpx.Response(200, text=LIVE)).enrich("https://x.test/i.m3u8")
    assert "codec" not in m.to_dict()
    assert "resolution" not in m.to_dict()


@pytest.mark.asyncio
async def test_the_serialised_form_is_json_safe():
    import json

    m = await enricher(lambda r: httpx.Response(200, text=MASTER)).enrich("https://x.test/i.m3u8")
    assert json.loads(json.dumps(m.to_dict()))["resolution"] == "1920x1080"


def test_resolution_needs_both_dimensions():
    from app.services.enrichment import StreamMetadata

    assert StreamMetadata(width=1920).resolution is None
    assert StreamMetadata(width=1920, height=1080).resolution == "1920x1080"


def test_is_live_false_survives_serialisation():
    """`v not in (None, [], 0)` drops False, because False == 0 in Python. That
    silently discarded the one fact distinguishing a recorded loop from a feed."""
    from app.services.enrichment import StreamMetadata

    out = StreamMetadata(manifest=parse_manifest(SENTINEL)).to_dict()
    assert out["manifest"]["is_live"] is False


def test_a_zero_segment_count_is_reported_rather_than_hidden():
    from app.services.enrichment import StreamMetadata

    out = StreamMetadata(manifest=parse_manifest(MASTER)).to_dict()
    assert out["manifest"]["segment_count"] == 0


# ---- segment fetching, decryption, and retry ---------------------------------

def test_the_first_segment_and_key_iv_are_captured():
    """These are what let us fetch one segment ourselves rather than making
    ffmpeg parse a 7,200-entry playlist to find it."""
    m = parse_manifest(SENTINEL)
    assert m.first_segment == "seg00000.ts"
    assert m.key_iv == "0x00000000000000000000000000000000"


def test_only_the_first_segment_is_recorded():
    body = "#EXTM3U\n#EXTINF:4,\na.ts\n#EXTINF:4,\nb.ts\n"
    assert parse_manifest(body).first_segment == "a.ts"


def test_a_variant_uri_is_not_mistaken_for_a_segment():
    assert parse_manifest(MASTER).first_segment is None


def test_aes128_round_trips():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from app.services.enrichment import _decrypt_aes128

    key, iv = b"k" * 16, bytes(16)
    plain = b"mpeg-ts payload " * 8
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    cipher = encryptor.update(plain) + encryptor.finalize()

    assert _decrypt_aes128(cipher, key, "0x" + "00" * 16) == plain


def test_valid_pkcs7_padding_is_stripped():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from app.services.enrichment import _decrypt_aes128

    key, iv = b"k" * 16, bytes(16)
    plain = b"payload" + bytes([9]) * 9  # 7 + 9 = one block, padded
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    cipher = encryptor.update(plain) + encryptor.finalize()

    assert _decrypt_aes128(cipher, key, None) == b"payload"


def test_a_truncated_segment_is_reported_not_silently_trimmed():
    """Stripping bytes that only look like padding would present a short
    download as a corrupt stream."""
    from app.services.enrichment import _decrypt_aes128

    with pytest.raises(ValueError, match="whole number of AES blocks"):
        _decrypt_aes128(b"x" * 17, b"k" * 16, None)


def test_a_malformed_iv_falls_back_to_zero_rather_than_raising():
    from app.services.enrichment import _decrypt_aes128

    # Not hex, and the wrong length. Neither should crash a fleet run.
    assert isinstance(_decrypt_aes128(b"\x00" * 16, b"k" * 16, "0xZZ"), bytes)


@pytest.mark.asyncio
async def test_a_timeout_is_retried():
    """The gateway's latency climbs under load, so one slow response is normal
    rather than fatal. Giving up on the first loses cameras that would have
    succeeded a moment later."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, content=b"ok")

    e = StreamEnricher(
        transport=httpx.MockTransport(handler), retry_backoff_s=0, max_retries=2
    )
    assert await e._fetch("https://x.test/a", None, None, None) == b"ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_an_http_status_is_not_retried():
    """A 404 is a settled fact; re-asking wastes a slot on the bottleneck."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404)

    e = StreamEnricher(transport=httpx.MockTransport(handler), retry_backoff_s=0)
    with pytest.raises(ValueError, match="HTTP 404"):
        await e._fetch("https://x.test/a", None, None, None)
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_retries_are_bounded():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.ReadTimeout("always")

    e = StreamEnricher(
        transport=httpx.MockTransport(handler), retry_backoff_s=0, max_retries=2
    )
    with pytest.raises(httpx.TimeoutException):
        await e._fetch("https://x.test/a", None, None, None)
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_the_decryption_key_is_fetched_once_across_cameras():
    """The sandbox serves one /enc.key for all thirty cameras. On a gateway with
    eleven seconds of latency, re-fetching it per camera is the difference
    between a fleet run finishing and timing out."""
    from app.services.enrichment import _KEY_CACHE

    _KEY_CACHE.clear()
    fetched = {"keys": 0}

    def handler(request):
        if request.url.path.endswith("enc.key"):
            fetched["keys"] += 1
            return httpx.Response(200, content=b"k" * 16)
        return httpx.Response(200, content=b"\x00" * 32)

    e = StreamEnricher(transport=httpx.MockTransport(handler), retry_backoff_s=0)
    manifest = parse_manifest(SENTINEL)
    for _ in range(3):
        await e._segment_bytes(
            "https://gw.test/cam01/index.m3u8", manifest, None, None, None
        )
    assert fetched["keys"] == 1
    _KEY_CACHE.clear()


@pytest.mark.asyncio
async def test_an_unsupported_encryption_scheme_is_named():
    """SAMPLE-AES encrypts inside the container; a whole-buffer decrypt would
    return noise that presents as a corrupt stream."""
    from app.services.enrichment import _KEY_CACHE

    _KEY_CACHE.clear()
    manifest = parse_manifest('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="/k"\n#EXTINF:4,\na.ts\n')
    e = StreamEnricher(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x")),
        retry_backoff_s=0,
    )
    with pytest.raises(ValueError, match="SAMPLE-AES"):
        await e._segment_bytes("https://gw.test/i.m3u8", manifest, None, None, None)


@pytest.mark.asyncio
async def test_a_wrong_length_key_is_rejected():
    from app.services.enrichment import _KEY_CACHE

    _KEY_CACHE.clear()

    def handler(request):
        if request.url.path.endswith("enc.key"):
            return httpx.Response(200, content=b"short")
        return httpx.Response(200, content=b"\x00" * 32)

    e = StreamEnricher(transport=httpx.MockTransport(handler), retry_backoff_s=0)
    with pytest.raises(ValueError, match="expected 16"):
        await e._segment_bytes(
            "https://gw.test/cam01/index.m3u8", parse_manifest(SENTINEL), None, None, None
        )
    _KEY_CACHE.clear()


@pytest.mark.asyncio
async def test_an_unencrypted_segment_is_returned_as_is():
    from app.services.enrichment import _KEY_CACHE

    _KEY_CACHE.clear()
    manifest = parse_manifest(LIVE)
    e = StreamEnricher(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"raw-ts")),
        retry_backoff_s=0,
    )
    body = await e._segment_bytes("https://gw.test/i.m3u8", manifest, None, None, None)
    assert body == b"raw-ts"


def test_the_network_and_decode_budgets_are_separate():
    """ffprobe reads bytes already in memory; giving it the network's budget
    means a hung decode blocks a fleet pass for no reason."""
    e = StreamEnricher(ffprobe_path=None)
    assert e.decode_timeout < e.media_timeout


def test_the_retry_budget_is_bounded_in_wall_clock():
    """Three attempts at the default must stay well inside a pass, or a single
    stuck camera stalls the whole run."""
    e = StreamEnricher(ffprobe_path=None)
    worst_case = e.media_timeout * (e.max_retries + 1) + e.retry_backoff_s * 3
    assert worst_case < 180


# ---- ranged segment fetch ----------------------------------------------------

@pytest.mark.asyncio
async def test_only_a_prefix_of_the_segment_is_requested():
    """Segment sizes vary tenfold on one gateway -- 268KB on one sandbox camera,
    2.7MB on another -- and a decoder needs only the beginning. Pulling whole
    segments made the largest cameras the ones that always timed out, which is
    backwards: they are no harder to describe, only slower to download.

    Measured live: whole segment timed out at 60s having pulled 131KB; the same
    fetch with a Range header returned 393KB in 7.1s."""
    from app.services.enrichment import SEGMENT_PREFIX_BYTES

    seen = {}

    def handler(request):
        seen[request.url.path] = request.headers.get("range")
        return httpx.Response(206, content=b"\x00" * SEGMENT_PREFIX_BYTES)

    e = StreamEnricher(transport=httpx.MockTransport(handler), retry_backoff_s=0)
    await e._fetch("https://gw.test/a.ts", None, None, None,
                   prefix_bytes=SEGMENT_PREFIX_BYTES)
    assert seen["/a.ts"] == f"bytes=0-{SEGMENT_PREFIX_BYTES - 1}"


@pytest.mark.asyncio
async def test_a_gateway_ignoring_the_range_header_still_works():
    """Nothing depends on the range being honoured; it is an optimisation."""
    from app.services.enrichment import SEGMENT_PREFIX_BYTES

    handler = lambda r: httpx.Response(200, content=b"\x01" * (SEGMENT_PREFIX_BYTES * 3))
    e = StreamEnricher(transport=httpx.MockTransport(handler), retry_backoff_s=0)
    body = await e._fetch("https://gw.test/a.ts", None, None, None,
                          prefix_bytes=SEGMENT_PREFIX_BYTES)
    # Trimmed locally, so a 2.7MB body is not carried through decryption.
    assert len(body) == SEGMENT_PREFIX_BYTES


@pytest.mark.asyncio
async def test_a_ranged_encrypted_segment_is_truncated_to_whole_blocks():
    """A ranged fetch lands mid-block. CBC decrypts from the start regardless,
    so the remainder is dropped rather than raising."""
    from app.services.enrichment import _KEY_CACHE

    _KEY_CACHE.clear()

    def handler(request):
        if request.url.path.endswith("enc.key"):
            return httpx.Response(200, content=b"k" * 16)
        # Deliberately not a multiple of 16.
        return httpx.Response(206, content=b"\x00" * 1001)

    e = StreamEnricher(transport=httpx.MockTransport(handler), retry_backoff_s=0)
    body = await e._segment_bytes(
        "https://gw.test/cam01/index.m3u8", parse_manifest(SENTINEL), None, None, None
    )
    assert len(body) <= 992  # 62 whole blocks
    _KEY_CACHE.clear()


@pytest.mark.asyncio
async def test_the_manifest_fetch_also_retries():
    """It used to have its own inline request with a shorter timeout and no
    retry, which made it the failure point on an erratic gateway -- the segment
    path was hardened and the step before it was not."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, text=MASTER)

    e = StreamEnricher(
        transport=httpx.MockTransport(handler), retry_backoff_s=0, probe_media=False
    )
    m = await e.enrich("https://gw.test/i.m3u8")
    assert m.resolution == "1920x1080"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_a_transport_error_names_the_stage_that_failed():
    """httpx timeout exceptions carry an empty message, so a bare repr reads as
    "ReadTimeout: " and tells an operator nothing."""
    def boom(request):
        raise httpx.ReadTimeout("")

    e = StreamEnricher(transport=httpx.MockTransport(boom), retry_backoff_s=0)
    m = await e.enrich("https://gw.test/i.m3u8")
    assert m.error == "ReadTimeout fetching manifest"
