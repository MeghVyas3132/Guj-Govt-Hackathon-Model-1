import pytest
from geoalchemy2.shape import to_shape
from sqlalchemy import select

from app.core.enums import SourceType
from app.models.camera import Camera
from app.models.department import Department
from app.models.field_mapping import FieldMapping
from app.schemas.ingestion import RawCameraRecord
from app.services.ingestion import IngestionService


@pytest.fixture
async def department(session) -> Department:
    dept = Department(code="AMC", name="Ahmedabad Municipal Corporation")
    session.add(dept)
    await session.flush()
    session.add(
        FieldMapping(
            department_id=dept.id,
            version=1,
            config={
                "column_map": {
                    "cam_id": "external_camera_id",
                    "lat": "latitude",
                    "lng": "longitude",
                    "state": "status",
                },
                "value_maps": {"status": {"ACTIVE": "online", "DOWN": "offline"}},
                "passthrough_to_metadata": True,
            },
        )
    )
    await session.commit()
    return dept


def record(dept, **overrides) -> RawCameraRecord:
    payload = {"cam_id": "A-1", "lat": "23.0225", "lng": "72.5714", "state": "ACTIVE"}
    payload.update(overrides)
    return RawCameraRecord(payload=payload, department_id=dept.id, source_type=SourceType.CSV)


@pytest.mark.asyncio
async def test_validate_only_does_not_write(session, department):
    service = IngestionService(session)
    report = await service.ingest([record(department)], department, mode="validate_only")
    assert report.total == 1
    assert report.failed == 0
    assert (await session.execute(select(Camera))).first() is None


@pytest.mark.asyncio
async def test_commit_creates_a_camera(session, department):
    service = IngestionService(session)
    report = await service.ingest([record(department)], department, mode="commit")
    assert report.created == 1
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.external_camera_id == "A-1"
    assert camera.current_status == "online"
    assert camera.camera_uid.startswith("GJ-AMC-")


