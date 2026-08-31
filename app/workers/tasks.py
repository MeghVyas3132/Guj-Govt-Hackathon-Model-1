import asyncio

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.enums import StreamProtocol
from app.models.camera import Camera
from app.models.stream_endpoint import StreamEndpoint
from app.schemas.health import HealthObservationIn
from app.services.health import HealthService
from app.services.probe import HlsProbe

# Bounded so a large fleet cannot open thousands of sockets at once. At 80k cameras the
# design is a worker pool partitioned by department with staggered schedules; here we
# probe a capped sample, which is honest and demonstrable.
PROBE_CONCURRENCY = 20
PROBE_BATCH = 200


async def probe_cameras(ctx: dict) -> dict[str, int]:
    semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)
    probe = HlsProbe(session_cookie=ctx.get("sentinel_cookie"))

    async with SessionLocal() as session:
        stmt = (
            select(Camera, StreamEndpoint)
            .join(StreamEndpoint, StreamEndpoint.camera_id == Camera.id)
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

        service = HealthService(session)

        async def run(camera: Camera, endpoint: StreamEndpoint) -> bool:
            async with semaphore:
                result = await probe.check(endpoint.url)
            outcome = await service.record(
                camera,
                HealthObservationIn(
                    status=result.status,
                    latency_ms=result.latency_ms,
                    detail=result.detail,
                ),
                source="probe",
            )
            return outcome.changed

        results = await asyncio.gather(*(run(c, e) for c, e in pairs))
        checked = len(results)
        changed = sum(results)
        await session.commit()

    return {"checked": checked, "changed": changed}


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [probe_cameras]
    cron_jobs = [
        cron(probe_cameras, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55})
    ]
