import os
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.csv_adapter import CsvAdapter
from app.core.db import get_session
from app.core.deps import require_scope
from app.core.enums import SourceType
from app.models.department import Department
from app.schemas.auth import Principal
from app.schemas.camera import CameraCreate
from app.schemas.ingestion import IngestReport, RawCameraRecord
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# Left as None in production so httpx picks its own transport. Tests replace it with
# an httpx.MockTransport, which is how the sync route is exercised without the live
# catalogue's session cookie -- a module attribute rather than a dependency because
# it is a test seam, not a request-scoped input.
_ADAPTER_TRANSPORT: httpx.AsyncBaseTransport | None = None

# Defaults to the real sandbox; the catalogue itself is behind a session cookie
# obtained by signing in, supplied via SENTINEL_SESSION_COOKIE.
SENTINEL_CATALOGUE_URL = os.environ.get(
    "SENTINEL_CATALOGUE_URL", "https://cctv.corp8.cloud/cameras.json"
)


async def _department(
    session: AsyncSession, department_id: UUID, principal: Principal
) -> Department:
    department = await session.get(Department, department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")
    # Holding cameras:write is not enough: it is departmental for every role below
    # super_admin, so an admin for one department cannot onboard into another.
    if not principal.may_write_department(department.id):
        raise HTTPException(
            status_code=403,
            detail="You may only onboard cameras into your own department.",
        )
    return department


async def _run_csv(
    session: AsyncSession,
    department_id: UUID,
    file: UploadFile,
    mode: Literal["validate_only", "commit"],
    principal: Principal,
) -> IngestReport:
    department = await _department(session, department_id, principal)
    records = CsvAdapter(await file.read(), file.filename or "upload.csv").parse(
        department_id
    )
    return await IngestionService(session).ingest(
        records, department, mode=mode, actor=principal
    )


@router.post(
    "/preview",
    response_model=IngestReport,
    summary="Validate a file and report row-level results without writing",
)
async def preview(
    department_id: UUID = Query(...),
    principal: Principal = Depends(require_scope("cameras:write")),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    return await _run_csv(session, department_id, file, "validate_only", principal)


@router.post("/import", response_model=IngestReport, summary="Commit a validated file")
async def import_file(
    department_id: UUID = Query(...),
    principal: Principal = Depends(require_scope("cameras:write")),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    return await _run_csv(session, department_id, file, "commit", principal)


def _api_payload(item: CameraCreate) -> dict:
    """Flatten one CameraCreate into the flat dict shape the resolver expects.

    department_id and operator_department_id are routing, not camera attributes — the
    department is already the query parameter — so they are dropped rather than left to
    fall through into metadata. The caller's own metadata is merged flat, because the
    resolver's passthrough is what puts unmapped keys into metadata; nesting it would
    store metadata inside metadata. Real columns win a name collision.
    """
    payload = item.model_dump(mode="json", exclude_none=True)
    payload.pop("department_id", None)
    payload.pop("operator_department_id", None)
    for key, value in (payload.pop("metadata", None) or {}).items():
        payload.setdefault(key, value)
    return payload


@router.post(
    "/bulk",
    response_model=IngestReport,
    summary="API onboarding for departmental systems",
    description="Same validation and normalization as CSV upload — only the source differs.",
)
async def bulk(
    payload: list[CameraCreate],
    department_id: UUID = Query(...),
    principal: Principal = Depends(require_scope("cameras:write")),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    department = await _department(session, department_id, principal)
    records = [
        RawCameraRecord(
            payload=_api_payload(item),
            department_id=department_id,
            source_type=SourceType.API,
        )
        for item in payload
    ]
    return await IngestionService(session).ingest(
        records, department, mode="commit", actor=principal
    )


# The adapter sync route now lives on /connectors/{code}/sync, driven by a
# source_connectors row rather than a hardcoded vendor name. This path is kept as
# a redirect so anything already pointing at it keeps working.
@router.post(
    "/adapters/{adapter_code}/sync",
    include_in_schema=False,
    deprecated=True,
)
async def sync_adapter_moved(adapter_code: str) -> RedirectResponse:
    return RedirectResponse(
        url=f"/api/v1/connectors/{adapter_code}/sync", status_code=308
    )
