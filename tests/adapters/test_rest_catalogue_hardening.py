"""Adversarial tests for the config-driven catalogue adapter.

The adapter is the part of the system that meets an environment we do not
control. Every vendor, sandbox and production gateway will return something
slightly different from the last, and a connector is a database row rather than
code -- so a malformed row must fail with a message an operator can act on,
never with an unhandled exception that aborts a whole sync.
"""

from uuid import uuid4

import httpx
import pytest

from app.adapters.rest_catalogue import RestCatalogueAdapter
from pydantic import ValidationError

from app.schemas.connector import AuthConfig, ConnectorConfig, EndpointRule

URL = "https://example.test/cameras.json"
DEPT = uuid4()


def adapter(config, handler, secret=None):
    return RestCatalogueAdapter(
        config, secret=secret, transport=httpx.MockTransport(handler), code="src"
    )


def responds(payload, status=200, **kw):
    return lambda request: httpx.Response(status, json=payload, **kw)


def raises(exc):
    def handler(request):
        raise exc

    return handler


# --------------------------------------------------------------------------
# Payload shapes. Six vendors, six ideas of what a camera list looks like.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_bare_array_is_read():
    a = adapter(ConnectorConfig(catalogue_url=URL), responds([{"id": "c1"}]))
    assert len(await a.fetch(DEPT)) == 1


@pytest.mark.parametrize("root", ["cameras", "items", "data", "results"])
@pytest.mark.asyncio
async def test_the_common_wrapper_keys_are_found_without_configuration(root):
    a = adapter(ConnectorConfig(catalogue_url=URL), responds({root: [{"id": "c1"}]}))
    assert len(await a.fetch(DEPT)) == 1


@pytest.mark.asyncio
async def test_a_deeply_nested_root_path_resolves():
    body = {"result": {"payload": {"devices": [{"id": "c1"}]}}}
    config = ConnectorConfig(catalogue_url=URL, root_path="result.payload.devices")
    assert len(await adapter(config, responds(body)).fetch(DEPT)) == 1


@pytest.mark.asyncio
async def test_an_explicit_root_path_beats_a_common_key():
    """A payload with both must follow the operator's configuration, not a guess."""
    body = {"cameras": [{"id": "wrong"}], "mine": [{"id": "right"}]}
    config = ConnectorConfig(catalogue_url=URL, root_path="mine")
    records = await adapter(config, responds(body)).fetch(DEPT)
    assert records[0].payload["id"] == "right"


@pytest.mark.asyncio
async def test_an_empty_catalogue_is_not_an_error():
    """A department with no cameras yet must sync to zero, not fail."""
    assert await adapter(ConnectorConfig(catalogue_url=URL), responds([])).fetch(DEPT) == []


@pytest.mark.asyncio
async def test_an_empty_wrapped_catalogue_is_not_an_error():
    a = adapter(ConnectorConfig(catalogue_url=URL), responds({"cameras": []}))
    assert await a.fetch(DEPT) == []


@pytest.mark.asyncio
async def test_an_unrecognised_object_names_the_keys_it_saw():
    """The operator has to be able to work out what root_path to set."""
    a = adapter(ConnectorConfig(catalogue_url=URL), responds({"devices": [{"id": "c1"}]}))
    with pytest.raises(ValueError, match="devices"):
        await a.fetch(DEPT)


@pytest.mark.parametrize("raw", [b"null", b"42", b'"a string"', b"true"])
@pytest.mark.asyncio
async def test_a_scalar_payload_is_rejected_with_its_type(raw):
    # Sent as raw bytes: httpx treats json=None as "no body" rather than a null.
    a = adapter(ConnectorConfig(catalogue_url=URL), lambda r: httpx.Response(200, content=raw))
    with pytest.raises(ValueError, match="Unrecognised catalogue payload"):
        await a.fetch(DEPT)


@pytest.mark.asyncio
async def test_a_root_path_through_a_list_is_rejected_not_crashed():
    body = {"a": [{"b": [{"id": "c1"}]}]}
    config = ConnectorConfig(catalogue_url=URL, root_path="a.b")
    with pytest.raises(ValueError, match="does not resolve"):
        await adapter(config, responds(body)).fetch(DEPT)


@pytest.mark.asyncio
async def test_a_root_path_that_misses_is_rejected():
    config = ConnectorConfig(catalogue_url=URL, root_path="result.cameras")
    with pytest.raises(ValueError, match="did not yield a list"):
        await adapter(config, responds({"result": {}})).fetch(DEPT)


@pytest.mark.asyncio
async def test_a_root_path_onto_a_scalar_is_rejected():
    config = ConnectorConfig(catalogue_url=URL, root_path="count")
    with pytest.raises(ValueError, match="did not yield a list"):
        await adapter(config, responds({"count": 7})).fetch(DEPT)


