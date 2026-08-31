from datetime import UTC, datetime
from typing import Any, Literal

from geoalchemy2.elements import WKBElement, WKTElement
from geoalchemy2.shape import to_shape
from shapely import wkt as shapely_wkt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera
from app.models.department import Department
from app.models.field_mapping import FieldMapping
from app.repositories.camera import CameraRepository
from app.schemas.ingestion import IngestReport, RawCameraRecord, RowResult
from app.services.normalization import FieldMappingResolver
from app.services.validation import CameraValidator

# Columns the persister is allowed to write from a normalized draft.
_WRITABLE = {
    "name",
    "address",
    "camera_type",
    "camera_technology",
    "azimuth_deg",
    "fov_deg",
    "range_m",
    "height_m",
    "resolution",
    "has_night_vision",
    "connectivity",
    "storage_type",
    "retention_days",
    "ownership_class",
    "site_type",
    "amc_vendor",
    "amc_expiry_date",
    "install_date",
}

# ~1.1 cm at the equator — finer than any coordinate a department can supply, coarse
# enough to absorb float noise from the PostGIS round trip.
_COORD_PRECISION = 7


def _point_moved(stored: Any, longitude: float, latitude: float) -> bool:
    """Has the camera actually moved?

    `Camera.location` is written as `"SRID=4326;POINT(lon lat)"` but comes back from
    PostGIS as a GeoAlchemy2 `WKBElement` whose `str()` is hex WKB — GeoAlchemy2 expires
    geography attributes on flush, so even the row just inserted in this session reloads
    that way. Comparing string forms would therefore always differ, reporting every
    re-import as `updated` and silently breaking the idempotency guarantee. Compare
    decoded coordinates instead.
    """
    if stored is None:
        return True
    if isinstance(stored, WKBElement | WKTElement):
        point = to_shape(stored)
    elif isinstance(stored, str):
        # Assigned in this session and not yet flushed: "SRID=4326;POINT(lon lat)".
        try:
            point = shapely_wkt.loads(stored.split(";", 1)[-1])
        except Exception:
            return True
    else:
        return True
    return (round(point.x, _COORD_PRECISION), round(point.y, _COORD_PRECISION)) != (
        round(longitude, _COORD_PRECISION),
        round(latitude, _COORD_PRECISION),
    )


class IngestionService:
    """The one function every onboarding path calls.

    CSV upload, manual form, REST POST and adapter pulls all build RawCameraRecord
    and land here, so validation and normalization cannot drift apart by source.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.cameras = CameraRepository(session)
        self.validator = CameraValidator()

    async def _resolver(self, department: Department) -> tuple[FieldMappingResolver, int]:
        stmt = (
            select(FieldMapping)
            .where(FieldMapping.department_id == department.id, FieldMapping.is_active)
            .order_by(FieldMapping.version.desc())
        )
        mapping = (await self.session.execute(stmt)).scalars().first()
        if mapping is None:
            return FieldMappingResolver({}), 0
        return FieldMappingResolver(mapping.config), mapping.version

    async def ingest(
        self,
        records: list[RawCameraRecord],
        department: Department,
        mode: Literal["validate_only", "commit"],
    ) -> IngestReport:
        resolver, mapping_version = await self._resolver(department)
        report = IngestReport(total=len(records))

        for record in records:
            resolved = resolver.resolve(record.payload)
            validated = self.validator.validate(resolved.values)
            warnings = resolved.warnings + validated.warnings
            external_id = resolved.values.get("external_camera_id")

            if not validated.is_valid:
                report.failed += 1
                report.rows.append(
                    RowResult(
                        row_number=record.row_number,
                        external_camera_id=external_id,
                        outcome="failed",
                        errors=validated.errors,
                        warnings=warnings,
                    )
                )
                continue

            if mode == "validate_only":
                existing = await self.cameras.get_by_external_id(department.id, str(external_id))
                outcome = "updated" if existing else "created"
                setattr(report, outcome, getattr(report, outcome) + 1)
                report.rows.append(
                    RowResult(
                        row_number=record.row_number,
                        external_camera_id=external_id,
                        outcome=outcome,
                        warnings=warnings,
                    )
                )
                continue

            outcome = await self._persist(
                validated.values, resolved.metadata, record, department, mapping_version
            )
            setattr(report, outcome, getattr(report, outcome) + 1)
            report.rows.append(
                RowResult(
                    row_number=record.row_number,
                    external_camera_id=external_id,
                    outcome=outcome,
                    warnings=warnings,
                )
            )

        if mode == "commit":
            await self.session.commit()
        return report

    async def _persist(
        self,
        values: dict[str, Any],
        metadata: dict[str, Any],
        record: RawCameraRecord,
        department: Department,
        mapping_version: int,
    ) -> str:
        external_id = str(values["external_camera_id"])
        wkt = f"SRID=4326;POINT({values['longitude']} {values['latitude']})"
        status = values.get("status")

        camera = await self.cameras.get_by_external_id(department.id, external_id)
        if camera is None:
            camera = Camera(
                camera_uid=await self.cameras.next_uid(department.code),
                department_id=department.id,
                external_camera_id=external_id,
                location=wkt,
                metadata_=metadata,
                source_type=record.source_type,
                field_mapping_version=mapping_version,
            )
            if status is not None:
                camera.current_status = str(status)
                camera.status_since = datetime.now(UTC)
            for key in _WRITABLE & values.keys():
                setattr(camera, key, values[key])
            self.cameras.add(camera)
            await self.session.flush()
            return "created"

        changed = False
        if _point_moved(camera.location, float(values["longitude"]), float(values["latitude"])):
            camera.location = wkt
            changed = True
        for key in _WRITABLE & values.keys():
            if getattr(camera, key) != values[key]:
                setattr(camera, key, values[key])
                changed = True
        if metadata and camera.metadata_ != {**camera.metadata_, **metadata}:
            camera.metadata_ = {**camera.metadata_, **metadata}
            changed = True
        if status is not None and camera.current_status != str(status):
            camera.current_status = str(status)
            camera.status_since = datetime.now(UTC)
            changed = True

        if changed:
            await self.session.flush()
            return "updated"
        return "skipped"
