import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon

from app.models.admin_boundary import AdminBoundary
from app.services.geocoding import DistrictGeocoder


@pytest.fixture
async def districts(session):
    """Two real-ish districts, deliberately far apart."""
    shapes = {
        "Ahmadabad": Polygon([(72.4, 22.9), (72.8, 22.9), (72.8, 23.3), (72.4, 23.3)]),
        "Junagadh": Polygon([(70.2, 21.3), (70.7, 21.3), (70.7, 21.8), (70.2, 21.8)]),
        "Navsari": Polygon([(72.8, 20.8), (73.2, 20.8), (73.2, 21.1), (72.8, 21.1)]),
    }
    for name, poly in shapes.items():
        session.add(
            AdminBoundary(
                level="district", name=name, geom=from_shape(MultiPolygon([poly]), srid=4326)
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_a_district_named_outright_is_matched(session, districts):
    result = await DistrictGeocoder(session).locate("08 majewadi-gate-junagadh")
    assert result is not None
    assert result.district_name == "Junagadh"
    assert result.precision == "district"
    assert 70.2 < result.longitude < 70.7
    assert 21.3 < result.latitude < 21.8


@pytest.mark.asyncio
async def test_the_common_spelling_of_ahmedabad_matches_the_census_spelling(
    session, districts
):
    """Boundaries store 2011 Census spellings; nobody writes 'Ahmadabad' by hand."""
    result = await DistrictGeocoder(session).locate("Ahmedabad Paldi Circle")
    assert result is not None
    assert result.district_name == "Ahmadabad"


@pytest.mark.asyncio
async def test_a_known_town_resolves_to_its_district(session, districts):
    result = await DistrictGeocoder(session).locate("28 bilimora")
    assert result is not None
    assert result.district_name == "Navsari"
    assert result.matched_on == "bilimora"


@pytest.mark.asyncio
async def test_an_ahmedabad_landmark_resolves_without_naming_the_city(
    session, districts
):
    result = await DistrictGeocoder(session).locate("01 Chiman bhai Bridge")
    assert result is not None
    assert result.district_name == "Ahmadabad"


@pytest.mark.asyncio
async def test_an_unrecognised_place_returns_none_rather_than_guessing(
    session, districts
):
    assert await DistrictGeocoder(session).locate("23 kheram") is None
    assert await DistrictGeocoder(session).locate("") is None
    assert await DistrictGeocoder(session).locate(None) is None


@pytest.mark.asyncio
async def test_the_point_lies_inside_the_district_not_merely_near_it(
    session, districts
):
    """ST_PointOnSurface, not ST_Centroid: a concave district's centroid can fall
    outside its own polygon, which would place the camera in a neighbouring one."""
    from sqlalchemy import text

    result = await DistrictGeocoder(session).locate("Junagadh")
    inside = (
        await session.execute(
            text(
                "SELECT ST_Intersects(geom, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography)"
                " FROM admin_boundaries WHERE name = 'Junagadh'"
            ),
            {"lon": result.longitude, "lat": result.latitude},
        )
    ).scalar_one()
    assert inside is True


@pytest.mark.asyncio
async def test_a_longer_alias_wins_over_a_shorter_substring(session, districts):
    """'gir somnath' must not lose to a bare 'somnath' style partial match."""
    geocoder = DistrictGeocoder(session)
    result = await geocoder.locate("07 hero-showroom-gir-somnath")
    # Gir Somnath is not in this fixture, so this must decline rather than
    # fall through to a wrong district.
    assert result is None