@pytest.mark.asyncio
async def test_a_list_of_bare_strings_is_rejected_with_a_readable_error():
    """Some catalogues return ids only. That cannot be onboarded, but it must say
    so rather than raising AttributeError from inside a comprehension."""
    a = adapter(ConnectorConfig(catalogue_url=URL), responds(["cam01", "cam02"]))
    with pytest.raises(ValueError, match="entry"):
        await a.fetch(DEPT)


@pytest.mark.asyncio
async def test_a_list_of_nulls_is_rejected_with_a_readable_error():
    a = adapter(ConnectorConfig(catalogue_url=URL), responds([None]))
    with pytest.raises(ValueError, match="entry"):
        await a.fetch(DEPT)


@pytest.mark.asyncio
async def test_a_list_of_lists_is_rejected_with_a_readable_error():
    a = adapter(ConnectorConfig(catalogue_url=URL), responds([["id", "c1"]]))
    with pytest.raises(ValueError, match="entry"):
        await a.fetch(DEPT)


# --------------------------------------------------------------------------
# Transport failures. Every one of these happens in a government network.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503, 504])
@pytest.mark.asyncio
async def test_error_statuses_raise_rather_than_returning_empty(status):
    """Returning [] on a 500 would let a sync report "0 cameras" and look like a
    source that had been emptied, which is how a registry silently goes stale."""
    a = adapter(ConnectorConfig(catalogue_url=URL), responds([], status=status))
    with pytest.raises(httpx.HTTPStatusError):
        await a.fetch(DEPT)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
@pytest.mark.asyncio
async def test_redirects_are_not_followed(status):
    """An expired session redirects to a login page. Following it would parse the
    login HTML as a catalogue; not following turns it into a loud failure."""
    handler = lambda request: httpx.Response(status, headers={"location": "/login"})
    a = adapter(ConnectorConfig(catalogue_url=URL), handler)
    with pytest.raises(httpx.HTTPStatusError):
        await a.fetch(DEPT)


@pytest.mark.asyncio
async def test_malformed_json_raises():
    handler = lambda request: httpx.Response(200, content=b'{"cameras": [')
    with pytest.raises(ValueError):
        await adapter(ConnectorConfig(catalogue_url=URL), handler).fetch(DEPT)


@pytest.mark.asyncio
async def test_an_html_body_raises_rather_than_being_parsed():
    handler = lambda request: httpx.Response(200, content=b"<html>login</html>")
    with pytest.raises(ValueError):
        await adapter(ConnectorConfig(catalogue_url=URL), handler).fetch(DEPT)


@pytest.mark.asyncio
async def test_an_empty_body_raises():
    handler = lambda request: httpx.Response(200, content=b"")
    with pytest.raises(ValueError):
        await adapter(ConnectorConfig(catalogue_url=URL), handler).fetch(DEPT)


@pytest.mark.asyncio
async def test_a_timeout_propagates():
    a = adapter(ConnectorConfig(catalogue_url=URL), raises(httpx.ConnectTimeout("slow")))
    with pytest.raises(httpx.TimeoutException):
        await a.fetch(DEPT)


@pytest.mark.asyncio
async def test_a_refused_connection_propagates():
    a = adapter(ConnectorConfig(catalogue_url=URL), raises(httpx.ConnectError("refused")))
    with pytest.raises(httpx.ConnectError):
        await a.fetch(DEPT)


@pytest.mark.asyncio
async def test_a_read_error_midstream_propagates():
    a = adapter(ConnectorConfig(catalogue_url=URL), raises(httpx.ReadError("reset")))
    with pytest.raises(httpx.ReadError):
        await a.fetch(DEPT)


@pytest.mark.asyncio
async def test_the_configured_timeout_reaches_the_client():
    seen = {}

    def handler(request):
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json=[])

    config = ConnectorConfig(catalogue_url=URL, request_timeout_s=2.5)
    await adapter(config, handler).fetch(DEPT)
    assert seen["timeout"]["connect"] == 2.5


# --------------------------------------------------------------------------
# Auth. Five schemes, and the failure mode where a secret is missing.
# --------------------------------------------------------------------------

def capture():
    seen = {}

    def handler(request):
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json=[])

    return seen, handler


@pytest.mark.asyncio
async def test_a_cookie_scheme_sends_the_named_cookie():
    seen, handler = capture()
    config = ConnectorConfig(
        catalogue_url=URL,
        auth=AuthConfig(type="cookie", name="sentinel", credential_ref="pw"),
    )
    await adapter(config, handler, secret="abc").fetch(DEPT)
    assert seen["headers"]["cookie"] == "sentinel=abc"


