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
