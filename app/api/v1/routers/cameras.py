from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import get_enricher, request_context, require_scope
from app.services.enrichment import StreamEnricher
from app.services.metadata import EnrichmentOutcome, MetadataService
from app.core.geo import to_point
from app.models.camera import Camera
from app.models.stream_endpoint import StreamEndpoint
from app.repositories.camera import CameraRepository
from app.schemas.auth import Principal
from app.schemas.camera import (
    CameraCreate,
    CameraRead,
    EnrichmentReport,
    EnrichmentResult,
    StreamEndpointRead,
)
from app.schemas.common import Page
from app.schemas.filters import CameraFilter
from app.services.export import cameras_to_csv

router = APIRouter(prefix="/cameras", tags=["cameras"])


def _enrichment_report(outcomes: list[EnrichmentOutcome]) -> EnrichmentReport:
    return EnrichmentReport(
        checked=len(outcomes),
        updated=sum(1 for o in outcomes if o.updated),
        failed=sum(1 for o in outcomes if o.error),
        results=[
            EnrichmentResult(
                camera_id=str(o.camera_id),
                external_camera_id=o.external_camera_id,
                updated=o.updated,
                metadata=o.metadata,
                error=o.error,
            )
            for o in outcomes
        ],
    )


def _to_read(row: Camera) -> CameraRead:
    """Project one ORM row onto the published contract shape.

    latitude/longitude are derived from the GEOGRAPHY column rather than stored, and
    `metadata_` is renamed back to its contract name `metadata`. Building the payload
    from __dict__ carries SQLAlchemy's `_sa_instance_state` along; pydantic ignores
    unknown keys, so it is harmless.
    """
    point = to_point(row.location)
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
    camera_types: list[str] = Query(default_factory=list),
    statuses: list[str] = Query(default_factory=list),
    ownership_classes: list[str] = Query(default_factory=list),
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
    principal: Principal = Depends(require_scope("cameras:read")),
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
@router.post(
    "",
    response_model=CameraRead,
    status_code=201,
    summary="Register a single camera",
    description=(
        "Manual onboarding. Routed through the same ingestion pipeline as CSV upload "
        "and vendor sync, so a camera typed into a form gets identical validation, "
        "vocabulary resolution and dedupe. Re-submitting the same "
        "(department, external_camera_id) updates rather than duplicating."
    ),
)
async def create_camera(
    payload: CameraCreate,
    principal: Principal = Depends(require_scope("cameras:write")),
    session: AsyncSession = Depends(get_session),
    context: dict = Depends(request_context),
) -> CameraRead:
    from app.core.enums import SourceType
    from app.models.department import Department
    from app.schemas.ingestion import RawCameraRecord
    from app.services.ingestion import IngestionService

    department = await session.get(Department, payload.department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")

    if not principal.may_write_department(department.id):
        raise HTTPException(
            status_code=403,
            detail=(
                "You may only write cameras for your own department. Scope alone is "
                "not enough: cameras:write is departmental for every role below "
                "super_admin."
            ),
        )

    body = payload.model_dump(mode="json", exclude_none=True)
    # department_id is routing, not a camera attribute: left in, the resolver would
    # file it into every manually-entered camera's metadata.
    body.pop("department_id", None)
    extra = body.pop("metadata", None) or {}
    body.update(extra)

    report = await IngestionService(session).ingest(
        [
            RawCameraRecord(
                payload=body,
                department_id=department.id,
                source_type=SourceType.MANUAL,
            )
        ],
        department,
        mode="commit",
        actor=principal,
    )

    row = report.rows[0]
    if row.outcome == "failed":
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Camera was not accepted.",
                "errors": [e.model_dump() for e in row.errors],
                "warnings": row.warnings,
            },
        )

    repo = CameraRepository(session)
    camera = await repo.get_by_external_id(department.id, payload.external_camera_id)
    return _to_read(camera)


@router.get(
    "/{camera_id}/audit",
    summary="Change history for one camera",
    description="Every recorded change, newest first, with the state before and after.",
)
async def camera_audit(
    camera_id: UUID,
    limit: int = Query(100, le=1000),
    principal: Principal = Depends(require_scope("cameras:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    from app.services.audit import AuditService

    entries = await AuditService(session).history("camera", camera_id, limit)
    return [
        {
            "action": e.action,
            "at": e.at.isoformat(),
            "actor_type": e.actor_type,
            "actor_label": e.actor_label,
            "before": e.before,
            "after": e.after,
        }
        for e in entries
    ]


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
    principal: Principal = Depends(require_scope("cameras:read")),
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


@router.get(
    "/export.csv",
    summary="Export the filtered result set as CSV",
    description=(
        "The same filter as the list endpoint and the tiles, rendered as a spreadsheet "
        "a department can reconcile against its own records. An empty result still "
        "returns a header row."
    ),
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Matching cameras."}},
)
async def export_csv(
    filters: CameraFilter = Depends(camera_filter),
    principal: Principal = Depends(require_scope("cameras:export")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # Not paginated: a filtered export that silently stopped at 50 rows would be
    # worse than no export, because it looks like a complete answer.
    rows = await CameraRepository(session).list(filters, limit=100_000, offset=0)
    return Response(
        content=cameras_to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cameras.csv"'},
    )


@router.get("/{camera_id}", response_model=CameraRead)
async def get_camera(
    camera_id: UUID,
    principal: Principal = Depends(require_scope("cameras:read")),
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
    camera_id: UUID,
    principal: Principal = Depends(require_scope("cameras:read")),
    session: AsyncSession = Depends(get_session),
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


@router.post(
    "/{camera_id}/enrich",
    response_model=EnrichmentReport,
    summary="Derive metadata from this camera's stream",
    description=(
        "Reads the camera's HLS manifest and decodes one segment to establish "
        "codec, resolution and frame rate. Use this when a source catalogue "
        "supplies identifiers but no technical metadata, which is the common case."
    ),
)
async def enrich_camera(
    camera_id: UUID,
    principal: Principal = Depends(require_scope("cameras:write")),
    session: AsyncSession = Depends(get_session),
    enricher: StreamEnricher = Depends(get_enricher),
) -> EnrichmentReport:
    camera = await session.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    if not principal.may_write_department(camera.department_id):
        raise HTTPException(
            status_code=403, detail="Not permitted to write to this department"
        )

    outcomes = await MetadataService(session, enricher).enrich([camera], actor=principal)
    await session.commit()
    return _enrichment_report(outcomes)


@router.post(
    "/enrich",
    response_model=EnrichmentReport,
    summary="Derive metadata for a filtered set of cameras",
    description=(
        "Same derivation applied in bulk. `limit` is capped because each camera "
        "costs one manifest fetch and one segment decode against the source."
    ),
)
async def enrich_cameras(
    filters: CameraFilter = Depends(camera_filter),
    limit: int = Query(50, ge=1, le=500),
    principal: Principal = Depends(require_scope("cameras:write")),
    session: AsyncSession = Depends(get_session),
    enricher: StreamEnricher = Depends(get_enricher),
) -> EnrichmentReport:
    repo = CameraRepository(session)
    rows = await repo.list(filters, limit=limit, offset=0)
    # Filtering after the query rather than in it: a dept_admin asking for a wider
    # set gets what they may write, not a 403 for the whole request.
    writable = [c for c in rows if principal.may_write_department(c.department_id)]

    outcomes = await MetadataService(session, enricher).enrich(
        list(writable), actor=principal
    )
    await session.commit()
    return _enrichment_report(outcomes)
