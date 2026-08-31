import pytest


@pytest.mark.asyncio
async def test_preview_reports_row_errors_without_writing(api_client, seeded_department):
    # Row 2 is New Delhi: a well-formed coordinate that is outside Gujarat, so the
    # bounding-box rule is what rejects it. (99.9 would fail range parsing first.)
    csv_bytes = b"cam_id,lat,lng\nA-1,23.02,72.57\nA-2,28.6139,77.2090\n"
    response = await api_client.post(
        f"/api/v1/onboarding/preview?department_id={seeded_department}",
        files={"file": ("amc.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["failed"] == 1
    assert body["rows"][1]["errors"][0]["code"] == "outside_gujarat"

    listing = await api_client.get("/api/v1/cameras")
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_import_commits_valid_rows(api_client, seeded_department):
    csv_bytes = b"cam_id,lat,lng\nA-1,23.02,72.57\n"
    response = await api_client.post(
        f"/api/v1/onboarding/import?department_id={seeded_department}",
        files={"file": ("amc.csv", csv_bytes, "text/csv")},
    )
    assert response.json()["created"] == 1

    listing = await api_client.get("/api/v1/cameras")
    assert listing.json()["total"] == 1


@pytest.mark.asyncio
async def test_bulk_api_applies_the_same_rules_as_csv(api_client, seeded_department):
    body = [
        {
            "department_id": str(seeded_department),
            "external_camera_id": "API-1",
            "latitude": 23.0225,
            "longitude": 72.5714,
            "metadata": {"pole_number": "P-77"},
        },
        {
            "department_id": str(seeded_department),
            "external_camera_id": "API-2",
            "latitude": 28.6139,
            "longitude": 77.2090,
        },
    ]
    response = await api_client.post(
        f"/api/v1/onboarding/bulk?department_id={seeded_department}", json=body
    )
    report = response.json()
    assert report["created"] == 1
    assert report["failed"] == 1
    # Same rule, same error code as the CSV path — the source does not change the rules.
    assert report["rows"][1]["errors"][0]["code"] == "outside_gujarat"

    camera = (await api_client.get("/api/v1/cameras")).json()["items"][0]
    assert camera["source_type"] == "api"
    # The caller's metadata is stored flat, and the routing-only department_id
    # does not leak into it.
    assert camera["metadata"] == {"pole_number": "P-77"}
