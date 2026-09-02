from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.core.config import settings
from app.schemas.common import ErrorDetail


@dataclass
class ValidationResult:
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[ErrorDetail] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _mapping_hint(
    field: str,
    column_map: dict[str, str] | None,
    source_columns: list[str] | None,
) -> str:
    """Explain a missing required field in terms of the operator's own file."""
    if not column_map:
        return ""

    # column_map is {their column: our field}; we need the reverse.
    expected = [src for src, dest in column_map.items() if dest == field]
    if not expected:
        return (
            f" This department's field mapping does not say which column supplies "
            f"{field}; add one in the department's field mapping."
        )

    present = set(source_columns or [])
    if present and not present.intersection(expected):
        found = ", ".join(sorted(present)[:8]) or "none"
        return (
            f" This department reads it from column {' or '.join(repr(e) for e in expected)}, "
            f"which is not in the file. Columns found: {found}."
        )
    return ""


class CameraValidator:
    # DATE columns fed from CSV or JSON arrive as strings. Without coercion the
    # failure surfaces at flush time as an opaque asyncpg encoding error rather
    # than as a row error the operator can act on.
    DATE_FIELDS = ("install_date", "amc_expiry_date")
    INT_FIELDS = ("retention_days",)
    BOOL_FIELDS = ("has_night_vision",)

    TRUE_WORDS = frozenset({"1", "true", "yes", "y", "t"})
    FALSE_WORDS = frozenset({"0", "false", "no", "n", "f"})

    REQUIRED = ("external_camera_id", "latitude", "longitude")

    def validate(
        self,
        values: dict[str, Any],
        column_map: dict[str, str] | None = None,
        source_columns: list[str] | None = None,
    ) -> ValidationResult:
        """Validate one resolved record.

        `column_map` and `source_columns` are optional and exist only to make the
        error actionable. "external_camera_id is required" is true but useless to
        someone looking at a spreadsheet with no such column: what they need to
        know is which of *their* columns this department reads it from, and that
        the file did not contain it.
        """
        result = ValidationResult(values=dict(values))

        for name in self.REQUIRED:
            if values.get(name) in (None, ""):
                result.errors.append(
                    ErrorDetail(
                        code="missing_required_field",
                        message=f"{name} is required."
                        + _mapping_hint(name, column_map, source_columns),
                        field=name,
                    )
                )

        coords: dict[str, float] = {}
        for name, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
            raw = values.get(name)
            if raw in (None, ""):
                continue
            try:
                coords[name] = float(raw)
            except (TypeError, ValueError):
                result.errors.append(
                    ErrorDetail(
                        code="invalid_coordinate",
                        message=f"{name} {raw!r} is not a number.",
                        field=name,
                    )
                )
                continue
            if not low <= coords[name] <= high:
                result.errors.append(
                    ErrorDetail(
                        code="invalid_coordinate",
                        message=f"{name} {coords[name]} is out of range.",
                        field=name,
                    )
                )

        if {"latitude", "longitude"} <= coords.keys():
            min_lon, min_lat, max_lon, max_lat = settings.gujarat_bbox
            inside = (
                min_lat <= coords["latitude"] <= max_lat
                and min_lon <= coords["longitude"] <= max_lon
            )
            if not inside:
                result.errors.append(
                    ErrorDetail(
                        code="outside_gujarat",
                        message=(
                            f"Point ({coords['latitude']}, {coords['longitude']}) falls "
                            "outside the Gujarat bounding box."
                        ),
                        field="location",
                    )
                )
            else:
                result.values["latitude"] = coords["latitude"]
                result.values["longitude"] = coords["longitude"]

        for name, low, high, inclusive_high in (
            ("azimuth_deg", 0, 360, False),
            ("fov_deg", 0, 360, True),
            ("range_m", 0, 100_000, True),
            ("height_m", 0, 200, True),
        ):
            raw = values.get(name)
            if raw in (None, ""):
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError):
                result.errors.append(
                    ErrorDetail(
                        code="invalid_number",
                        message=f"{name} {raw!r} is not a number.",
                        field=name,
                    )
                )
                continue
            upper_ok = number <= high if inclusive_high else number < high
            lower_ok = number >= low if name == "azimuth_deg" else number > low
            if not (lower_ok and upper_ok):
                result.errors.append(
                    ErrorDetail(
                        code="out_of_range",
                        message=f"{name} {number} is out of range.",
                        field=name,
                    )
                )
            else:
                result.values[name] = number

        for name in self.INT_FIELDS:
            raw = values.get(name)
            if raw in (None, "") or isinstance(raw, bool):
                continue
            try:
                result.values[name] = int(str(raw).strip())
            except ValueError:
                result.errors.append(
                    ErrorDetail(
                        code="invalid_integer",
                        message=f"{name} {raw!r} is not a whole number.",
                        field=name,
                    )
                )

        for name in self.BOOL_FIELDS:
            raw = values.get(name)
            if raw in (None, ""):
                continue
            if isinstance(raw, bool):
                result.values[name] = raw
                continue
            word = str(raw).strip().lower()
            if word in self.TRUE_WORDS:
                result.values[name] = True
            elif word in self.FALSE_WORDS:
                result.values[name] = False
            else:
                # Not silently False: a department writing "sometimes" needs telling,
                # not a fabricated answer about whether the camera sees at night.
                result.errors.append(
                    ErrorDetail(
                        code="invalid_boolean",
                        message=f"{name} {raw!r} is not a yes/no value.",
                        field=name,
                    )
                )

        for name in self.DATE_FIELDS:
            raw = values.get(name)
            if raw in (None, ""):
                continue
            if isinstance(raw, date) and not isinstance(raw, datetime):
                result.values[name] = raw
                continue
            try:
                result.values[name] = date.fromisoformat(str(raw).strip()[:10])
            except ValueError:
                result.errors.append(
                    ErrorDetail(
                        code="invalid_date",
                        message=f"{name} {raw!r} is not an ISO date (YYYY-MM-DD).",
                        field=name,
                    )
                )

        return result
