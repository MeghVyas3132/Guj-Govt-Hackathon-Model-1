from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.csv_adapter import CsvAdapter
from app.core.db import get_session
from app.core.enums import SourceType
from app.models.department import Department
from app.schemas.camera import CameraCreate
from app.schemas.ingestion import IngestReport, RawCameraRecord
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


async def _department(session: AsyncSession, department_id: UUID) -> Department:
    department = await session.get(Department, department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")
    return department


async def _run_csv(
    session: AsyncSession,
    department_id: UUID,
    file: UploadFile,
    mode: Literal["validate_only", "commit"],
) -> IngestReport:
    department = await _department(session, department_id)
    records = CsvAdapter(await file.read(), file.filename or "upload.csv").parse(
        department_id
    )
    return await IngestionService(session).ingest(records, department, mode=mode)


@router.post(
    "/preview",
    response_model=IngestReport,
    summary="Validate a file and report row-level results without writing",
)
async def preview(
    department_id: UUID = Query(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    return await _run_csv(session, department_id, file, "validate_only")


@router.post("/import", response_model=IngestReport, summary="Commit a validated file")
async def import_file(
    department_id: UUID = Query(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    return await _run_csv(session, department_id, file, "commit")


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
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    department = await _department(session, department_id)
    records = [
        RawCameraRecord(
            payload=_api_payload(item),
            department_id=department_id,
            source_type=SourceType.API,
        )
        for item in payload
    ]
    return await IngestionService(session).ingest(records, department, mode="commit")
