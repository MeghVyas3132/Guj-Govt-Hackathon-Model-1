import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon

from app.models.admin_boundary import AdminBoundary


@pytest.fixture
async def district(session):
    box = MultiPolygon(
        [Polygon([(72.4, 22.9), (72.8, 22.9), (72.8, 23.3), (72.4, 23.3)])]
    )
    boundary = AdminBoundary(
        level="district", name="Ahmadabad", code="474", geom=from_shape(box, srid=4326)
    )
    session.add(boundary)
    await session.commit()
    return boundary


@pytest.mark.asyncio
async def test_districts_are_listed_with_their_area(api_client, district):
    response = await api_client.get("/api/v1/boundaries?level=district")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["name"] == "Ahmadabad"
    assert body[0]["code"] == "474"
    assert body[0]["area_km2"] > 0


@pytest.mark.asyncio
async def test_a_level_with_no_rows_returns_an_empty_list(api_client, district):
    assert (await api_client.get("/api/v1/boundaries?level=taluka")).json() == []


@pytest.mark.asyncio
async def test_an_alias_can_be_added_and_read_back(api_client, district):
    created = await api_client.post(
        f"/api/v1/boundaries/{district.id}/aliases",
        json={"alias": "Amdavad", "source": "common spelling"},
    )
    assert created.status_code == 201
    # Stored normalised, so matching is not case-sensitive by accident.
    assert created.json()["alias"] == "amdavad"

    listed = (await api_client.get(f"/api/v1/boundaries/{district.id}/aliases")).json()
    assert [a["alias"] for a in listed] == ["amdavad"]
    assert listed[0]["source"] == "common spelling"


@pytest.mark.asyncio
async def test_a_duplicate_alias_is_rejected(api_client, district):
    payload = {"alias": "amdavad"}
    await api_client.post(f"/api/v1/boundaries/{district.id}/aliases", json=payload)
    second = await api_client.post(
        f"/api/v1/boundaries/{district.id}/aliases", json=payload
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_an_empty_alias_is_rejected(api_client, district):
    response = await api_client.post(
        f"/api/v1/boundaries/{district.id}/aliases", json={"alias": "   "}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_alias_on_an_unknown_boundary_is_404(api_client):
    response = await api_client.post(
        "/api/v1/boundaries/00000000-0000-0000-0000-000000000000/aliases",
        json={"alias": "nowhere"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_alias_added_through_the_api_is_used_by_the_geocoder(
    api_client, session, district
):
    """The claim this endpoint exists to support: a place the registry has never
    heard of becomes resolvable without a deploy."""
    from app.services.geocoding import DistrictGeocoder

    assert await DistrictGeocoder(session).locate("14 Karnavati Circle") is None

    await api_client.post(
        f"/api/v1/boundaries/{district.id}/aliases",
        json={"alias": "karnavati", "source": "historic name for Ahmedabad"},
    )

    result = await DistrictGeocoder(session).locate("14 Karnavati Circle")
    assert result is not None
    assert result.district_name == "Ahmadabad"
    assert result.precision == "district"
