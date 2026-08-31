import pytest
from sqlalchemy import select

from app.core.enums import SourceType
from app.models.stream_endpoint import StreamEndpoint
from app.schemas.ingestion import RawCameraRecord
from app.services.ingestion import IngestionService


def sentinel_record(dept_id, hls_url="https://cctv.corp8.cloud/cam04/index.m3u8"):
    return RawCameraRecord(
        payload={
            "external_camera_id": "cam04",
            "latitude": 23.0225,
            "longitude": 72.5714,
            "_stream_endpoints": [
                {
                    "protocol": "hls",
                    "url": hls_url,
                    "codec": "h264",
                    "resolution": "1920x1080",
                    "reachability": "public_cdn",
                    "requires_auth": True,
                    "credential_ref": "sentinel_cdn_password",
                    "is_primary": True,
                },
                {
                    "protocol": "rtsp",
                    "url": "rtsp://103.250.160.189:8554/stream/cam04",
                    "codec": "h264",
                    "resolution": "1920x1080",
                    "reachability": "direct_ip",
                    "requires_auth": False,
                    "credential_ref": None,
                    "is_primary": False,
                },
            ],
        },
        department_id=dept_id,
        source_type=SourceType.ADAPTER,
    )


@pytest.mark.asyncio
async def test_endpoints_are_persisted_on_create(session, seeded_department_obj):
    service = IngestionService(session)
    await service.ingest(
        [sentinel_record(seeded_department_obj.id)], seeded_department_obj, mode="commit"
    )

    endpoints = (await session.execute(select(StreamEndpoint))).scalars().all()
    assert {e.protocol for e in endpoints} == {"hls", "rtsp"}
    assert next(e for e in endpoints if e.protocol == "hls").reachability == "public_cdn"


@pytest.mark.asyncio
async def test_resyncing_replaces_endpoints_rather_than_duplicating(
    session, seeded_department_obj
):
    service = IngestionService(session)
    for _ in range(3):
        await service.ingest(
            [sentinel_record(seeded_department_obj.id)],
            seeded_department_obj,
            mode="commit",
        )

    endpoints = (await session.execute(select(StreamEndpoint))).scalars().all()
    assert len(endpoints) == 2


@pytest.mark.asyncio
async def test_endpoints_are_not_written_during_validate_only(
    session, seeded_department_obj
):
    service = IngestionService(session)
    await service.ingest(
        [sentinel_record(seeded_department_obj.id)],
        seeded_department_obj,
        mode="validate_only",
    )
    assert (await session.execute(select(StreamEndpoint))).first() is None


@pytest.mark.asyncio
async def test_a_moved_url_is_resynced_even_when_the_camera_itself_is_unchanged(
    session, seeded_department_obj
):
    """The case that makes syncing on `skipped` non-optional.

    A source can move a stream to a new host without touching a single core field.
    If endpoints only synced on created/updated, the second pull here would report
    `skipped` and leave the registry serving a dead URL forever.
    """
    service = IngestionService(session)
    await service.ingest(
        [sentinel_record(seeded_department_obj.id)], seeded_department_obj, mode="commit"
    )

    report = await service.ingest(
        [sentinel_record(seeded_department_obj.id, hls_url="https://cdn2.corp8.cloud/cam04.m3u8")],
        seeded_department_obj,
        mode="commit",
    )

    assert report.skipped == 1, "core fields are identical, so the camera row is untouched"
    urls = {
        e.url for e in (await session.execute(select(StreamEndpoint))).scalars().all()
    }
    assert "https://cdn2.corp8.cloud/cam04.m3u8" in urls
    assert "https://cctv.corp8.cloud/cam04/index.m3u8" not in urls
