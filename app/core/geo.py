"""Decoding a stored geography value back to coordinates.

A `Geography` attribute does not have one representation. Loaded from the database it
is a `WKBElement`; assigned in the current session and not yet expired it is still the
plain `"SRID=4326;POINT(lon lat)"` string that was written. `geoalchemy2.shape.to_shape`
handles the first and raises `TypeError` on the second, so any code path that creates a
camera and serialises it in the same session crashes on a value that decodes fine
everywhere else.
"""

from typing import Any

from geoalchemy2.elements import WKBElement, WKTElement
from geoalchemy2.shape import to_shape
from shapely import wkt as shapely_wkt
from shapely.geometry import Point


def to_point(stored: Any) -> Point | None:
    """Return the stored geography as a shapely Point, or None if undecodable."""
    if stored is None:
        return None
    if isinstance(stored, WKBElement | WKTElement):
        return to_shape(stored)
    if isinstance(stored, str):
        try:
            return shapely_wkt.loads(stored.split(";", 1)[-1])
        except Exception:  # noqa: BLE001 -- any parse failure means "cannot decode"
            return None
    return None


def to_lonlat(stored: Any) -> tuple[float, float] | None:
    """Return (longitude, latitude), or None if the value cannot be decoded."""
    point = to_point(stored)
    return None if point is None else (point.x, point.y)
