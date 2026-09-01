"""Onboarding a source is configuration, not code.

These tests exist to prove one claim: a department nobody anticipated can be
onboarded through the API alone -- its own auth scheme, its own JSON shape, its
own protocol names -- with no vendor name anywhere in the application.
"""

import httpx
import pytest
from sqlalchemy import select

from app.models.camera import Camera
from app.models.department import Department
from app.models.field_mapping import FieldMapping


@pytest.fixture
async def department(session):
    dept = Department(code="RTO", name="Regional Transport Office")
    session.add(dept)
    await session.flush()
    session.add(
        FieldMapping(
            department_id=dept.id,
            version=1,
            config={
                "column_map": {
                    "veh_cam_id": "external_camera_id",
                    "y": "latitude",
                    "x": "longitude",
                    "kind": "camera_type",
                },
                # value_maps keys are CANONICAL field names: column_map runs first,
                # so by this point "kind" is already "camera_type".
                "value_maps": {"camera_type": {"NUMBERPLATE": "anpr"}},
            },
        )
    )
    await session.commit()
    return dept


def connector_payload(dept_id, **overrides):
    payload = {
        "code": "rto",
        "name": "RTO checkpoint catalogue",
        "department_id": str(dept_id),
        "config": {
            "catalogue_url": "https://rto.example/api/v2/devices",
            "auth": {"type": "header", "name": "X-API-Key", "credential_ref": "rto_key"},
            "root_path": "payload.devices",
            "id_keys": ["veh_cam_id"],
            "endpoint_rules": [
                {
                    "protocol": "onvif",
                    "url_template": "http://10.20.0.1/onvif/{id}",
                    "reachability": "lan_only",
                    "requires_auth": True,
                    "credential_ref": "rto_onvif",
                    "is_primary": True,
                }
            ],
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_a_connector_can_be_created_and_listed(api_client, department):
    response = await api_client.post(
        "/api/v1/connectors", json=connector_payload(department.id)
    )
    assert response.status_code == 201
    assert response.json()["code"] == "rto"

    listed = (await api_client.get("/api/v1/connectors")).json()
    assert [c["code"] for c in listed] == ["rto"]


@pytest.mark.asyncio
async def test_a_malformed_config_is_rejected_at_write_time(api_client, department):
    """Better a 422 now than a failed sync at 3am."""
    payload = connector_payload(department.id)
    payload["config"]["endpoint_rules"][0].pop("url_template")
    response = await api_client.post("/api/v1/connectors", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_template_without_the_id_placeholder_is_rejected(api_client, department):
    payload = connector_payload(department.id)
    payload["config"]["endpoint_rules"][0]["url_template"] = "http://10.20.0.1/onvif"
    assert (await api_client.post("/api/v1/connectors", json=payload)).status_code == 422


@pytest.mark.asyncio
async def test_a_duplicate_code_is_rejected(api_client, department):
    await api_client.post("/api/v1/connectors", json=connector_payload(department.id))
    second = await api_client.post(
        "/api/v1/connectors", json=connector_payload(department.id)
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_syncing_an_unknown_connector_is_404(api_client):
    assert (await api_client.post("/api/v1/connectors/nope/sync")).status_code == 404


@pytest.mark.asyncio
async def test_a_sync_without_its_credential_says_so_rather_than_failing_obscurely(
    api_client, department
):
    await api_client.post("/api/v1/connectors", json=connector_payload(department.id))
    response = await api_client.post("/api/v1/connectors/rto/sync")
    assert response.status_code == 400
    assert "rto_key" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_credential_is_stored_and_never_echoed_back(api_client):
    response = await api_client.post(
        "/api/v1/connectors/credentials",
        json={"name": "rto_key", "value": "super-secret-value"},
    )
    assert response.status_code == 201
    assert "super-secret-value" not in response.text


@pytest.mark.asyncio
async def test_a_new_vendor_onboards_end_to_end_with_no_code(
    api_client, session, department, monkeypatch
):
    """The whole point. A vendor with its own auth header, its own nested JSON
    shape and its own protocol is onboarded by two POSTs."""
    catalogue = {
        "payload": {
            "devices": [
                {"veh_cam_id": "RTO-1", "y": 23.0225, "x": 72.5714, "kind": "NUMBERPLATE"},
                {"veh_cam_id": "RTO-2", "y": 22.3072, "x": 73.1812, "kind": "NUMBERPLATE"},
            ]
        }
    }
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=catalogue)

    # Only the transport is substituted; the route, adapter and pipeline are real.
    import app.api.v1.routers.connectors as connectors_module

    real_adapter = connectors_module.RestCatalogueAdapter

    def patched(config, secret=None, transport=None, code="source"):
        return real_adapter(
            config, secret=secret, transport=httpx.MockTransport(handler), code=code
        )

    monkeypatch.setattr(connectors_module, "RestCatalogueAdapter", patched)

    await api_client.post("/api/v1/connectors", json=connector_payload(department.id))
    await api_client.post(
        "/api/v1/connectors/credentials", json={"name": "rto_key", "value": "k123"}
    )

    report = (await api_client.post("/api/v1/connectors/rto/sync")).json()
    assert report["created"] == 2
    assert report["failed"] == 0
    assert seen["key"] == "k123"

    cameras = (await session.execute(select(Camera))).scalars().all()
    assert {c.external_camera_id for c in cameras} == {"RTO-1", "RTO-2"}
    assert all(c.camera_type == "anpr" for c in cameras)

    # And the ONVIF endpoint the connector declared, on a protocol the old
    # hardcoded adapter had no concept of.
    streams = (
        await api_client.get(f"/api/v1/cameras/{cameras[0].id}/streams")
    ).json()
    assert streams[0]["protocol"] == "onvif"
    assert streams[0]["reachability"] == "lan_only"
    assert streams[0]["url"].endswith("/onvif/RTO-1")


@pytest.mark.asyncio
async def test_re_syncing_the_same_catalogue_changes_nothing(
    api_client, department, monkeypatch
):
    catalogue = {"payload": {"devices": [{"veh_cam_id": "RTO-1", "y": 23.0, "x": 72.5}]}}
    import app.api.v1.routers.connectors as connectors_module

    real_adapter = connectors_module.RestCatalogueAdapter
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=catalogue))
    monkeypatch.setattr(
        connectors_module,
        "RestCatalogueAdapter",
        lambda config, secret=None, transport_=None, code="source": real_adapter(
            config, secret=secret, transport=transport, code=code
        ),
    )

    await api_client.post("/api/v1/connectors", json=connector_payload(department.id))
    await api_client.post(
        "/api/v1/connectors/credentials", json={"name": "rto_key", "value": "k123"}
    )

    first = (await api_client.post("/api/v1/connectors/rto/sync")).json()
    second = (await api_client.post("/api/v1/connectors/rto/sync")).json()

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped"] == 1
