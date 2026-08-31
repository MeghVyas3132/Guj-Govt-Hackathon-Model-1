from typing import Protocol
from uuid import UUID

from app.schemas.ingestion import RawCameraRecord


class SourceAdapter(Protocol):
    """Turns some external representation into RawCameraRecords.

    Adapters never validate or normalize — that is IngestionService's job. This keeps
    every source funnelling through identical rules.
    """

    code: str

    async def fetch(self, department_id: UUID) -> list[RawCameraRecord]: ...
