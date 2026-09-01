"""Reading a spreadsheet a department actually sent.

The happy path -- UTF-8, comma-delimited, rectangular -- is the one case that
rarely arrives. What arrives is Excel output from a Windows machine in a
regional locale: cp1252 or UTF-16 bytes, semicolons for delimiters, a BOM, and
trailing blank rows. None of that is exotic, and every one of them either
crashes a strict reader or silently produces a single unusable column.

So the file is sniffed rather than assumed, and the fallbacks are ordered from
most to least specific. Nothing here guesses at content: only at how to turn
bytes into rows.
"""

import csv
import io
import sys
from uuid import UUID

from app.core.enums import SourceType
from app.schemas.ingestion import RawCameraRecord

# A base64 image or a long descriptive note blows through csv's 128KB default,
# which raises rather than truncating. Bounded well below memory exhaustion.
_FIELD_LIMIT = 10 * 1024 * 1024
csv.field_size_limit(min(_FIELD_LIMIT, sys.maxsize))

# Only these are plausible column separators in a camera export. Letting the
# sniffer consider any punctuation makes it pick "." out of a file of decimal
# coordinates.
_DELIMITERS = ",;\t|"

_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")


class CsvAdapter:
    code = "csv"

    def __init__(self, content: bytes, filename: str) -> None:
        self.content = content
        self.filename = filename

    def _decode(self) -> str:
        """Bytes to text, trying the encodings a department plausibly sent.

        cp1252 before latin-1 because it is what Excel on Windows writes, and it
        maps the 0x80-0x9F range to real punctuation -- an en dash in a road name
        becomes "–" rather than a control character. latin-1 is last because it
        cannot fail, which makes it the terminator for this chain rather than a
        choice on its merits.
        """
        if self.content[:2] in _UTF16_BOMS:
            return self.content.decode("utf-16")
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return self.content.decode(encoding)
            except UnicodeDecodeError:
                continue
        # latin-1 decodes any byte sequence, so this is unreachable in practice.
        return self.content.decode("utf-8", errors="replace")

    @staticmethod
    def _dialect(text: str) -> type[csv.Dialect] | csv.Dialect:
        """Detect the delimiter from a sample of the file.

        A semicolon-delimited file read as comma-delimited does not fail -- it
        produces one column named "cam_id;lat;lng", which then maps to nothing
        and reports every row as missing an identifier. Silent, and baffling.
        """
        sample = text[:8192]
        try:
            return csv.Sniffer().sniff(sample, delimiters=_DELIMITERS)
        except csv.Error:
            # A single-column file gives the sniffer nothing to work with, and is
            # read correctly by the default dialect anyway.
            return csv.excel

    def parse(self, department_id: UUID) -> list[RawCameraRecord]:
        # NUL bytes make csv raise _csv.Error mid-iteration, losing the whole
        # upload. They carry no information here, so they are dropped.
        text = self._decode().replace("\x00", "")
        reader = csv.DictReader(io.StringIO(text), dialect=self._dialect(text))
        records: list[RawCameraRecord] = []

        for row in reader:
            payload = {
                (key or "").strip(): (value or "").strip()
                for key, value in row.items()
                # A row with more fields than headers collects the surplus under
                # None. There is no column name to map it to, so it is dropped.
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
                    # reader.line_num is the physical line in the file, so a
                    # reported row number matches what the operator sees in their
                    # spreadsheet even when blank rows were skipped.
                    row_number=reader.line_num,
                )
            )
        return records
