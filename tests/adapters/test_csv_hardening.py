"""Adversarial tests for CSV onboarding.

A department sends whatever their system exports. In practice that means Excel
on a Windows machine with a regional locale, which produces files that are not
UTF-8, not comma-delimited, and not always rectangular. Every one of these must
either import correctly or fail with a message naming the problem -- never a
UnicodeDecodeError with a byte offset in it.
"""

from uuid import uuid4

import pytest

from app.adapters.csv_adapter import CsvAdapter

DEPT = uuid4()


def parse(content: bytes, filename: str = "upload.csv"):
    return CsvAdapter(content, filename).parse(DEPT)


def payloads(content: bytes):
    return [r.payload for r in parse(content)]


# ---- encodings ----

def test_plain_utf8_parses():
    assert payloads(b"cam_id,lat\nc1,23.0\n") == [{"cam_id": "c1", "lat": "23.0"}]


def test_a_utf8_bom_is_stripped():
    """Excel writes one on every "CSV UTF-8" save. Left in place it becomes part
    of the first header name, so that column silently never maps."""
    assert payloads(b"\xef\xbb\xbfcam_id,lat\nc1,23.0\n")[0]["cam_id"] == "c1"


def test_gujarati_text_survives():
    content = "cam_id,name\nc1,ચીમનભાઈ પુલ\n".encode()
    assert payloads(content)[0]["name"] == "ચીમનભાઈ પુલ"


def test_a_cp1252_file_is_decoded_rather_than_crashing():
    """Excel's default on a Windows machine outside a UTF-8 locale. The bytes are
    not valid UTF-8, and a strict decode raises before a single row is read."""
    # Byte 0x96 is an en dash in cp1252 and invalid as UTF-8.
    content = b"cam_id,name\nc1,Sardar Patel Bridge \x96 north\n"
    assert payloads(content)[0]["name"] == "Sardar Patel Bridge \u2013 north"


def test_a_utf16_file_is_decoded():
    """Excel's "Unicode Text" export. Decoded as UTF-8 it is either an error or
    a row of NUL-separated nonsense."""
    content = "cam_id,name\nc1,Paldi Circle\n".encode("utf-16")
    assert payloads(content)[0]["cam_id"] == "c1"


def test_a_latin1_file_is_decoded():
    content = "cam_id,name\nc1,Café Junction\n".encode("latin-1")
    assert payloads(content)[0]["cam_id"] == "c1"


# ---- delimiters ----

def test_a_semicolon_delimited_file_is_detected():
    """Excel uses ; as the list separator in most European and several Indian
    locales. Read as comma-delimited the whole row becomes one column."""
    rows = payloads(b"cam_id;lat;lng\nc1;23.0;72.5\n")
    assert rows == [{"cam_id": "c1", "lat": "23.0", "lng": "72.5"}]


def test_a_tab_delimited_file_is_detected():
    rows = payloads(b"cam_id\tlat\nc1\t23.0\n")
    assert rows == [{"cam_id": "c1", "lat": "23.0"}]


def test_a_comma_file_containing_semicolons_is_still_comma_delimited():
    """Delimiter detection must not be fooled by punctuation inside values."""
    rows = payloads(b'cam_id,name\nc1,"Ring Road; near flyover"\n')
    assert rows[0]["name"] == "Ring Road; near flyover"


# ---- shape ----

def test_crlf_line_endings_are_handled():
    assert len(parse(b"cam_id,lat\r\nc1,23.0\r\nc2,24.0\r\n")) == 2


def test_a_quoted_field_containing_a_comma_stays_one_value():
    rows = payloads(b'cam_id,name\nc1,"Paldi, Ahmedabad"\n')
    assert rows[0]["name"] == "Paldi, Ahmedabad"


def test_a_quoted_field_containing_a_newline_stays_one_row():
    rows = parse(b'cam_id,name\nc1,"Line one\nLine two"\n')
    assert len(rows) == 1
    assert "\n" in rows[0].payload["name"]


