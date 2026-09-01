from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.adapters.rest_catalogue import RestCatalogueAdapter
from app.core.enums import SourceType
from app.schemas.connector import AuthConfig, ConnectorConfig, EndpointRule

URL = "https://example.test/cameras.json"

# The shape the real Sentinel catalogue actually returns: id and name only.
NAME_ONLY = [
    {"id": "cam01", "name": "01 Chiman bhai Bridge"},
    {"id": "cam04", "name": "04 Paldi Circle"},
]

SENTINEL_RULES = [
    EndpointRule(
        protocol="hls", url_key="hls",
        url_template="https://cdn.test/{id}/index.m3u8",
        reachability="public_cdn", requires_auth=True,
        credential_ref="sentinel_pw", is_primary=True,
    ),
    EndpointRule(
        protocol="rtsp", url_key="rtsp",
        url_template="rtsp://10.0.0.1:8554/stream/{id}",
        reachability="direct_ip",
    ),
    EndpointRule(
        protocol="whep", url_key="whep",
        url_template="http://10.0.0.1:8889/stream/{id}/whep",
        reachability="direct_ip",
    ),
]


def adapter(config: ConnectorConfig, handler, secret: str | None = None):
    return RestCatalogueAdapter(
        config, secret=secret, transport=httpx.MockTransport(handler), code="sentinel"
    )


def responds(payload):
    return lambda request: httpx.Response(200, json=payload)


# ---- config validation ----

def test_a_minimal_connector_validates():
    config = ConnectorConfig(catalogue_url=URL)
    assert config.auth.type == "none"
    assert config.id_keys == ["id"]
    assert config.endpoint_rules == []


@pytest.mark.parametrize("auth_type", ["cookie", "header"])
def test_named_auth_schemes_require_a_name(auth_type):
    with pytest.raises(ValidationError):
        AuthConfig(type=auth_type, credential_ref="pw")


def test_every_scheme_except_none_requires_a_credential_ref():
    with pytest.raises(ValidationError):
        AuthConfig(type="bearer")


def test_bearer_needs_no_name():
    assert AuthConfig(type="bearer", credential_ref="pw").name is None


def test_an_endpoint_rule_needs_a_key_or_a_template():
    with pytest.raises(ValidationError):
        EndpointRule(protocol="hls", reachability="public_cdn")


def test_a_template_must_reference_the_id_placeholder():
    with pytest.raises(ValidationError):
        EndpointRule(
            protocol="hls", url_template="https://cdn.test/fixed.m3u8",
            reachability="public_cdn",
        )


# ---- auth schemes ----

@pytest.mark.asyncio
async def test_cookie_auth_sends_the_named_cookie():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=NAME_ONLY)

    config = ConnectorConfig(
        catalogue_url=URL,
        auth=AuthConfig(type="cookie", name="sentinel", credential_ref="pw"),
    )
    await adapter(config, handler, secret="s3cret").fetch(uuid4())
    assert seen["cookie"] == "sentinel=s3cret"


@pytest.mark.asyncio
async def test_header_auth_sends_the_named_header():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=NAME_ONLY)

    config = ConnectorConfig(
        catalogue_url=URL,
        auth=AuthConfig(type="header", name="X-API-Key", credential_ref="pw"),
    )
    await adapter(config, handler, secret="s3cret").fetch(uuid4())
    assert seen["key"] == "s3cret"


@pytest.mark.asyncio
async def test_bearer_auth_sends_an_authorization_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=NAME_ONLY)

    config = ConnectorConfig(
        catalogue_url=URL, auth=AuthConfig(type="bearer", credential_ref="pw")
    )
    await adapter(config, handler, secret="tok").fetch(uuid4())
    assert seen["auth"] == "Bearer tok"


@pytest.mark.asyncio
async def test_basic_auth_sends_a_base64_authorization_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=NAME_ONLY)

    config = ConnectorConfig(
        catalogue_url=URL, auth=AuthConfig(type="basic", credential_ref="pw")
    )
    await adapter(config, handler, secret="user:pass").fetch(uuid4())
    assert seen["auth"] == "Basic dXNlcjpwYXNz"


@pytest.mark.asyncio
async def test_no_auth_sends_no_credentials():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=NAME_ONLY)

    await adapter(ConnectorConfig(catalogue_url=URL), handler).fetch(uuid4())
    assert seen["auth"] is None
    assert seen["cookie"] is None


# ---- payload shapes ----

@pytest.mark.asyncio
async def test_a_bare_array_is_accepted():
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds(NAME_ONLY)).fetch(uuid4())
    assert len(records) == 2


@pytest.mark.asyncio
async def test_a_nested_root_path_is_followed():
    payload = {"result": {"cameras": NAME_ONLY}}
    config = ConnectorConfig(catalogue_url=URL, root_path="result.cameras")
    assert len(await adapter(config, responds(payload)).fetch(uuid4())) == 2