@pytest.mark.asyncio
async def test_a_header_scheme_sends_the_named_header():
    seen, handler = capture()
    config = ConnectorConfig(
        catalogue_url=URL,
        auth=AuthConfig(type="header", name="X-Api-Key", credential_ref="pw"),
    )
    await adapter(config, handler, secret="abc").fetch(DEPT)
    assert seen["headers"]["x-api-key"] == "abc"


@pytest.mark.asyncio
async def test_a_bearer_scheme_sends_the_authorization_header():
    seen, handler = capture()
    config = ConnectorConfig(
        catalogue_url=URL, auth=AuthConfig(type="bearer", credential_ref="pw")
    )
    await adapter(config, handler, secret="tok").fetch(DEPT)
    assert seen["headers"]["authorization"] == "Bearer tok"


@pytest.mark.asyncio
async def test_a_basic_scheme_base64_encodes_the_secret():
    seen, handler = capture()
    config = ConnectorConfig(
        catalogue_url=URL, auth=AuthConfig(type="basic", credential_ref="pw")
    )
    await adapter(config, handler, secret="user:pass").fetch(DEPT)
    assert seen["headers"]["authorization"] == "Basic dXNlcjpwYXNz"


@pytest.mark.asyncio
async def test_no_auth_sends_no_credential_headers():
    seen, handler = capture()
    await adapter(ConnectorConfig(catalogue_url=URL), handler, secret="leak").fetch(DEPT)
    assert "authorization" not in seen["headers"]
    assert "cookie" not in seen["headers"]


@pytest.mark.asyncio
async def test_a_non_latin1_secret_names_the_credential_at_fault():
    """HTTP header values are latin-1. Without this the operator sees a
    UnicodeEncodeError from inside httpx that names no credential."""
    seen, handler = capture()
    config = ConnectorConfig(
        catalogue_url=URL, auth=AuthConfig(type="bearer", credential_ref="vendor_pw")
    )
    with pytest.raises(ValueError, match="vendor_pw"):
        await adapter(config, handler, secret="pass-ગુજરાત").fetch(DEPT)


# --------------------------------------------------------------------------
# Endpoint rules -- the templating that makes an id-only catalogue usable.
# --------------------------------------------------------------------------

HLS = EndpointRule(
    protocol="hls", url_key="hls", url_template="https://cdn.test/{id}/i.m3u8",
    reachability="public_cdn", is_primary=True,
)


@pytest.mark.asyncio
async def test_a_url_in_the_catalogue_wins_over_the_template():
    """The source is authoritative where it speaks."""
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=[HLS])
    records = await adapter(config, responds([{"id": "c1", "hls": "https://real/x"}])).fetch(DEPT)
    assert records[0].payload["_stream_endpoints"][0]["url"] == "https://real/x"


@pytest.mark.asyncio
async def test_the_template_fills_in_when_the_catalogue_omits_the_url():
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=[HLS])
    records = await adapter(config, responds([{"id": "c1"}])).fetch(DEPT)
    assert records[0].payload["_stream_endpoints"][0]["url"] == "https://cdn.test/c1/i.m3u8"


@pytest.mark.parametrize("empty", ["", None])
@pytest.mark.asyncio
async def test_an_empty_url_in_the_catalogue_falls_back_to_the_template(empty):
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=[HLS])
    records = await adapter(config, responds([{"id": "c1", "hls": empty}])).fetch(DEPT)
    assert records[0].payload["_stream_endpoints"][0]["url"].startswith("https://cdn.test")


@pytest.mark.asyncio
async def test_a_template_containing_other_braces_does_not_crash_the_sync():
    """A vendor URL with an unfillable placeholder is rejected when the connector
    is saved, so it can never reach a sync at all."""
    with pytest.raises(ValidationError, match="cannot be filled"):
        EndpointRule(
            protocol="hls", url_template="https://cdn/{id}/{quality}/i.m3u8",
            reachability="public_cdn",
        )


@pytest.mark.asyncio
async def test_a_legacy_unfillable_template_skips_one_endpoint_not_the_camera():
    """Defence in depth for rows written before the validator existed."""
    rule = EndpointRule(protocol="hls", url_template="https://cdn/{id}", reachability="public_cdn")
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=[rule, HLS])
    object.__setattr__(rule, "url_template", "https://cdn/{id}/{quality}")
    records = await adapter(config, responds([{"id": "c1"}])).fetch(DEPT)
    assert [e["protocol"] for e in records[0].payload["_stream_endpoints"]] == ["hls"]


@pytest.mark.asyncio
async def test_a_template_is_skipped_when_no_id_can_be_found():
    rule = EndpointRule(
        protocol="hls", url_template="https://cdn/{id}/i.m3u8", reachability="public_cdn"
    )
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=[rule])
    records = await adapter(config, responds([{"name": "no id here"}])).fetch(DEPT)
    assert records[0].payload["_stream_endpoints"] == []


