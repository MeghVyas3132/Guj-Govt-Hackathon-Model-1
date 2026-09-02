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


# ---- bulk API onboarding: dry-run and department integrity ------------------

@pytest.mark.asyncio
async def test_bulk_supports_a_dry_run(api_client, seeded_department):
    """The CSV path has preview; the API path had no equivalent, so an
    integration could not verify a nightly export before committing to it."""
    body = [{"external_camera_id": "DRY-1", "name": "Dry run",
             "latitude": 23.03, "longitude": 72.58}]
    response = await api_client.post(
        f"/api/v1/onboarding/bulk?department_id={seeded_department}&mode=validate_only",
        json=body,
    )
    assert response.status_code == 200
    assert response.json()["created"] == 1

    # Nothing was written.
    listing = await api_client.get("/api/v1/cameras?q=DRY-1")
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_bulk_commits_by_default(api_client, seeded_department):
    """The default must stay `commit`, or every existing integration silently
    stops writing."""
    body = [{"external_camera_id": "COMMIT-1", "name": "Committed",
             "latitude": 23.03, "longitude": 72.58}]
    response = await api_client.post(
        f"/api/v1/onboarding/bulk?department_id={seeded_department}", json=body
    )
    assert response.json()["created"] == 1
    assert (await api_client.get("/api/v1/cameras?q=COMMIT-1")).json()["total"] == 1


@pytest.mark.asyncio
async def test_a_record_naming_another_department_is_rejected(
    api_client, session, seeded_department
):
    """Silently rewriting it would move someone's camera into another
    department without anyone noticing."""
    from app.models.department import Department

    other = Department(code="OTH", name="Other")
    session.add(other)
    await session.commit()

    body = [{"external_camera_id": "X-1", "name": "Wrong dept",
             "latitude": 23.03, "longitude": 72.58,
             "department_id": str(other.id)}]
    response = await api_client.post(
        f"/api/v1/onboarding/bulk?department_id={seeded_department}", json=body
    )
    assert response.status_code == 422
    assert "different department_id" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_record_may_omit_the_department_entirely(api_client, seeded_department):
    """An integrator should not have to repeat the same UUID on every row."""
    body = [{"external_camera_id": "NODEPT-1", "name": "No dept on record",
             "latitude": 23.03, "longitude": 72.58}]
    response = await api_client.post(
        f"/api/v1/onboarding/bulk?department_id={seeded_department}", json=body
    )
    assert response.status_code == 200
    assert response.json()["created"] == 1


@pytest.mark.asyncio
async def test_a_record_repeating_the_correct_department_is_accepted(
    api_client, seeded_department
):
    """Backwards compatible: callers already sending it keep working."""
    body = [{"external_camera_id": "SAMEDEPT-1", "name": "Same dept",
             "latitude": 23.03, "longitude": 72.58,
             "department_id": str(seeded_department)}]
    response = await api_client.post(
        f"/api/v1/onboarding/bulk?department_id={seeded_department}", json=body
    )
    assert response.status_code == 200
    assert response.json()["created"] == 1


@pytest.mark.asyncio
async def test_fifty_heterogeneous_cameras_onboard_and_are_idempotent(
    api_client, seeded_department
):
    """The organisers' Step 4 test case: onboard ~50 cameras. Mixed casing and
    unknown vocabulary values, because a real export has both."""
    types = ["fixed", "PTZ", "ptz", "Dome", "ANPR", "thermal", "quantum-array"]
    body = [
        {
            "external_camera_id": f"TC{i:03d}",
            "name": f"Test case camera {i}",
            "latitude": 22.8 + (i % 40) * 0.02,
            "longitude": 72.3 + (i % 40) * 0.02,
            "camera_type": types[i % len(types)],
            "install_date": "2019-04-01" if i % 3 == 0 else "2024-11-15",
            "retention_days": 7 if i % 7 == 0 else 90,
        }
        for i in range(1, 51)
    ]
    url = f"/api/v1/onboarding/bulk?department_id={seeded_department}"

    first = (await api_client.post(url, json=body)).json()
    assert (first["total"], first["created"], first["failed"]) == (50, 50, 0)

    # Re-running a nightly sync writes nothing.
    second = (await api_client.post(url, json=body)).json()
    assert (second["created"], second["skipped"], second["failed"]) == (0, 50, 0)
