from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.camera import Camera
from app.models.camera_health import CameraHealth
from app.repositories.health import HealthRepository
from app.schemas.common import Page
from app.schemas.health import HealthObservationIn, HealthSummary, OfflineCamera
from app.services.health import HealthService

router = APIRouter(prefix="/health", tags=["health"])


class BatchAck(BaseModel):
    accepted: int
    changed: int
    unmatched: list[str]


@router.post(
    "/observations",
    response_model=BatchAck,
    status_code=202,
    summary="Push a batch of health observations",
    description=(
        "Accepts observations keyed by the department's own camera id. Unknown ids are "
        "reported in `unmatched` rather than failing the batch, so a departmental sync "
        "is never blocked by one stale row."
    ),
)
async def push_observations(
    observations: list[HealthObservationIn],
    department_id: UUID = Query(...),
    session: AsyncSession = Depends(get_session),
) -> BatchAck:
    external_ids = [o.external_camera_id for o in observations if o.external_camera_id]
    rows = (
        (
            await session.execute(
                select(Camera).where(
                    Camera.department_id == department_id,
                    Camera.external_camera_id.in_(external_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    by_external = {c.external_camera_id: c for c in rows}

    service = HealthService(session)
    accepted = changed = 0
    unmatched: list[str] = []

    for observation in observations:
        camera = by_external.get(observation.external_camera_id or "")
        if camera is None:
            if observation.external_camera_id:
                unmatched.append(observation.external_camera_id)
            continue
        outcome = await service.record(camera, observation, source="api")
        accepted += 1
        changed += int(outcome.changed)

    await session.commit()
    return BatchAck(accepted=accepted, changed=changed, unmatched=unmatched)


@router.get(
    "/offline",
    response_model=Page[OfflineCamera],
    summary="Currently offline cameras, longest down first",
)
async def offline(
    limit: int = Query(200, le=1000),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[OfflineCamera]:
    items = await HealthRepository(session).offline(limit=limit, offset=offset)
    return Page(items=items, total=len(items), limit=limit, offset=offset)


@router.get("/summary", response_model=HealthSummary, summary="Fleet health counts")
async def summary(session: AsyncSession = Depends(get_session)) -> HealthSummary:
    return await HealthRepository(session).summary()


@router.get("/cameras/{camera_id}/history", summary="Observation history for one camera")
async def history(
    camera_id: UUID,
    limit: int = Query(200, le=2000),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if await session.get(Camera, camera_id) is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    rows = (
        (
            await session.execute(
                select(CameraHealth)
                .where(CameraHealth.camera_id == camera_id)
                .order_by(CameraHealth.observed_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "status": r.status,
            "observed_at": r.observed_at.isoformat(),
            "source": r.source,
            "latency_ms": r.latency_ms,
        }
        for r in rows
    ]
