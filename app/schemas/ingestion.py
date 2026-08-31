from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.core.enums import SourceType
from app.schemas.common import ErrorDetail


@dataclass
class RawCameraRecord:
    """The single entry shape for every onboarding path."""

    payload: dict[str, Any]
    department_id: UUID
    source_type: SourceType
    source_ref: str | None = None
    row_number: int | None = None


class RowResult(BaseModel):
    row_number: int | None = None
    external_camera_id: str | None = None
    outcome: str  # created | updated | skipped | failed
    errors: list[ErrorDetail] = []
    warnings: list[str] = []


class IngestReport(BaseModel):
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    rows: list[RowResult] = []

    @property
    def is_dry_run_clean(self) -> bool:
        return self.failed == 0
