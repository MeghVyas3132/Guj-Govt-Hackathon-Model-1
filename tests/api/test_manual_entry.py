"""Manual camera entry.

A named deliverable: "bulk and manual onboarding demonstration". The point of
routing it through IngestionService rather than writing the row directly is that
a camera typed into a form gets exactly the same validation, normalisation,
vocabulary resolution and dedupe as one arriving from a CSV or a vendor API.
"""

import pytest
from sqlalchemy import select

from app.models.camera import Camera


def payload(dept_id, **overrides):
    body = {
        "department_id": str(dept_id),
        "external_camera_id": "MAN-001",
        "name": "Nehru Bridge East Approach",
        "latitude": 23.0225,
        "longitude": 72.5714,
        "camera_type": "fixed",
        "azimuth_deg": 135.0,
        "fov_deg": 90.0,
        "range_m": 100.0,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_a_camera_can_be_created_through_the_api(
    api_client, session, seeded_department
):
    response = await api_client.post(
        "/api/v1/cameras", json=payload(seeded_department)
    )
    assert response.status_code == 201
    body = response.json()
    assert body["external_camera_id"] == "MAN-001"
    assert body["camera_uid"].startswith("GJ-")
    assert body["latitude"] == pytest.approx(23.0225)
    assert body["longitude"] == pytest.approx(72.5714)

    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.source_type == "manual"


@pytest.mark.asyncio
async def test_the_response_is_the_same_shape_the_list_endpoint_returns(
    api_client, seeded_department
):
    """Other teams code against CameraRead; creation must not invent a variant."""
    created = (
        await api_client.post("/api/v1/cameras", json=payload(seeded_department))
    ).json()
    listed = (await api_client.get("/api/v1/cameras")).json()["items"][0]
    assert set(created) == set(listed)


@pytest.mark.asyncio
async def test_a_point_outside_gujarat_is_rejected_with_the_reason(
    api_client, seeded_department
):
    response = await api_client.post(
        "/api/v1/cameras",
        json=payload(seeded_department, latitude=28.6139, longitude=77.2090),
    )
    assert response.status_code == 422
    assert "outside_gujarat" in response.text


@pytest.mark.asyncio
async def test_a_missing_required_field_is_rejected(api_client, seeded_department):
    body = payload(seeded_department)
    del body["latitude"]
    assert (await api_client.post("/api/v1/cameras", json=body)).status_code == 422


@pytest.mark.asyncio
async def test_an_out_of_range_azimuth_is_rejected(api_client, seeded_department):
    response = await api_client.post(
        "/api/v1/cameras", json=payload(seeded_department, azimuth_deg=400)
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_creating_the_same_external_id_twice_updates_rather_than_duplicating(
    api_client, session, seeded_department
):
    """Same dedupe key as every other path: a re-submitted form is not a new camera."""
    await api_client.post("/api/v1/cameras", json=payload(seeded_department))
    second = await api_client.post(
        "/api/v1/cameras", json=payload(seeded_department, name="Renamed")
    )
    assert second.status_code == 201

    cameras = (await session.execute(select(Camera))).scalars().all()
    assert len(cameras) == 1
    assert cameras[0].name == "Renamed"


@pytest.mark.asyncio
async def test_an_unknown_department_is_404(api_client):
    response = await api_client.post(
        "/api/v1/cameras",
        json=payload("00000000-0000-0000-0000-000000000000"),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_unknown_camera_type_is_preserved_like_every_other_path(
    api_client, session, seeded_department
):
    """The form is not a bypass: vocabulary rules apply here too."""
    await api_client.post(
        "/api/v1/cameras",
        json=payload(seeded_department, camera_type="fisheye-360"),
    )
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.camera_type == "other"
    assert camera.metadata_["unmapped_camera_type"] == "fisheye-360"


@pytest.mark.asyncio
async def test_extra_fields_land_in_metadata(api_client, session, seeded_department):
    await api_client.post(
        "/api/v1/cameras",
        json=payload(seeded_department, metadata={"pole_number": "P-77"}),
    )
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.metadata_["pole_number"] == "P-77"
