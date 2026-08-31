from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.enums import CameraStatus, CameraType, OwnershipClass
from app.models.camera import Camera
from app.models.stream_endpoint import StreamEndpoint
from app.repositories.camera import CameraRepository
from app.schemas.camera import CameraRead, StreamEndpointRead
from app.schemas.common import Page
from app.schemas.filters import CameraFilter

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


def camera_filter(
    q: str | None = Query(None, description="Free text over uid, name, address."),
    department_ids: list[UUID] = Query(default_factory=list),
    camera_types: list[CameraType] = Query(default_factory=list),
    statuses: list[CameraStatus] = Query(default_factory=list),
    ownership_classes: list[OwnershipClass] = Query(default_factory=list),
    district_id: UUID | None = Query(None),
) -> CameraFilter:
    """The one place the query string becomes a CameraFilter.

    The list endpoint, the CSV export and the vector-tile endpoint all depend on this
    function, so `?statuses=offline` cannot mean one thing to the table and another to
    the map. The enum annotations also mean an unrecognised value is rejected by
    FastAPI with a 422 before it reaches any query builder.
    """
    return CameraFilter(
        q=q,
        department_ids=department_ids,
        camera_types=camera_types,
        statuses=statuses,
        ownership_classes=ownership_classes,
        district_id=district_id,
    )


class CameraNearby(CameraRead):
    """A camera plus how far it is from the query point.

    `CameraRead` has no `distance_m` of its own, so spreading a CameraRead into this
    constructor alongside `distance_m=` cannot collide on that key.
    """

    distance_m: float


@router.get("", response_model=Page[CameraRead], summary="List and filter cameras")
async def list_cameras(
    filters: CameraFilter = Depends(camera_filter),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[CameraRead]:
    repo = CameraRepository(session)
    rows = await repo.list(filters, limit=limit, offset=offset)
    total = await repo.count(filters)
    return Page(items=[_to_read(row) for row in rows], total=total, limit=limit, offset=offset)


# `/nearby` is declared before `/{camera_id}`: FastAPI matches routes in declaration
# order, so with the paths reversed the literal string "nearby" would be handed to the
# UUID parser and every request here would answer 422 instead of searching. The same
# goes for `/export.csv` below.
@router.get(
    "/nearby",
    response_model=Page[CameraNearby],
    summary="Cameras within a radius, nearest first",
    description=(
        "The incident-response query: given an FIR location, which cameras could have "
        "seen it, closest first. Distances are true metres on the spheroid, not degrees."
    ),
)
async def cameras_nearby(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_m: float = Query(..., gt=0, le=200_000),
    limit: int = Query(50, le=500),
    session: AsyncSession = Depends(get_session),
) -> Page[CameraNearby]:
    filters = CameraFilter(near_lat=lat, near_lon=lon, radius_m=radius_m)
    rows = await CameraRepository(session).list_nearby(filters, limit=limit)
    items = [
        CameraNearby(**_to_read(camera).model_dump(), distance_m=round(distance, 2))
        for camera, distance in rows
    ]
    return Page(items=items, total=len(items), limit=limit, offset=0)


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
