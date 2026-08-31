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
