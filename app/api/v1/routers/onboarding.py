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
            payload=item.model_dump(mode="json", exclude_none=True),
            department_id=department_id,
            source_type=SourceType.API,
        )
        for item in payload
    ]
    return await IngestionService(session).ingest(records, department, mode="commit")
