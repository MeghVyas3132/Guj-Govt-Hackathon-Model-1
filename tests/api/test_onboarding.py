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
