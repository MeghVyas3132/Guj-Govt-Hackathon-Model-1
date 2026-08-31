from uuid import uuid4

from app.adapters.csv_adapter import CsvAdapter
from app.core.enums import SourceType


def test_parses_rows_with_one_based_row_numbers():
    csv_bytes = b"cam_id,lat,lng\nA-1,23.02,72.57\nA-2,22.30,73.19\n"
    dept_id = uuid4()

    records = CsvAdapter(csv_bytes, filename="amc.csv").parse(dept_id)

    assert len(records) == 2
    assert records[0].payload == {"cam_id": "A-1", "lat": "23.02", "lng": "72.57"}
    assert records[0].row_number == 2  # header is row 1
    assert records[0].source_type == SourceType.CSV
    assert records[0].source_ref == "amc.csv"


def test_strips_whitespace_from_headers_and_values():
    csv_bytes = b" cam_id , lat \n A-1 , 23.02 \n"
    records = CsvAdapter(csv_bytes, filename="x.csv").parse(uuid4())
    assert records[0].payload == {"cam_id": "A-1", "lat": "23.02"}


def test_skips_fully_blank_rows():
    csv_bytes = b"cam_id,lat\nA-1,23.02\n,\n"
    records = CsvAdapter(csv_bytes, filename="x.csv").parse(uuid4())
    assert len(records) == 1
