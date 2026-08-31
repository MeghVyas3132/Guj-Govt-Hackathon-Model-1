from uuid import uuid4

import httpx
import pytest

from app.adapters.sentinel_adapter import SentinelAdapter
from app.core.enums import SourceType

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


@pytest.mark.asyncio
async def test_fetch_maps_catalogue_entries_to_raw_records():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=CATALOGUE))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )

    records = await adapter.fetch(uuid4())

    assert len(records) == 1
    assert records[0].payload["id"] == "cam04"
    assert records[0].source_type == SourceType.ADAPTER
    assert records[0].source_ref == "sentinel:cam04"


@pytest.mark.asyncio
async def test_fetch_collects_the_three_stream_endpoints_with_reachability():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=CATALOGUE))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )

    endpoints = adapter.endpoints_for(CATALOGUE[0])

    by_protocol = {e["protocol"]: e for e in endpoints}
    assert by_protocol["hls"]["reachability"] == "public_cdn"
    assert by_protocol["hls"]["requires_auth"] is True
    assert by_protocol["rtsp"]["reachability"] == "direct_ip"
    assert by_protocol["whep"]["reachability"] == "direct_ip"


@pytest.mark.asyncio
async def test_catalogue_wrapped_in_an_object_is_also_accepted():
    payload = {"cameras": CATALOGUE}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )
    assert len(await adapter.fetch(uuid4())) == 1


@pytest.mark.asyncio
async def test_source_ref_survives_a_catalogue_that_names_its_id_differently():
    """The catalogue's id key is not guaranteed to be literally `id`, and a
    provenance label reading `sentinel:None` would be useless for tracing."""
    catalogue = [{"camera_ref": "cam07", "lat": 23.02, "lon": 72.57}]
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=catalogue))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )

    records = await adapter.fetch(uuid4())

    assert records[0].source_ref == "sentinel:cam07"


@pytest.mark.asyncio
async def test_raw_stream_urls_do_not_also_land_in_metadata():
    """Stream URLs live authoritatively in stream_endpoints. Leaving the raw hls/rtsp/
    whep keys in the payload would file a second copy into cameras.metadata via normal
    passthrough, and that copy goes stale silently on the next re-sync."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=CATALOGUE))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )

    payload = (await adapter.fetch(uuid4()))[0].payload

    assert "hls" not in payload
    assert "rtsp" not in payload
    assert "whep" not in payload
    # ...but they are present, once, on the authoritative channel.
    assert {e["protocol"] for e in payload["_stream_endpoints"]} == {"hls", "rtsp", "whep"}
    # Non-stream fields still pass through for field_mappings to resolve.
    assert payload["id"] == "cam04"
    assert payload["lat"] == 23.0225


# The real catalogue at cctv.corp8.cloud/cameras.json carries only these two fields,
# despite the integrator's guide describing location, codec, status and all three URLs.
REAL_CATALOGUE = [
    {"id": "cam01", "name": "01 Chiman bhai Bridge"},
    {"id": "cam04", "name": "04 Paldi Circle"},
]


@pytest.mark.asyncio
async def test_endpoints_are_templated_when_the_catalogue_omits_urls():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=REAL_CATALOGUE))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )

    endpoints = adapter.endpoints_for(REAL_CATALOGUE[1])

    by_protocol = {e["protocol"]: e for e in endpoints}
    assert by_protocol["hls"]["url"] == "https://cctv.corp8.cloud/cam04/index.m3u8"
    assert by_protocol["rtsp"]["url"] == "rtsp://103.250.160.189:8554/stream/cam04"
    assert by_protocol["whep"]["url"] == "http://103.250.160.189:8889/stream/cam04/whep"
    # Reachability policy is unchanged by where the URL came from.
    assert by_protocol["hls"]["reachability"] == "public_cdn"
    assert by_protocol["rtsp"]["reachability"] == "direct_ip"


@pytest.mark.asyncio
async def test_a_url_present_in_the_catalogue_wins_over_the_template():
    """If the catalogue ever grows real URLs, they are authoritative -- the guide's
    'the catalogue is the contract, the URL pattern is not' still holds where it can."""
    entry = {"id": "cam04", "hls": "https://elsewhere.example/cam04.m3u8"}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[entry]))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )

    by_protocol = {e["protocol"]: e for e in adapter.endpoints_for(entry)}

    assert by_protocol["hls"]["url"] == "https://elsewhere.example/cam04.m3u8"
    assert by_protocol["rtsp"]["url"] == "rtsp://103.250.160.189:8554/stream/cam04"


@pytest.mark.asyncio
async def test_a_display_name_is_never_used_as_a_url_identifier():
    """"01 Chiman bhai Bridge" is not a path segment; templating it would produce a
    plausible-looking URL that 404s."""
    entry = {"name": "01 Chiman bhai Bridge"}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=[entry]))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )
    assert adapter.endpoints_for(entry) == []
