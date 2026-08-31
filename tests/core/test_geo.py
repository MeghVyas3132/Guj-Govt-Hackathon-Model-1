from geoalchemy2.elements import WKTElement

from app.core.geo import to_lonlat, to_point

WKT = "SRID=4326;POINT(72.5714 23.0225)"


def test_decodes_a_wkt_element():
    assert to_lonlat(WKTElement(WKT, extended=True)) == (72.5714, 23.0225)


def test_decodes_the_raw_string_a_pending_session_still_holds():
    """This is the case geoalchemy2.shape.to_shape rejects with TypeError."""
    assert to_lonlat(WKT) == (72.5714, 23.0225)


def test_decodes_a_string_without_the_srid_prefix():
    assert to_lonlat("POINT(72.5714 23.0225)") == (72.5714, 23.0225)


def test_none_and_garbage_decode_to_none_rather_than_raising():
    assert to_point(None) is None
    assert to_lonlat("not a geometry") is None
    assert to_lonlat(object()) is None
