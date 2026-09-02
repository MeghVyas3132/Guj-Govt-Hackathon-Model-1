import pytest
from sqlalchemy import select

from app.api.v1.routers.cameras import _to_read
from app.models.camera import Camera


@pytest.mark.asyncio
async def test_serialises_a_camera_the_caller_still_holds(session, seeded_department):
    """Any code path that creates a camera and serialises it in the same session
    holds a strong reference, so location is still the assigned WKT string rather
    than the WKBElement a fresh load returns."""
    camera = Camera(
        camera_uid="GJ-AMC-000042",
        department_id=seeded_department,
        external_camera_id="A-42",
        location="SRID=4326;POINT(72.5714 23.0225)",
    )
    session.add(camera)
    await session.commit()

    held = (await session.execute(select(Camera))).scalar_one()  # strong reference
    result = _to_read(held)

    assert round(result.latitude, 4) == 23.0225


@pytest.mark.asyncio
async def test_the_department_code_is_populated_not_null(api_client, session):
    """It lives on the related Department, so it is absent from the row's
    __dict__ and was published as null on every camera. A field that is always
    null is worse than an absent one: a client cannot tell "no department" from
    "never filled in"."""
    from app.models.camera import Camera
    from app.models.department import Department

    department = Department(code="DPT", name="Department Under Test")
    session.add(department)
    await session.flush()
    session.add(
        Camera(
            camera_uid="GJ-DPT-000001", department_id=department.id,
            external_camera_id="d1", name="Coded",
            location="SRID=4326;POINT(72.5 23.0)",
        )
    )
    await session.commit()

    listing = await api_client.get("/api/v1/cameras?q=Coded")
    assert listing.json()["items"][0]["department_code"] == "DPT"


@pytest.mark.asyncio
async def test_the_department_code_is_consistent_between_list_and_detail(
    api_client, session
):
    """Two endpoints disagreeing about the same field is how clients end up
    special-casing one of them."""
    from app.models.camera import Camera
    from app.models.department import Department

    department = Department(code="CON", name="Consistency")
    session.add(department)
    await session.flush()
    camera = Camera(
        camera_uid="GJ-CON-000001", department_id=department.id,
        external_camera_id="c1", name="Consistent",
        location="SRID=4326;POINT(72.5 23.0)",
    )
    session.add(camera)
    await session.commit()

    listed = (await api_client.get("/api/v1/cameras?q=Consistent")).json()["items"][0]
    detail = (await api_client.get(f"/api/v1/cameras/{camera.id}")).json()
    assert listed["department_code"] == detail["department_code"] == "CON"


@pytest.mark.asyncio
async def test_department_codes_are_fetched_once_per_page(api_client, session):
    """Per row it would be a query per camera; a page of 500 spans a handful of
    departments."""
    from sqlalchemy import event

    from app.models.camera import Camera
    from app.models.department import Department

    department = Department(code="BLK", name="Bulk")
    session.add(department)
    await session.flush()
    session.add_all([
        Camera(
            camera_uid=f"GJ-BLK-{i:06d}", department_id=department.id,
            external_camera_id=f"b{i}", name=f"Bulk {i}",
            location="SRID=4326;POINT(72.5 23.0)",
        )
        for i in range(20)
    ])
    await session.commit()

    page = (await api_client.get("/api/v1/cameras?q=Bulk&limit=20")).json()
    assert len(page["items"]) == 20
    assert {c["department_code"] for c in page["items"]} == {"BLK"}
