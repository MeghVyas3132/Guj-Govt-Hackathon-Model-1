from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.models.camera import Camera
from app.models.camera_health import CameraHealth
from app.models.stream_endpoint import StreamEndpoint
from app.services.probe import HlsProbe
from app.workers.tasks import probe_cameras

LIVE_MANIFEST = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nseg1.ts\n"
DEAD_MANIFEST = b"#EXTM3U\n#EXT-X-VERSION:3\n"


def probe_returning(handler) -> HlsProbe:
    return HlsProbe(transport=httpx.MockTransport(handler))


async def add_camera(
    session,
    department_id,
    uid: str,
    *,
    status: str = "unknown",
    with_hls: bool = True,
    last_seen_at: datetime | None = None,
    is_active: bool = True,
    lifecycle_state: str = "active",
) -> Camera:
    camera = Camera(
        camera_uid=uid,
        department_id=department_id,
        external_camera_id=uid,
        location="SRID=4326;POINT(72.5714 23.0225)",
        current_status=status,
        last_seen_at=last_seen_at,
        is_active=is_active,
        lifecycle_state=lifecycle_state,
    )
    session.add(camera)
    await session.flush()
    if with_hls:
        session.add(
            StreamEndpoint(
                camera_id=camera.id,
                protocol="hls",
                url=f"https://cctv.example/{uid}/index.m3u8",
                reachability="public_cdn",
            )
        )
    await session.commit()
    return camera


@pytest.fixture
def session_factory(session):
    """Hand the worker the testcontainer session instead of a real connection."""

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, *exc):
            return False

    return _Factory()


@pytest.mark.asyncio
async def test_a_live_manifest_marks_the_camera_online(
    session, seeded_department, session_factory
):
    await add_camera(session, seeded_department, "GJ-AMC-000001")

    result = await probe_cameras(
        {},
        session_factory=session_factory,
        probe=probe_returning(lambda r: httpx.Response(200, content=LIVE_MANIFEST)),
    )

    assert result == {"checked": 1, "changed": 1}
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.current_status == "online"


@pytest.mark.asyncio
async def test_every_probe_is_written_to_the_health_log(
    session, seeded_department, session_factory
):
    await add_camera(session, seeded_department, "GJ-AMC-000001")
    probe = probe_returning(lambda r: httpx.Response(200, content=LIVE_MANIFEST))

    for _ in range(3):
        await probe_cameras({}, session_factory=session_factory, probe=probe)

    observations = (
        await session.execute(select(func.count()).select_from(CameraHealth))
    ).scalar_one()
    assert observations == 3


@pytest.mark.asyncio
async def test_an_unchanged_status_reports_zero_changed(
    session, seeded_department, session_factory
):
    await add_camera(session, seeded_department, "GJ-AMC-000001")
    probe = probe_returning(lambda r: httpx.Response(200, content=LIVE_MANIFEST))

    first = await probe_cameras({}, session_factory=session_factory, probe=probe)
    second = await probe_cameras({}, session_factory=session_factory, probe=probe)

    assert first["changed"] == 1
    assert second == {"checked": 1, "changed": 0}


@pytest.mark.asyncio
async def test_a_manifest_with_no_segments_marks_the_camera_offline(
    session, seeded_department, session_factory
):
    await add_camera(session, seeded_department, "GJ-AMC-000001", status="online")

    await probe_cameras(
        {},
        session_factory=session_factory,
        probe=probe_returning(lambda r: httpx.Response(200, content=DEAD_MANIFEST)),
    )

    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.current_status == "offline"


@pytest.mark.asyncio
async def test_an_expired_session_does_not_paint_the_fleet_offline(
    session, seeded_department, session_factory
):
    """A redirect to the login page means our cookie died, not that the camera did."""
    await add_camera(session, seeded_department, "GJ-AMC-000001", status="online")

    await probe_cameras(
        {},
        session_factory=session_factory,
        probe=probe_returning(
            lambda r: httpx.Response(302, headers={"location": "/auth/login"})
        ),
    )

    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.current_status == "unknown"


@pytest.mark.asyncio
async def test_cameras_without_an_hls_endpoint_are_not_probed(
    session, seeded_department, session_factory
):
    await add_camera(session, seeded_department, "GJ-AMC-000001", with_hls=False)

    result = await probe_cameras(
        {},
        session_factory=session_factory,
        probe=probe_returning(lambda r: httpx.Response(200, content=LIVE_MANIFEST)),
    )

    assert result["checked"] == 0


@pytest.mark.asyncio
async def test_decommissioned_cameras_are_not_probed(
    session, seeded_department, session_factory
):
    await add_camera(session, seeded_department, "GJ-AMC-000001", lifecycle_state="decommissioned")
    await add_camera(session, seeded_department, "GJ-AMC-000002", is_active=False)
    await add_camera(session, seeded_department, "GJ-AMC-000003")

    result = await probe_cameras(
        {},
        session_factory=session_factory,
        probe=probe_returning(lambda r: httpx.Response(200, content=LIVE_MANIFEST)),
    )

    assert result["checked"] == 1


@pytest.mark.asyncio
async def test_the_least_recently_seen_camera_is_probed_first(
    session, seeded_department, session_factory, monkeypatch
):
    """Rotation by staleness is what stops a fleet larger than one batch from
    re-probing the same rows forever."""
    import app.workers.tasks as tasks

    monkeypatch.setattr(tasks, "PROBE_BATCH", 1)
    now = datetime.now(UTC)
    await add_camera(session, seeded_department, "GJ-AMC-000001", last_seen_at=now)
    await add_camera(
        session, seeded_department, "GJ-AMC-000002", last_seen_at=now - timedelta(days=3)
    )

    probed: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        probed.append(str(request.url))
        return httpx.Response(200, content=LIVE_MANIFEST)

    await probe_cameras(
        {}, session_factory=session_factory, probe=probe_returning(record)
    )

    assert len(probed) == 1
    assert "GJ-AMC-000002" in probed[0]


@pytest.mark.asyncio
async def test_the_batch_limit_is_respected(
    session, seeded_department, session_factory, monkeypatch
):
    import app.workers.tasks as tasks

    monkeypatch.setattr(tasks, "PROBE_BATCH", 2)
    for index in range(1, 6):
        await add_camera(session, seeded_department, f"GJ-AMC-00000{index}")

    result = await probe_cameras(
        {},
        session_factory=session_factory,
        probe=probe_returning(lambda r: httpx.Response(200, content=LIVE_MANIFEST)),
    )

    assert result["checked"] == 2


@pytest.mark.asyncio
async def test_many_cameras_probe_concurrently_without_a_session_conflict(
    session, seeded_department, session_factory
):
    """Regression: AsyncSession is not concurrency-safe. Letting gathered probe tasks
    each flush raised 'Session is already flushing' the moment two overlapped, so the
    worker would have failed on its first real run at PROBE_BATCH=200."""
    for index in range(1, 13):
        await add_camera(session, seeded_department, f"GJ-AMC-{index:06d}")

    result = await probe_cameras(
        {},
        session_factory=session_factory,
        probe=probe_returning(lambda r: httpx.Response(200, content=LIVE_MANIFEST)),
    )

    assert result == {"checked": 12, "changed": 12}
    observations = (
        await session.execute(select(func.count()).select_from(CameraHealth))
    ).scalar_one()
    assert observations == 12
