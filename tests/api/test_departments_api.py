"""Department registration and versioned field-mapping config.

Onboarding a new department has to be an HTTP call, not a psql insert: the README,
the onboarding guide and the demo script all start here.
"""

import pytest


@pytest.mark.asyncio
async def test_create_and_list_a_department(api_client):
    created = await api_client.post(
        "/api/v1/departments",
        json={"code": "RTO", "name": "Regional Transport Office"},
    )
    assert created.status_code == 201
    assert created.json()["code"] == "RTO"

    listed = await api_client.get("/api/v1/departments")
    assert any(d["code"] == "RTO" for d in listed.json())


@pytest.mark.asyncio
async def test_duplicate_code_is_rejected(api_client):
    payload = {"code": "RTO", "name": "Regional Transport Office"}
    await api_client.post("/api/v1/departments", json=payload)
    duplicate = await api_client.post("/api/v1/departments", json=payload)
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_field_mapping_can_be_set_and_read_back(api_client):
    dept = (
        await api_client.post(
            "/api/v1/departments", json={"code": "RTO", "name": "RTO"}
        )
    ).json()

    config = {
        "column_map": {
            "veh_cam_id": "external_camera_id",
            "y": "latitude",
            "x": "longitude",
        },
        "value_maps": {"status": {"RUNNING": "online", "HALTED": "offline"}},
    }
    put = await api_client.put(
        f"/api/v1/departments/{dept['id']}/field-mappings", json={"config": config}
    )
    assert put.status_code == 200
    assert put.json()["version"] == 1

    fetched = await api_client.get(f"/api/v1/departments/{dept['id']}/field-mappings")
    assert fetched.json()["config"]["value_maps"]["status"]["RUNNING"] == "online"


@pytest.mark.asyncio
async def test_updating_a_mapping_creates_a_new_version(api_client):
    """Versioning rather than overwrite is what makes a past import reproducible:
    `cameras.field_mapping_version` records which config a row was translated under,
    so overwriting version 1 would silently rewrite history."""
    dept = (
        await api_client.post("/api/v1/departments", json={"code": "RTO", "name": "RTO"})
    ).json()

    for _ in range(2):
        await api_client.put(
            f"/api/v1/departments/{dept['id']}/field-mappings",
            json={"config": {"column_map": {"a": "external_camera_id"}}},
        )

    latest = await api_client.get(f"/api/v1/departments/{dept['id']}/field-mappings")
    assert latest.json()["version"] == 2


@pytest.mark.asyncio
async def test_the_older_version_is_still_readable_after_an_update(api_client, session):
    """The whole point of versioning: v1 survives, it is not replaced.

    `api_client` and `session` share one session, so the rows the route wrote are
    visible here without a second connection.
    """
    from sqlalchemy import select

    from app.models.field_mapping import FieldMapping

    dept = (
        await api_client.post("/api/v1/departments", json={"code": "RTO", "name": "RTO"})
    ).json()
    for key in ("old", "new"):
        await api_client.put(
            f"/api/v1/departments/{dept['id']}/field-mappings",
            json={"config": {"column_map": {key: "external_camera_id"}}},
        )

    rows = (
        (
            await session.execute(
                select(FieldMapping)
                .where(FieldMapping.department_id == dept["id"])
                .order_by(FieldMapping.version)
            )
        )
        .scalars()
        .all()
    )
    assert [r.version for r in rows] == [1, 2]
    assert list(rows[0].config["column_map"]) == ["old"]
    assert list(rows[1].config["column_map"]) == ["new"]


@pytest.mark.asyncio
async def test_field_mapping_for_an_unknown_department_is_404(api_client):
    unknown = "00000000-0000-0000-0000-0000000000ff"
    response = await api_client.get(f"/api/v1/departments/{unknown}/field-mappings")
    assert response.status_code == 404