def test_an_escaped_quote_is_unescaped():
    rows = payloads(b'cam_id,name\nc1,"The ""Old"" Bridge"\n')
    assert rows[0]["name"] == 'The "Old" Bridge'


def test_surrounding_whitespace_is_trimmed_from_values():
    """A trailing space turns "online" into a vocabulary miss."""
    assert payloads(b"cam_id,status\nc1,  online  \n")[0]["status"] == "online"


def test_surrounding_whitespace_is_trimmed_from_headers():
    assert "cam_id" in payloads(b" cam_id , lat \nc1,23.0\n")[0]


def test_an_empty_file_yields_nothing():
    assert parse(b"") == []


def test_a_header_only_file_yields_nothing():
    assert parse(b"cam_id,lat,lng\n") == []


def test_blank_rows_are_skipped():
    """Excel exports trailing empty rows constantly."""
    assert len(parse(b"cam_id,lat\nc1,23.0\n,\n,\n")) == 1


def test_whitespace_only_rows_are_skipped():
    assert len(parse(b"cam_id,lat\nc1,23.0\n   ,  \n")) == 1


def test_a_short_row_leaves_the_missing_field_empty():
    """DictReader yields None for a missing value, which must not reach the
    validator as the string "None"."""
    rows = payloads(b"cam_id,lat,lng\nc1,23.0\n")
    assert rows[0]["lng"] == ""


def test_extra_columns_beyond_the_header_do_not_crash():
    assert payloads(b"cam_id,lat\nc1,23.0,72.5,extra\n")[0]["cam_id"] == "c1"


def test_duplicate_headers_do_not_crash():
    rows = payloads(b"cam_id,cam_id\nc1,c2\n")
    assert rows[0]["cam_id"] in ("c1", "c2")


def test_an_unnamed_trailing_column_is_dropped():
    """A trailing comma in the header row produces an empty column name."""
    rows = payloads(b"cam_id,lat,\nc1,23.0,x\n")
    assert "" not in rows[0] or rows[0].get("") == "x"


def test_a_nul_byte_does_not_raise():
    """csv raises _csv.Error on an embedded NUL, which surfaces as a 500."""
    rows = parse(b"cam_id,lat\nc1\x00,23.0\n")
    assert len(rows) == 1


def test_a_very_long_field_is_accepted():
    """csv has a 128KB field limit that raises rather than truncating."""
    long_name = b"x" * 200_000
    rows = parse(b"cam_id,name\nc1," + long_name + b"\n")
    assert len(rows[0].payload["name"]) == 200_000


def test_a_large_file_parses():
    body = b"cam_id,lat,lng\n" + b"".join(
        f"c{i},23.0{i},72.5{i}\n".encode() for i in range(20_000)
    )
    assert len(parse(body)) == 20_000


# ---- provenance, which the audit trail depends on ----

def test_row_numbers_are_one_based_on_the_file_including_the_header():
    """An operator fixing an error needs the line number their spreadsheet shows."""
    rows = parse(b"cam_id\nc1\nc2\n")
    assert [r.row_number for r in rows] == [2, 3]


def test_row_numbers_stay_aligned_when_blank_rows_are_skipped():
    """Skipping a row must not shift the numbering of everything after it."""
    rows = parse(b"cam_id\nc1\n\nc3\n")
    assert [r.payload["cam_id"] for r in rows] == ["c1", "c3"]
    assert [r.row_number for r in rows] == [2, 4]


def test_the_filename_is_recorded_as_the_source_reference():
    assert parse(b"cam_id\nc1\n", "amc-jan.csv")[0].source_ref == "amc-jan.csv"


def test_every_record_carries_the_requested_department():
    dept = uuid4()
    assert CsvAdapter(b"cam_id\nc1\n", "f.csv").parse(dept)[0].department_id == dept


def test_the_source_type_is_csv():
    from app.core.enums import SourceType

    assert parse(b"cam_id\nc1\n")[0].source_type == SourceType.CSV