@pytest.mark.asyncio
async def test_reimporting_identical_data_is_idempotent(session, department):
    service = IngestionService(session)
    await service.ingest([record(department)], department, mode="commit")
    second = await service.ingest([record(department)], department, mode="commit")
    assert second.created == 0
    assert second.skipped == 1
    assert len((await session.execute(select(Camera))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_changed_field_produces_an_update_not_a_duplicate(session, department):
    service = IngestionService(session)
    await service.ingest([record(department)], department, mode="commit")
    second = await service.ingest([record(department, state="DOWN")], department, mode="commit")
    assert second.updated == 1
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.current_status == "offline"


@pytest.mark.asyncio
async def test_relocated_camera_updates_its_stored_point(session, department):
    """The mirror of the idempotency test: a real move must still be detected.

    location round-trips as a WKBElement, so the moved/not-moved decision compares
    decoded coordinates rather than string forms.
    """
    service = IngestionService(session)
    await service.ingest([record(department)], department, mode="commit")
    second = await service.ingest(
        [record(department, lat="23.0300", lng="72.5800")], department, mode="commit"
    )
    assert second.updated == 1
    camera = (await session.execute(select(Camera))).scalar_one()
    point = to_shape(camera.location)
    assert round(point.x, 4) == 72.58
    assert round(point.y, 4) == 23.03


@pytest.mark.asyncio
async def test_invalid_row_fails_without_blocking_valid_rows(session, department):
    service = IngestionService(session)
    report = await service.ingest(
        [record(department, cam_id="A-1"), record(department, cam_id="A-2", lat="99.9")],
        department,
        mode="commit",
    )
    assert report.created == 1
    assert report.failed == 1
    assert len((await session.execute(select(Camera))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_unmapped_columns_land_in_metadata(session, department):
    service = IngestionService(session)
    await service.ingest([record(department, pole_number="P-77")], department, mode="commit")
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.metadata_["pole_number"] == "P-77"


@pytest.fixture
async def geocodable_department(session):
    """A department whose source has names but no coordinates -- the Sentinel case."""
    from geoalchemy2.shape import from_shape
    from shapely.geometry import MultiPolygon, Polygon

    from app.models.admin_boundary import AdminBoundary

    session.add(
        AdminBoundary(
            level="district",
            name="Junagadh",
            geom=from_shape(
                MultiPolygon(
                    [Polygon([(70.2, 21.3), (70.7, 21.3), (70.7, 21.8), (70.2, 21.8)])]
                ),
                srid=4326,
            ),
        )
    )
    dept = Department(code="SEN", name="Sentinel Sandbox")
    session.add(dept)
    await session.flush()
    session.add(
        FieldMapping(
            department_id=dept.id,
            version=1,
            config={
                "column_map": {"id": "external_camera_id", "name": "name"},
                "geocode_from": "name",
            },
        )
    )
    await session.commit()
    return dept


def nameonly_record(dept, name: str = "08 majewadi-gate-junagadh"):
    return RawCameraRecord(
        payload={"id": "cam08", "name": name},
        department_id=dept.id,
        source_type=SourceType.ADAPTER,
    )


@pytest.mark.asyncio
async def test_a_record_with_no_coordinates_is_geocoded_from_its_name(
    session, geocodable_department
):
    report = await IngestionService(session).ingest(
        [nameonly_record(geocodable_department)], geocodable_department, mode="commit"
    )
    assert report.created == 1

    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.metadata_["geocode_precision"] == "district"
    assert camera.metadata_["geocode_district"] == "Junagadh"
    assert camera.metadata_["geocode_matched_on"] == "junagadh"


@pytest.mark.asyncio
async def test_geocoding_warns_so_the_imprecision_is_visible_in_the_report(
    session, geocodable_department
):
    report = await IngestionService(session).ingest(
        [nameonly_record(geocodable_department)], geocodable_department, mode="commit"
    )
    assert any("district" in w.lower() for w in report.rows[0].warnings)


@pytest.mark.asyncio
async def test_an_ungeocodable_name_fails_the_row_rather_than_guessing(
    session, geocodable_department
):
    report = await IngestionService(session).ingest(
        [nameonly_record(geocodable_department, name="23 kheram")],
        geocodable_department,
        mode="commit",
    )
    assert report.failed == 1
    assert report.rows[0].errors[0].code == "missing_required_field"


@pytest.mark.asyncio
async def test_supplied_coordinates_are_never_overridden_by_geocoding(
    session, geocodable_department
):
    record = nameonly_record(geocodable_department)
    record.payload["latitude"] = 21.5
    record.payload["longitude"] = 70.4

    await IngestionService(session).ingest(
        [record], geocodable_department, mode="commit"
    )

    camera = (await session.execute(select(Camera))).scalar_one()
    assert "geocode_precision" not in camera.metadata_


@pytest.mark.asyncio
async def test_an_unknown_camera_type_is_preserved_not_discarded(
    session, seeded_department_obj
):
    """Gujarat runs unknown vendors. A camera type we have never seen must stay
    queryable AND stay recoverable -- flattening it to 'other' and forgetting the
    original is how a registry loses the information it exists to hold."""
    dept = seeded_department_obj
    record = RawCameraRecord(
        payload={
            "external_camera_id": "X-1", "latitude": 23.02, "longitude": 72.57,
            "camera_type": "fisheye-360-panoramic",
        },
        department_id=dept.id,
        source_type=SourceType.API,
    )

    report = await IngestionService(session).ingest([record], dept, mode="commit")

    assert report.created == 1
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.camera_type == "other"
    assert camera.metadata_["unmapped_camera_type"] == "fisheye-360-panoramic"
    assert any("fisheye-360-panoramic" in w for w in report.rows[0].warnings)


@pytest.mark.asyncio
async def test_adding_the_term_makes_the_next_import_classify_it_properly(
    session, seeded_department_obj
):
    """The recovery path: an operator adds one row and re-imports, no deploy."""
    from app.models.vocabulary import VocabularyTerm

    dept = seeded_department_obj

    def record():
        return RawCameraRecord(
            payload={
                "external_camera_id": "X-1", "latitude": 23.02, "longitude": 72.57,
                "camera_type": "fisheye",
            },
            department_id=dept.id,
            source_type=SourceType.API,
        )

    await IngestionService(session).ingest([record()], dept, mode="commit")
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.camera_type == "other"

    session.add(VocabularyTerm(dimension="camera_type", code="fisheye", label="Fisheye"))
    await session.commit()

    report = await IngestionService(session).ingest([record()], dept, mode="commit")
    assert report.updated == 1
    await session.refresh(camera)
    assert camera.camera_type == "fisheye"


@pytest.mark.asyncio
async def test_a_known_term_records_no_unmapped_metadata(
    session, seeded_department_obj
):
    dept = seeded_department_obj
    record = RawCameraRecord(
        payload={
            "external_camera_id": "X-1", "latitude": 23.02, "longitude": 72.57,
            "camera_type": "ptz", "status": "online",
        },
        department_id=dept.id,
        source_type=SourceType.API,
    )
    await IngestionService(session).ingest([record], dept, mode="commit")

    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.camera_type == "ptz"
    assert "unmapped_camera_type" not in camera.metadata_
