from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2.shape import to_shape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.camera import Camera
from app.models.stream_endpoint import StreamEndpoint
from app.schemas.camera import CameraRead, StreamEndpointRead
from app.schemas.common import Page

router = APIRouter(prefix="/cameras", tags=["cameras"])


def _to_read(row: Camera) -> CameraRead:
    """Project one ORM row onto the published contract shape.

    latitude/longitude are derived from the GEOGRAPHY column rather than stored, and
    `metadata_` is renamed back to its contract name `metadata`. Building the payload
    from __dict__ carries SQLAlchemy's `_sa_instance_state` along; pydantic ignores
    unknown keys, so it is harmless.
    """
    point = to_shape(row.location)
    return CameraRead.model_validate(
        {
            **row.__dict__,
            "latitude": point.y,
            "longitude": point.x,
            "metadata": row.metadata_,
            "stream_endpoints": [],
        }
    )


@router.get("", response_model=Page[CameraRead], summary="List and filter cameras")
async def list_cameras(
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[CameraRead]:
    total = (await session.execute(select(func.count()).select_from(Camera))).scalar_one()
    rows = (
        (
            await session.execute(
                select(Camera).order_by(Camera.camera_uid).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return Page(items=[_to_read(row) for row in rows], total=total, limit=limit, offset=offset)


@router.get("/{camera_id}", response_model=CameraRead)
async def get_camera(
    camera_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> CameraRead:
    row = await session.get(Camera, camera_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return _to_read(row)


@router.get(
    "/{camera_id}/streams",
    response_model=list[StreamEndpointRead],
    summary="Stream endpoints for this camera",
    description=(
        "Entry point for Models 2-4. Prefer the endpoint whose reachability matches "
        "your network: public_cdn works anywhere, direct_ip needs gateway ports open."
    ),
)
async def get_camera_streams(
    camera_id: UUID, session: AsyncSession = Depends(get_session)
) -> list[StreamEndpointRead]:
    # Primary first, so a client that just takes the head of the list gets the
    # endpoint the source designated as canonical rather than an arbitrary row.
    rows = (
        (
            await session.execute(
                select(StreamEndpoint)
                .where(StreamEndpoint.camera_id == camera_id)
                .order_by(StreamEndpoint.is_primary.desc())
            )
        )
        .scalars()
        .all()
    )
    return [StreamEndpointRead.model_validate(row) for row in rows]
