from uuid import uuid4

import pytest

from app.models.camera import Camera

# The z12 tile covering central Ahmedabad. A-1 sits inside it; A-2 (Rajkot) does not.
AHMEDABAD_TILE = "/api/v1/tiles/cameras/12/2873/1778.mvt"


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
                camera_type="fixed",
            ),
            Camera(
                camera_uid="GJ-AMC-000002",
                department_id=seeded_department,
                external_camera_id="A-2",
                location="SRID=4326;POINT(70.8 22.3)",
                current_status="offline",
                camera_type="ptz",
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_nearby_returns_closest_first_with_distance(api_client, two_cameras):
    response = await api_client.get(
        "/api/v1/cameras/nearby?lat=23.0225&lon=72.5714&radius_m=5000"
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["camera_uid"] == "GJ-AMC-000001"
    assert items[0]["distance_m"] < 1


@pytest.mark.asyncio
async def test_nearby_is_not_swallowed_by_the_camera_id_route(api_client, two_cameras):
    """Route ordering guard.

    `/cameras/{camera_id}` takes a UUID. If it were declared first, FastAPI would try
    to parse the literal string "nearby" as one and answer 422 -- a route that exists
    but is permanently unreachable. Declaration order is the only thing preventing
    that, and nothing else in the suite would notice if it were reversed.
    """
    response = await api_client.get(
        "/api/v1/cameras/nearby?lat=23.0225&lon=72.5714&radius_m=5000"
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_nearby_orders_by_true_distance_not_by_uid(api_client, seeded_department, session):
    """The uid order and the distance order are deliberately opposed here, so a query
    that forgot to order by ST_Distance would still return both rows -- in the wrong
    order -- and pass a length-only assertion."""
    session.add_all(
        [
            Camera(
                camera_uid="GJ-AMC-000001",
                department_id=seeded_department,
                external_camera_id="far",
                location="SRID=4326;POINT(72.5814 23.0225)",  # ~1 km east
            ),
            Camera(
                camera_uid="GJ-AMC-000002",
                department_id=seeded_department,
                external_camera_id="near",
                location="SRID=4326;POINT(72.5724 23.0225)",  # ~100 m east
            ),
        ]
    )
    await session.commit()

    items = (
        await api_client.get("/api/v1/cameras/nearby?lat=23.0225&lon=72.5714&radius_m=5000")
    ).json()["items"]
    assert [c["external_camera_id"] for c in items] == ["near", "far"]
    assert items[0]["distance_m"] < items[1]["distance_m"]


@pytest.mark.asyncio
async def test_status_filter_narrows_the_list(api_client, two_cameras):
    response = await api_client.get("/api/v1/cameras?statuses=offline")
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_radius_search_rejects_partial_parameters(api_client):
    response = await api_client.get("/api/v1/cameras/nearby?lat=23.0&radius_m=1000")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_tiles_honour_the_same_status_filter(api_client, two_cameras):
    matching = await api_client.get(f"{AHMEDABAD_TILE}?statuses=online")
    excluded = await api_client.get(f"{AHMEDABAD_TILE}?statuses=offline")
    assert matching.status_code == 200
    assert excluded.status_code == 204


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected_uids"),
    [
        ("camera_types=fixed", ["GJ-AMC-000001"]),
        ("camera_types=thermal", []),
        ("statuses=online", ["GJ-AMC-000001"]),
        ("statuses=maintenance", []),
        ("ownership_classes=private", []),
        ("q=A-1", ["GJ-AMC-000001"]),
        # The case that catches a filter the tile query forgot to implement: this
        # matches only the Rajkot camera, so the *Ahmedabad* tile must come back
        # empty. A tile query that silently drops `q` answers 200 here, because the
        # Ahmedabad camera is still inside the envelope.
        ("q=A-2", ["GJ-AMC-000002"]),
        ("q=nothing-matches-this", []),
    ],
)
async def test_list_and_tiles_never_disagree(api_client, two_cameras, query, expected_uids):
    """The whole reason CameraFilter is one object with three consumers.

    The table is served by SQLAlchemy and the map by hand-written MVT SQL -- two
    entirely separate query builders that must answer the same question the same way.
    The tile is additionally bounded by its own envelope, so the agreement asserted
    here is: the Ahmedabad tile has content exactly when the table's result set
    contains the Ahmedabad camera.
    """
    listed = await api_client.get(f"/api/v1/cameras?{query}")
    tile = await api_client.get(f"{AHMEDABAD_TILE}?{query}")

    assert listed.status_code == 200
    assert [c["camera_uid"] for c in listed.json()["items"]] == expected_uids
    in_tile = "GJ-AMC-000001" in expected_uids
    assert tile.status_code == (200 if in_tile else 204)


@pytest.mark.asyncio
async def test_list_and_tiles_agree_on_a_district_filter(api_client, two_cameras, session):
    """district_id is the other filter that reaches the tile endpoint and has to be
    translated into the raw SQL by hand rather than by SQLAlchemy."""
    from geoalchemy2.shape import from_shape
    from shapely.geometry import MultiPolygon, Polygon

    from app.models.admin_boundary import AdminBoundary

    def box(west, south, east, north):
        return MultiPolygon(
            [Polygon([(west, south), (east, south), (east, north), (west, north)])]
        )

    ahmedabad = AdminBoundary(
        level="district", name="Ahmedabad", geom=from_shape(box(72.4, 22.9, 72.7, 23.2), srid=4326)
    )
    rajkot = AdminBoundary(
        level="district", name="Rajkot", geom=from_shape(box(70.6, 22.1, 71.0, 22.5), srid=4326)
    )
    session.add_all([ahmedabad, rajkot])
    await session.commit()

    for district, expected_uid in ((ahmedabad, "GJ-AMC-000001"), (rajkot, "GJ-AMC-000002")):
        query = f"district_id={district.id}"
        listed = (await api_client.get(f"/api/v1/cameras?{query}")).json()
        tile = await api_client.get(f"{AHMEDABAD_TILE}?{query}")

        assert [c["camera_uid"] for c in listed["items"]] == [expected_uid]
        assert tile.status_code == (200 if expected_uid == "GJ-AMC-000001" else 204)


@pytest.mark.asyncio
async def test_tiles_filter_by_department_uuid(api_client, two_cameras, seeded_department):
    """department_ids is the only filter carrying UUIDs into the raw-SQL tile query,
    so it is the one whose parameter binding can fail on type inference alone."""
    mine = await api_client.get(f"{AHMEDABAD_TILE}?department_ids={seeded_department}")
    theirs = await api_client.get(f"{AHMEDABAD_TILE}?department_ids={uuid4()}")
    assert mine.status_code == 200
    assert theirs.status_code == 204


@pytest.mark.asyncio
async def test_a_filter_value_that_is_not_a_valid_enum_is_rejected(api_client):
    """The tile query interpolates clause text and binds values. This confirms the
    values are constrained by the enum before they ever reach the database."""
    response = await api_client.get(f"{AHMEDABAD_TILE}?statuses=online';DROP TABLE cameras;--")
    assert response.status_code == 422
