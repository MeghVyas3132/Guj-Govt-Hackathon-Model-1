"""Vocabulary administration.

The claim under test: a camera type nobody anticipated becomes fully supported --
selectable, filterable and correctly modelled in the gap analysis -- by adding a
row through the API, with no deploy.
"""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_shipped_terms_are_listed(api_client):
    terms = (await api_client.get("/api/v1/vocabulary/camera_type")).json()
    codes = {t["code"] for t in terms}
    assert {"fixed", "ptz", "other"} <= codes


@pytest.mark.asyncio
async def test_dimensions_are_summarised(api_client):
    dimensions = (await api_client.get("/api/v1/vocabulary")).json()
    by_name = {d["dimension"]: d for d in dimensions}
    assert by_name["camera_type"]["active"] >= 6
    assert "status" in by_name


@pytest.mark.asyncio
async def test_inactive_terms_are_hidden_unless_asked_for(api_client):
    await api_client.post(
        "/api/v1/vocabulary/camera_type",
        json={"code": "retired-type", "label": "Retired"},
    )
    await api_client.patch(
        "/api/v1/vocabulary/camera_type/retired-type", json={"is_active": False}
    )

    active = (await api_client.get("/api/v1/vocabulary/camera_type")).json()
    assert "retired-type" not in {t["code"] for t in active}

    everything = (
        await api_client.get("/api/v1/vocabulary/camera_type?include_inactive=true")
    ).json()
    assert "retired-type" in {t["code"] for t in everything}


@pytest.mark.asyncio
async def test_an_unknown_dimension_is_rejected(api_client):
    response = await api_client.post(
        "/api/v1/vocabulary/nonsense", json={"code": "x", "label": "X"}
    )
    assert response.status_code == 422
    assert "Known:" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_duplicate_term_is_rejected(api_client):
    payload = {"code": "fixed", "label": "Fixed again"}
    assert (
        await api_client.post("/api/v1/vocabulary/camera_type", json=payload)
    ).status_code == 409


@pytest.mark.asyncio
async def test_the_fallback_term_cannot_be_deactivated(api_client):
    """Unrecognised values normalise to it; without one they would be discarded."""
    response = await api_client.patch(
        "/api/v1/vocabulary/camera_type/other", json={"is_active": False}
    )
    assert response.status_code == 422
    assert "fallback" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_new_camera_type_is_immediately_usable_end_to_end(
    api_client, session, seeded_department
):
    """The full claim, in one test: add the term, register a camera with it, filter
    by it, and confirm the gap analysis models its geometry -- no deploy."""
    from app.models.camera import Camera

    # 1. Before the term exists, the value is preserved but unclassified.
    await api_client.post(
        "/api/v1/cameras",
        json={
            "department_id": str(seeded_department), "external_camera_id": "FE-1",
            "latitude": 23.02, "longitude": 72.57, "camera_type": "fisheye-360",
        },
    )
    from sqlalchemy import select

    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.camera_type == "other"
    assert camera.metadata_["unmapped_camera_type"] == "fisheye-360"

    # 2. An operator adds the term, with its own coverage geometry.
    created = await api_client.post(
        "/api/v1/vocabulary/camera_type",
        json={
            "code": "fisheye-360", "label": "Fisheye 360",
            "coverage_range_m": 400, "is_omnidirectional": True,
        },
    )
    assert created.status_code == 201

    # 3. Re-registering now classifies it properly.
    await api_client.post(
        "/api/v1/cameras",
        json={
            "department_id": str(seeded_department), "external_camera_id": "FE-1",
            "latitude": 23.02, "longitude": 72.57, "camera_type": "fisheye-360",
        },
    )
    await session.refresh(camera)
    assert camera.camera_type == "fisheye-360"

    # 4. It is filterable.
    listed = (
        await api_client.get("/api/v1/cameras?camera_types=fisheye-360")
    ).json()
    assert listed["total"] == 1

    # 5. And the gap analysis uses its geometry: a 400m circle, not the 100m default.
    area = (
        await session.execute(
            text(
                "SELECT ST_Area(camera_footprint("
                "ST_GeogFromText('POINT(72.57 23.02)'), 'fisheye-360', 90, NULL, NULL))"
            )
        )
    ).scalar_one()
    assert 450_000 < area < 550_000  # pi * 400^2 is about 502,655


@pytest.mark.asyncio
async def test_adding_a_term_is_recorded_in_the_audit_trail(api_client):
    await api_client.post(
        "/api/v1/vocabulary/site_type", json={"code": "toll-plaza", "label": "Toll plaza"}
    )
    entries = (
        await api_client.get("/api/v1/admin/audit-logs?entity_type=vocabulary_term")
    ).json()
    assert entries[0]["action"] == "vocabulary.term_added"
    assert entries[0]["after"]["code"] == "toll-plaza"


@pytest.mark.asyncio
async def test_a_non_admin_cannot_add_terms(session, seeded_department):
    from app.core.security import hash_password
    from app.main import app
    from app.models.user import User
    from tests.api.test_rbac import client_for, headers_for

    user = User(
        email="analyst3@gujarat.gov.in", full_name="A", role="analyst",
        password_hash=hash_password("x"), department_id=seeded_department,
    )
    session.add(user)
    await session.commit()

    async with await client_for(session, headers_for(user)) as client:
        response = await client.post(
            "/api/v1/vocabulary/camera_type", json={"code": "x", "label": "X"}
        )
        assert response.status_code == 403
    app.dependency_overrides.clear()
