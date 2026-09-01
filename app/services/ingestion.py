from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.geo import to_lonlat
from app.models.camera import Camera
from app.models.department import Department
from app.models.field_mapping import FieldMapping
from app.models.stream_endpoint import StreamEndpoint
from app.repositories.camera import CameraRepository
from app.schemas.auth import Principal
from app.schemas.ingestion import IngestReport, RawCameraRecord, RowResult
from app.services.audit import AuditService
from app.services.geocoding import DistrictGeocoder
from app.services.normalization import VOCABULARY_FIELDS, FieldMappingResolver
from app.services.validation import CameraValidator
from app.services.vocabulary import VocabularyService


def _snapshot(camera: Camera) -> dict[str, Any]:
    """The fields worth diffing in an audit entry. Deliberately not everything:
    a trail nobody can read at a glance is a trail nobody reads."""
    return {
        "external_camera_id": camera.external_camera_id,
        "name": camera.name,
        "camera_type": camera.camera_type,
        "current_status": camera.current_status,
        "connectivity": camera.connectivity,
        "site_type": camera.site_type,
        "ownership_class": camera.ownership_class,
        "lifecycle_state": camera.lifecycle_state,
    }


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
    """True when the stored point differs from the supplied coordinates.

    An undecodable stored value counts as moved: a redundant write is the safe
    direction, a silently dropped one is not.
    """
    coords = to_lonlat(stored)
    if coords is None:
        return True
    return (round(coords[0], _COORD_PRECISION), round(coords[1], _COORD_PRECISION)) != (
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
        self.geocoder = DistrictGeocoder(session)
        self.vocabulary = VocabularyService(session)
        self.audit = AuditService(session)

    async def _resolver(
        self, department: Department
    ) -> tuple[FieldMappingResolver, int, dict[str, Any]]:
        stmt = (
            select(FieldMapping)
            .where(FieldMapping.department_id == department.id, FieldMapping.is_active)
            .order_by(FieldMapping.version.desc())
        )
        mapping = (await self.session.execute(stmt)).scalars().first()
        if mapping is None:
            return FieldMappingResolver({}), 0, {}
        return FieldMappingResolver(mapping.config), mapping.version, mapping.config

    async def ingest(
        self,
        records: list[RawCameraRecord],
        department: Department,
        mode: Literal["validate_only", "commit"],
        actor: Principal | None = None,
    ) -> IngestReport:
        resolver, mapping_version, config = await self._resolver(department)
        geocode_from = config.get("geocode_from")
        report = IngestReport(total=len(records))

        for record in records:
            resolved = resolver.resolve(record.payload)

            # Some sources give a place name and no coordinates. Resolve it to the
            # district's representative point rather than dropping the camera, and
            # record the imprecision in metadata so it is never mistaken for a
            # surveyed position. Supplied coordinates always win.
            if geocode_from and not (
                resolved.values.get("latitude") and resolved.values.get("longitude")
            ):
                located = await self.geocoder.locate(resolved.values.get(geocode_from))
                if located is not None:
                    resolved.values["latitude"] = located.latitude
                    resolved.values["longitude"] = located.longitude
                    resolved.metadata |= {
                        "geocode_precision": located.precision,
                        "geocode_district": located.district_name,
                        "geocode_matched_on": located.matched_on,
                        "geocode_source": geocode_from,
                    }
                    resolved.warnings.append(
                        f"No coordinates supplied; placed at the representative point of "
                        f"{located.district_name} district (precision: {located.precision})."
                    )

            # Resolve controlled values against the vocabulary tables. An unknown
            # term normalises to the dimension's fallback so it stays queryable,
            # but the original text is kept in metadata: a registry that silently
            # rewrites "fisheye-360" to "other" and forgets loses the very thing
            # it exists to record.
            for dimension in VOCABULARY_FIELDS:
                raw_term = resolved.values.get(dimension)
                if raw_term in (None, ""):
                    continue
                code, term_warning = await self.vocabulary.resolve(dimension, raw_term)
                resolved.values[dimension] = code
                if term_warning:
                    resolved.metadata[f"unmapped_{dimension}"] = str(raw_term)
                    resolved.warnings.append(term_warning)

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
                validated.values,
                resolved.metadata,
                record,
                department,
                mapping_version,
                actor,
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

    async def _sync_endpoints(
        self, camera: Camera, endpoints: list[dict[str, Any]]
    ) -> None:
        """Replace rather than merge.

        The source catalogue is authoritative about how a camera can be reached, so a
        URL that disappeared upstream has to disappear here too. Merging would leave
        the registry handing Models 2-4 an endpoint that no longer exists, which is
        worse than handing them none.
        """
        await self.session.execute(
            delete(StreamEndpoint).where(StreamEndpoint.camera_id == camera.id)
        )
        for endpoint in endpoints:
            self.session.add(
                StreamEndpoint(
                    camera_id=camera.id,
                    protocol=endpoint["protocol"],
                    url=endpoint["url"],
                    codec=endpoint.get("codec"),
                    resolution=endpoint.get("resolution"),
                    is_primary=endpoint.get("is_primary", False),
                    reachability=endpoint.get("reachability", "direct_ip"),
                    requires_auth=endpoint.get("requires_auth", False),
                    credential_ref=endpoint.get("credential_ref"),
                )
            )

    async def _persist(
        self,
        values: dict[str, Any],
        metadata: dict[str, Any],
        record: RawCameraRecord,
        department: Department,
        mapping_version: int,
        actor: "Principal | None" = None,
    ) -> str:
        external_id = str(values["external_camera_id"])
        wkt = f"SRID=4326;POINT({values['longitude']} {values['latitude']})"
        status = values.get("status")

        # Absent key and empty list mean different things and must not be conflated.
        # No key at all (CSV, manual form, REST POST) means "this source has nothing
        # to say about stream endpoints", so existing rows are left alone. A present
        # but empty list is a catalogue asserting "this camera has none any more",
        # which must clear them.
        endpoints = record.payload.get("_stream_endpoints")

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
            self.audit.record(
                action="camera.created", entity_type="camera", entity_id=camera.id,
                actor=actor, after=_snapshot(camera),
            )
            outcome = "created"
        else:
            before = _snapshot(camera)
            changed = False
            if _point_moved(
                camera.location, float(values["longitude"]), float(values["latitude"])
            ):
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
                self.audit.record(
                    action="camera.updated", entity_type="camera", entity_id=camera.id,
                    actor=actor, before=before, after=_snapshot(camera),
                )
                outcome = "updated"
            else:
                # Nothing recorded on a no-op. A trail that gains thousands of
                # "nothing changed" rows every night is one nobody reads.
                outcome = "skipped"

        # Deliberately also on "skipped". A camera's core fields can be identical
        # while its stream URLs have moved to a new host; syncing only on
        # created/updated would let every re-sync silently keep serving a dead URL.
        if endpoints is not None:
            await self._sync_endpoints(camera, endpoints)
            await self.session.flush()
        return outcome
