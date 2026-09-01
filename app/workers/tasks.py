import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.enums import StreamProtocol
from app.models.camera import Camera
from app.models.source_connector import SourceConnector
from app.models.stream_endpoint import StreamEndpoint
from app.schemas.health import HealthObservationIn
from app.services.credentials import CredentialResolver
from app.services.health import HealthService
from app.services.probe import HlsProbe, ProbeResult

# Bounded so a large fleet cannot open thousands of sockets at once. At 80k cameras the
# design is a worker pool partitioned by department with staggered schedules; here we
# probe a capped sample, which is honest and demonstrable.
PROBE_CONCURRENCY = 20
PROBE_BATCH = 200


async def probe_cameras(
    ctx: dict,
    *,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None,
    probe: HlsProbe | None = None,
) -> dict[str, int]:
    """Probe a batch of cameras and record what came back.

    `session_factory` and `probe` exist so this is testable. Built in-line they would
    be unreachable from a test, which could then only be written against a real
    database and a real remote host -- and a unit test must not call either. Production
    passes neither argument and gets the real ones.
    """
    semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
    probe = probe or HlsProbe()
    session_factory = session_factory or SessionLocal

    async with session_factory() as session:
        # Each endpoint's auth comes from the connector that owns its department,
        # so a fleet spanning several sources with different schemes probes correctly.
        stmt = (
            select(Camera, StreamEndpoint, SourceConnector)
            .join(StreamEndpoint, StreamEndpoint.camera_id == Camera.id)
            .outerjoin(
                SourceConnector, SourceConnector.department_id == Camera.department_id
            )
            .where(
                StreamEndpoint.protocol == StreamProtocol.HLS.value,
                Camera.is_active,
                Camera.lifecycle_state == "active",
            )
            # Least recently checked first, so coverage rotates fairly across a fleet
            # larger than one batch instead of re-probing the same 200 rows forever.
            .order_by(Camera.last_seen_at.asc().nulls_first())
            .limit(PROBE_BATCH)
        )
        pairs = (await session.execute(stmt)).all()

        # Fan out on the network, fan in on the database. AsyncSession is not
        # concurrency-safe: letting gathered tasks each call flush() raises
        # "Session is already flushing" as soon as two overlap. Probing is the slow
        # part and stays concurrent; the writes are serialised afterwards.
        resolver = CredentialResolver(session)

        async def check(
            camera: Camera, endpoint: StreamEndpoint, connector: SourceConnector | None
        ) -> tuple[Camera, ProbeResult]:
            secret = cookie_name = header_name = None
            if endpoint.requires_auth and endpoint.credential_ref:
                secret = await resolver.resolve(endpoint.credential_ref)
                auth = (connector.config or {}).get("auth", {}) if connector else {}
                if auth.get("type") == "cookie":
                    cookie_name = auth.get("name")
                elif auth.get("type") == "header":
                    header_name = auth.get("name")
            async with semaphore:
                result = await probe.check(
                    endpoint.url,
                    secret=secret,
                    cookie_name=cookie_name,
                    header_name=header_name,
                )
            return camera, result

        probed = await asyncio.gather(*(check(c, e, k) for c, e, k in pairs))

        service = HealthService(session)
        changed = 0
        for camera, result in probed:
            outcome = await service.record(
                camera,
                HealthObservationIn(
                    status=result.status,
                    latency_ms=result.latency_ms,
                    detail=result.detail,
                ),
                source="probe",
            )
            changed += int(outcome.changed)

        checked = len(probed)
        await session.commit()

    return {"checked": checked, "changed": changed}


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [probe_cameras]
    cron_jobs = [
        cron(probe_cameras, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55})
    ]
