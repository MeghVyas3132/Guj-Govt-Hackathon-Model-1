import csv
import io

import pytest

from app.models.camera import Camera


@pytest.fixture
async def two_cameras(session, seeded_department):
    session.add_all(
        [
            Camera(
                camera_uid="GJ-AMC-000001",
                department_id=seeded_department,
                external_camera_id="A-1",
                location="SRID=4326;POINT(72.5714 23.0225)",
                current_status="online",
                name="Nehru Bridge",
            ),
            Camera(
                camera_uid="GJ-AMC-000002",
                department_id=seeded_department,
                external_camera_id="A-2",
                location="SRID=4326;POINT(72.58 23.03)",
                current_status="offline",
                name="Ashram Road",
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_export_returns_csv_matching_the_filter(api_client, two_cameras):
    response = await api_client.get("/api/v1/cameras/export.csv?statuses=offline")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["camera_uid"] == "GJ-AMC-000002"
    assert rows[0]["latitude"] == "23.03"


@pytest.mark.asyncio
async def test_export_of_an_empty_result_still_has_a_header_row(api_client, seeded_department):
    response = await api_client.get("/api/v1/cameras/export.csv")
    assert response.status_code == 200
    assert response.text.splitlines()[0].startswith("camera_uid,")


@pytest.mark.asyncio
async def test_export_is_not_swallowed_by_the_camera_id_route(api_client, two_cameras):
    """`export.csv` is not a UUID. If `/{camera_id}` were declared first it would be
    handed to the UUID parser and this route would answer 422 forever."""
    response = await api_client.get("/api/v1/cameras/export.csv")
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_export_and_list_return_the_same_rows(api_client, two_cameras):
    """The third consumer of CameraFilter. What the analyst downloads must be exactly
    what the table showed, not a near-miss assembled by a second query builder."""
    query = "camera_types=fixed&statuses=online"

    listed = (await api_client.get(f"/api/v1/cameras?{query}")).json()
    csv_body = (await api_client.get(f"/api/v1/cameras/export.csv?{query}")).text
    exported = list(csv.DictReader(io.StringIO(csv_body)))

    assert [row["camera_uid"] for row in exported] == [c["camera_uid"] for c in listed["items"]]
    assert len(exported) == listed["total"] == 1


@pytest.mark.asyncio
async def test_export_carries_the_derived_coordinates_not_the_wkb(api_client, two_cameras):
    """latitude/longitude live only in the GEOGRAPHY column; `getattr(camera, ...)`
    finds no such attributes, so the writer has to derive them or emit blanks."""
    rows = list(
        csv.DictReader(io.StringIO((await api_client.get("/api/v1/cameras/export.csv")).text))
    )
    assert {row["longitude"] for row in rows} == {"72.5714", "72.58"}
