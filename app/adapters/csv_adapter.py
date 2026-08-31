import csv
import io
from uuid import UUID

from app.core.enums import SourceType
from app.schemas.ingestion import RawCameraRecord


class CsvAdapter:
    code = "csv"

    def __init__(self, content: bytes, filename: str) -> None:
        self.content = content
        self.filename = filename

    def parse(self, department_id: UUID) -> list[RawCameraRecord]:
        text = self.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        records: list[RawCameraRecord] = []

        for offset, row in enumerate(reader, start=2):
            payload = {
                (key or "").strip(): (value or "").strip()
                for key, value in row.items()
                if key is not None
            }
            if not any(payload.values()):
                continue
            records.append(
                RawCameraRecord(
                    payload=payload,
                    department_id=department_id,
                    source_type=SourceType.CSV,
                    source_ref=self.filename,
                    row_number=offset,
                )
            )
        return records
