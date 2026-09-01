"""The audit trail."""

import pytest
from sqlalchemy import func, select

from app.models.user import AuditLog


def camera_payload(dept_id, **overrides):
    body = {
        "department_id": str(dept_id),
        "external_camera_id": "AUD-1",
        "name": "Original Name",
        "latitude": 23.0225,
        "longitude": 72.5714,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_creating_a_camera_is_recorded(api_client, session, seeded_department):
    await api_client.post("/api/v1/cameras", json=camera_payload(seeded_department))

    entry = (await session.execute(select(AuditLog))).scalar_one()
    assert entry.action == "camera.created"
    assert entry.actor_type == "user"
    assert entry.actor_label == "root@gujarat.gov.in"
    assert entry.before is None
    assert entry.after["external_camera_id"] == "AUD-1"


@pytest.mark.asyncio
async def test_an_update_records_both_states(api_client, session, seeded_department):
    """Before-and-after, not a message: a reviewer can answer what a camera looked
    like last week without replaying the whole history."""
    await api_client.post("/api/v1/cameras", json=camera_payload(seeded_department))
    await api_client.post(
        "/api/v1/cameras", json=camera_payload(seeded_department, name="Renamed")
    )

    entries = (
        await session.execute(select(AuditLog).order_by(AuditLog.at))
    ).scalars().all()
    assert [e.action for e in entries] == ["camera.created", "camera.updated"]
    assert entries[1].before["name"] == "Original Name"
    assert entries[1].after["name"] == "Renamed"


@pytest.mark.asyncio
async def test_a_no_op_writes_nothing(api_client, session, seeded_department):
    """A trail that gains thousands of 'nothing changed' rows every night from a
    departmental sync is a trail nobody reads."""
    for _ in range(4):
        await api_client.post(
            "/api/v1/cameras", json=camera_payload(seeded_department)
        )

    count = (
        await session.execute(select(func.count()).select_from(AuditLog))
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_the_trail_is_queryable_through_the_api(
    api_client, seeded_department
):
    await api_client.post("/api/v1/cameras", json=camera_payload(seeded_department))

    entries = (await api_client.get("/api/v1/admin/audit-logs")).json()
    assert entries[0]["action"] == "camera.created"
    assert entries[0]["actor_label"] == "root@gujarat.gov.in"


@pytest.mark.asyncio
async def test_the_trail_can_be_filtered(api_client, seeded_department):
    await api_client.post("/api/v1/cameras", json=camera_payload(seeded_department))
    await api_client.post(
        "/api/v1/admin/api-keys",
        json={"department_id": str(seeded_department), "name": "k", "scopes": []},
    )

    cameras = (
        await api_client.get("/api/v1/admin/audit-logs?entity_type=camera")
    ).json()
    assert all(e["entity_type"] == "camera" for e in cameras)
    assert len(cameras) == 1


@pytest.mark.asyncio
async def test_api_key_creation_and_revocation_are_recorded(
    api_client, seeded_department
):
    created = (
        await api_client.post(
            "/api/v1/admin/api-keys",
            json={"department_id": str(seeded_department), "name": "nightly sync",
                  "scopes": ["cameras:read"]},
        )
    ).json()
    await api_client.delete(f"/api/v1/admin/api-keys/{created['id']}")

    actions = [
        e["action"]
        for e in (await api_client.get("/api/v1/admin/audit-logs")).json()
    ]
    assert "api_key.created" in actions
    assert "api_key.revoked" in actions


@pytest.mark.asyncio
async def test_a_non_admin_cannot_read_the_audit_trail(session, seeded_department):
    from app.core.security import hash_password
    from app.models.user import User
    from tests.api.test_rbac import client_for, headers_for

    user = User(
        email="analyst2@gujarat.gov.in", full_name="A", role="analyst",
        password_hash=hash_password("x"), department_id=seeded_department,
    )
    session.add(user)
    await session.commit()

    from app.main import app

    async with await client_for(session, headers_for(user)) as client:
        assert (await client.get("/api/v1/admin/audit-logs")).status_code == 403
    app.dependency_overrides.clear()