@pytest.mark.asyncio
async def test_id_keys_are_tried_in_order():
    config = ConnectorConfig(catalogue_url=URL, id_keys=["uid", "id"])
    records = await adapter(config, responds([{"id": "b", "uid": "a"}])).fetch(DEPT)
    assert records[0].source_ref == "src:a"


@pytest.mark.asyncio
async def test_id_keys_skip_empty_values_and_fall_through():
    config = ConnectorConfig(catalogue_url=URL, id_keys=["uid", "id"])
    records = await adapter(config, responds([{"uid": "", "id": "b"}])).fetch(DEPT)
    assert records[0].source_ref == "src:b"


@pytest.mark.asyncio
async def test_a_numeric_id_is_stringified():
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds([{"id": 17}])).fetch(DEPT)
    assert records[0].source_ref == "src:17"


@pytest.mark.asyncio
async def test_a_zero_id_is_not_treated_as_missing():
    """`if not value` would drop camera 0. The check must be against None and ''."""
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds([{"id": 0}])).fetch(DEPT)
    assert records[0].source_ref == "src:0"


@pytest.mark.asyncio
async def test_several_rules_produce_several_endpoints():
    rules = [
        HLS,
        EndpointRule(protocol="rtsp", url_template="rtsp://h/{id}", reachability="direct_ip"),
        EndpointRule(protocol="whep", url_template="http://h/{id}/whep", reachability="direct_ip"),
    ]
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=rules)
    records = await adapter(config, responds([{"id": "c1"}])).fetch(DEPT)
    assert [e["protocol"] for e in records[0].payload["_stream_endpoints"]] == ["hls", "rtsp", "whep"]


@pytest.mark.asyncio
async def test_a_consumed_url_key_is_not_also_copied_into_metadata():
    """A second copy in metadata would go stale the moment the vendor changed it."""
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=[HLS])
    records = await adapter(config, responds([{"id": "c1", "hls": "u"}])).fetch(DEPT)
    assert "hls" not in records[0].payload


@pytest.mark.asyncio
async def test_rule_flags_are_carried_onto_the_endpoint():
    rule = EndpointRule(
        protocol="hls", url_template="https://c/{id}", reachability="public_cdn",
        requires_auth=True, credential_ref="pw", is_primary=True,
    )
    config = ConnectorConfig(catalogue_url=URL, endpoint_rules=[rule])
    endpoint = (await adapter(config, responds([{"id": "c1"}])).fetch(DEPT))[0].payload["_stream_endpoints"][0]
    assert (endpoint["requires_auth"], endpoint["credential_ref"], endpoint["is_primary"]) == (True, "pw", True)


# --------------------------------------------------------------------------
# Content fidelity and scale.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gujarati_names_survive_intact():
    """Department data will arrive in Gujarati script; a mojibake name is a camera
    nobody can search for again."""
    name = "ચીમનભાઈ પુલ"
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds([{"id": "c1", "name": name}])).fetch(DEPT)
    assert records[0].payload["name"] == name


@pytest.mark.asyncio
async def test_nested_vendor_objects_are_preserved_for_metadata():
    entry = {"id": "c1", "specs": {"codec": "h265", "fps": 25}, "tags": ["ptz", "anpr"]}
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds([entry])).fetch(DEPT)
    assert records[0].payload["specs"] == {"codec": "h265", "fps": 25}
    assert records[0].payload["tags"] == ["ptz", "anpr"]


@pytest.mark.asyncio
async def test_null_valued_fields_are_preserved_rather_than_dropped():
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds([{"id": "c1", "lat": None}])).fetch(DEPT)
    assert "lat" in records[0].payload


@pytest.mark.asyncio
async def test_duplicate_ids_are_passed_through_for_the_pipeline_to_resolve():
    """Deduplication belongs downstream where the database is; the adapter must not
    silently drop a row and make the counts disagree."""
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds([{"id": "c1"}, {"id": "c1"}])).fetch(DEPT)
    assert len(records) == 2


@pytest.mark.asyncio
async def test_a_large_catalogue_is_handled():
    body = [{"id": f"c{i}", "name": f"Camera {i}"} for i in range(5000)]
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds(body)).fetch(DEPT)
    assert len(records) == 5000
    assert records[-1].source_ref == "src:c4999"


@pytest.mark.asyncio
async def test_every_record_carries_the_requested_department():
    dept = uuid4()
    records = await adapter(ConnectorConfig(catalogue_url=URL), responds([{"id": "c1"}])).fetch(dept)
    assert records[0].department_id == dept
