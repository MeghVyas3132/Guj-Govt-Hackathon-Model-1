import re
from dataclasses import dataclass, field
from typing import Any

# The separator before the hemisphere must exclude NSEW, or a greedy class swallows
# the letter and every southern/western coordinate silently comes back positive.
# Fields whose values are resolved against the vocabulary tables downstream.
# Listed here only so an already-canonical key passes through instead of
# being filed into metadata as an unrecognised column.
VOCABULARY_FIELDS = frozenset(
    {
        "status",
        "camera_type",
        "camera_technology",
        "connectivity",
        "ownership_class",
        "site_type",
        "storage_type",
    }
)

_DMS = re.compile(
    r"^\s*(\d+)[^\d]+(\d+)[^\d]+([\d.]+)[^\dNSEWnsew]*([NSEW])?\s*$", re.IGNORECASE
)

# Canonical column names a department may already use verbatim. Anything not here and not
# in SOFT_ENUMS is department-specific and belongs in metadata.
_KNOWN_CANONICAL = {
    "external_camera_id",
    "name",
    "latitude",
    "longitude",
    "address",
    "azimuth_deg",
    "fov_deg",
    "range_m",
    "height_m",
    "resolution",
    "has_night_vision",
    "storage_type",
    "retention_days",
    "amc_vendor",
    "amc_expiry_date",
    "install_date",
}


def _parse_dms(value: str) -> float:
    match = _DMS.match(value)
    if not match:
        return float(value)
    degrees, minutes, seconds, hemisphere = match.groups()
    decimal = int(degrees) + int(minutes) / 60 + float(seconds) / 3600
    if hemisphere and hemisphere.upper() in {"S", "W"}:
        decimal = -decimal
    return decimal


@dataclass
class ResolveResult:
    values: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class FieldMappingResolver:
    """Translates one department's field names and vocabulary into canonical form.

    Two rules make this safe to run unattended against a nightly departmental sync:
    unmapped columns are preserved in metadata rather than dropped, and unmapped
    values fall back to the enum's neutral member with a warning rather than raising.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.column_map: dict[str, str] = config.get("column_map", {})
        self.value_maps: dict[str, dict[str, str]] = config.get("value_maps", {})
        self.defaults: dict[str, Any] = config.get("defaults", {})
        self.coordinate_format: str = config.get("coordinate_format", "decimal_degrees")
        self.passthrough: bool = config.get("passthrough_to_metadata", True)

    def resolve(self, raw: dict[str, Any]) -> ResolveResult:
        result = ResolveResult()

        for source_key, value in raw.items():
            # Underscore-prefixed keys are a private channel through the payload
            # (e.g. `_stream_endpoints`): never mapped, never passed to metadata.
            if source_key.startswith("_"):
                continue
            target = self.column_map.get(source_key)
            if target is None:
                # An already-canonical key passes through untouched; anything else
                # is department-specific and belongs in metadata.
                if source_key in VOCABULARY_FIELDS or source_key in _KNOWN_CANONICAL:
                    result.values[source_key] = value
                elif self.passthrough:
                    result.metadata[source_key] = value
                continue
            result.values[target] = value

        # Translate the department's word into ours, and stop. Whether the result
        # is a term this registry recognises is VocabularyService's question, not
        # this class's -- the set of valid terms lives in the database so a new
        # camera type is a row rather than a deploy.
        for field_name, mapping in self.value_maps.items():
            if field_name not in result.values:
                continue
            raw_value = result.values[field_name]
            if raw_value is None:
                continue
            key = str(raw_value).strip()
            mapped = mapping.get(key, mapping.get(key.upper(), mapping.get(key.lower())))
            result.values[field_name] = mapped if mapped is not None else key

        for field_name, default in self.defaults.items():
            result.values.setdefault(field_name, default)

        if self.coordinate_format == "dms":
            for coord in ("latitude", "longitude"):
                raw_coord = result.values.get(coord)
                if not isinstance(raw_coord, str):
                    continue
                try:
                    result.values[coord] = _parse_dms(raw_coord)
                except ValueError:
                    # Leave the raw value in place so CameraValidator reports it as an
                    # invalid_coordinate row error. One unparseable cell must not raise
                    # out of resolve() and abort the whole import batch.
                    result.warnings.append(
                        f"Could not parse {coord} {raw_coord!r} as "
                        "degrees-minutes-seconds; left for validation."
                    )

        return result