@pytest.mark.asyncio
async def test_a_common_wrapper_key_is_found_without_configuration():
    records = await adapter(
        ConnectorConfig(catalogue_url=URL), responds({"items": NAME_ONLY})
    ).fetch(uuid4())
    assert len(records) == 2


@pytest.mark.asyncio
async def test_an_unrecognisable_payload_raises_a_useful_error():
    with pytest.raises(ValueError, match="Unrecognised catalogue shape"):
        await adapter(ConnectorConfig(catalogue_url=URL), responds({"nope": 1})).fetch(uuid4())


@pytest.mark.asyncio
async def test_a_scalar_payload_raises_rather_than_crashing_obscurely():
    with pytest.raises(ValueError, match="Unrecognised catalogue payload"):
        await adapter(ConnectorConfig(catalogue_url=URL), responds("just a string")).fetch(uuid4())


# ---- endpoint rules ----

@pytest.mark.asyncio
async def test_endpoints_are_templated_when_the_catalogue_omits_urls():
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=SENTINEL_RULES)
    by_protocol = {
        e["protocol"]: e
        for e in adapter(config, responds(NAME_ONLY)).endpoints_for(NAME_ONLY[1])
    }
    assert by_protocol["hls"]["url"] == "https://cdn.test/cam04/index.m3u8"
    assert by_protocol["rtsp"]["url"] == "rtsp://10.0.0.1:8554/stream/cam04"
    assert by_protocol["hls"]["reachability"] == "public_cdn"
    assert by_protocol["hls"]["requires_auth"] is True
    assert by_protocol["rtsp"]["reachability"] == "direct_ip"


@pytest.mark.asyncio
async def test_a_url_in_the_catalogue_wins_over_the_template():
    entry = {"id": "cam04", "hls": "https://elsewhere.test/cam04.m3u8"}
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=SENTINEL_RULES)
    by_protocol = {
        e["protocol"]: e for e in adapter(config, responds([entry])).endpoints_for(entry)
    }
    assert by_protocol["hls"]["url"] == "https://elsewhere.test/cam04.m3u8"
    assert by_protocol["rtsp"]["url"] == "rtsp://10.0.0.1:8554/stream/cam04"


@pytest.mark.asyncio
async def test_nothing_about_hls_or_rtsp_is_baked_into_the_adapter():
    """A vendor speaking only ONVIF on a LAN is configured, not coded."""
    config = ConnectorConfig(
        catalogue_url=URL,
        endpoint_rules=[
            EndpointRule(
                protocol="onvif", url_template="http://cam/{id}/onvif",
                reachability="lan_only", requires_auth=True,
                credential_ref="onvif_pw", is_primary=True,
            )
        ],
    )
    endpoints = adapter(config, responds([{"id": "c1"}])).endpoints_for({"id": "c1"})
    assert len(endpoints) == 1
    assert endpoints[0]["protocol"] == "onvif"
    assert endpoints[0]["reachability"] == "lan_only"
    assert endpoints[0]["url"] == "http://cam/c1/onvif"
    assert endpoints[0]["credential_ref"] == "onvif_pw"


@pytest.mark.asyncio
async def test_an_entry_with_no_resolvable_id_gets_no_templated_endpoints():
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=SENTINEL_RULES)
    assert adapter(config, responds([{}])).endpoints_for({"name": "nameless"}) == []


# ---- record construction ----

@pytest.mark.asyncio
async def test_records_carry_provenance_and_the_adapter_source_type():
    config = ConnectorConfig(catalogue_url=URL)
    records = await adapter(config, responds(NAME_ONLY)).fetch(uuid4())
    assert records[0].source_type == SourceType.ADAPTER
    assert records[0].source_ref == "sentinel:cam01"


@pytest.mark.asyncio
async def test_the_id_key_is_configurable():
    config = ConnectorConfig(catalogue_url=URL, id_keys=["camera_ref", "id"])
    records = await adapter(config, responds([{"camera_ref": "cam07"}])).fetch(uuid4())
    assert records[0].source_ref == "sentinel:cam07"


@pytest.mark.asyncio
async def test_consumed_url_keys_are_not_also_filed_into_metadata():
    """Stream URLs live in stream_endpoints. A second copy in metadata goes stale."""
    entry = {"id": "cam04", "hls": "https://x/a.m3u8", "note": "keep me"}
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=SENTINEL_RULES)
    payload = (await adapter(config, responds([entry])).fetch(uuid4()))[0].payload
    assert "hls" not in payload
    assert payload["note"] == "keep me"
    assert {e["protocol"] for e in payload["_stream_endpoints"]} == {"hls", "rtsp", "whep"}
