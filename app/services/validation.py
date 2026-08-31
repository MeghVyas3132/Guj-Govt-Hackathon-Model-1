from dataclasses import dataclass, field
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


class CameraValidator:
    REQUIRED = ("external_camera_id", "latitude", "longitude")

    def validate(self, values: dict[str, Any]) -> ValidationResult:
        result = ValidationResult(values=dict(values))

        for name in self.REQUIRED:
            if values.get(name) in (None, ""):
                result.errors.append(
                    ErrorDetail(
                        code="missing_required_field",
                        message=f"{name} is required.",
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

        return result
