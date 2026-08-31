"""CSV rendering of a filtered camera result set.

The columns are the ones a department actually reconciles against its own records
during onboarding -- identity, position, hardware, status and the AMC fields -- not a
dump of the ORM. Internal keys (id, department_id, lifecycle_state, the source
bookkeeping) are deliberately absent: this file goes to people, and a UUID column
invites them to paste it into a spreadsheet and treat it as an asset number.
"""

import csv
import io
from collections.abc import Iterable

from geoalchemy2.shape import to_shape

from app.models.camera import Camera

COLUMNS = [
    "camera_uid",
    "external_camera_id",
    "name",
    "latitude",
    "longitude",
    "address",
    "camera_type",
    "camera_technology",
    "current_status",
    "status_since",
    "connectivity",
    "ownership_class",
    "site_type",
    "resolution",
    "retention_days",
    "amc_vendor",
    "amc_expiry_date",
    "install_date",
]


def cameras_to_csv(cameras: Iterable[Camera]) -> str:
    """Render rows in the order given -- which, for a radius search, is nearest first.

    The header is written before the loop, so an empty result set is still a valid CSV
    a spreadsheet will open with the right columns rather than a zero-byte file.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for camera in cameras:
        # latitude/longitude are not attributes -- they are derived from the
        # GEOGRAPHY column, so getattr would silently emit two empty columns.
        point = to_shape(camera.location)
        row = {column: getattr(camera, column, None) for column in COLUMNS}
        row["latitude"] = point.y
        row["longitude"] = point.x
        writer.writerow(row)
    return buffer.getvalue()
