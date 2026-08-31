"""Seed the departments and field mappings the demo runs against.

The five sandbox departments are the ones the organisers name (FAQ #39): Health,
Police, GSRTC, Panchayat and Municipal Corporation. Each is given a *different*
field-mapping config, because the point of the mapping layer is that five sources
with five different vocabularies land in one canonical schema without code changes.
"""

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.department import Department
from app.models.field_mapping import FieldMapping

DEPARTMENTS: list[tuple[str, str, dict]] = [
    (
        "POL",
        "Gujarat Police",
        {
            "column_map": {
                "camera_id": "external_camera_id", "latitude": "latitude",
                "longitude": "longitude", "type": "camera_type",
                "status": "status", "location_name": "name",
            },
            "value_maps": {
                "status": {"ACTIVE": "online", "DOWN": "offline", "AMC": "maintenance"},
                "camera_type": {"FIXED": "fixed", "PTZ": "ptz"},
            },
            "defaults": {"connectivity": "fiber", "site_type": "traffic_junction"},
            "passthrough_to_metadata": True,
        },
    ),
    (
        "MUN",
        "Municipal Corporation",
        {
            # Different column names, numeric status, four-way junction naming.
            "column_map": {
                "cam_no": "external_camera_id", "lat_dd": "latitude",
                "long_dd": "longitude", "cam_kind": "camera_type",
                "working": "status", "place": "name",
            },
            "value_maps": {
                "status": {"1": "online", "0": "offline"},
                "camera_type": {"DOME": "dome", "BULLET": "bullet"},
            },
            "defaults": {"connectivity": "fiber", "site_type": "public_space"},
            "passthrough_to_metadata": True,
        },
    ),
    (
        "GSRTC",
        "Gujarat State Road Transport Corporation",
        {
            "column_map": {
                "AssetCode": "external_camera_id", "GPS_Lat": "latitude",
                "GPS_Long": "longitude", "DeviceType": "camera_type",
                "OperationalState": "status", "DepotName": "name",
                "InstalledOn": "install_date",
            },
            "value_maps": {
                "status": {"UP": "online", "DOWN": "offline", "MAINT": "maintenance"},
                "camera_type": {
                    "IP-BULLET": "bullet", "IP-DOME": "dome", "ANALOG-BULLET": "bullet",
                },
            },
            "defaults": {"site_type": "bus_depot", "connectivity": "lan"},
            "passthrough_to_metadata": True,
        },
    ),
    (
        "HLTH",
        "Health Department",
        {
            "column_map": {
                "facility_cam_ref": "external_camera_id", "coord_lat": "latitude",
                "coord_lon": "longitude", "camera_category": "camera_type",
                "live": "status", "facility": "name", "retention": "retention_days",
            },
            "value_maps": {
                "status": {"YES": "online", "NO": "offline"},
                "camera_type": {"FIXED": "fixed", "PTZ": "ptz"},
            },
            "defaults": {"site_type": "hospital", "connectivity": "lan"},
            "passthrough_to_metadata": True,
        },
    ),
    (
        "PANCH",
        "Panchayat Department",
        {
            # The awkward one: degrees-minutes-seconds coordinates.
            "column_map": {
                "id": "external_camera_id", "latitude_dms": "latitude",
                "longitude_dms": "longitude", "kind": "camera_type",
                "state": "status", "village": "name",
            },
            "value_maps": {
                "status": {"OK": "online", "FAULT": "offline"},
                "camera_type": {"FIXED": "fixed", "BULLET": "bullet"},
            },
            "defaults": {"connectivity": "4g", "site_type": "office"},
            "coordinate_format": "dms",
            "passthrough_to_metadata": True,
        },
    ),
    (
        "SEN",
        "Sentinel Sandbox (Gujarat Police)",
        {
            # Name-only catalogue: no coordinates, so it geocodes from the name.
            "column_map": {"id": "external_camera_id", "name": "name"},
            "defaults": {
                "ownership_class": "government", "camera_type": "fixed",
                "connectivity": "fiber", "site_type": "public_space",
            },
            "geocode_from": "name",
            "passthrough_to_metadata": True,
        },
    ),
]


async def main() -> None:
    async with SessionLocal() as session:
        for code, name, config in DEPARTMENTS:
            dept = (
                await session.execute(select(Department).where(Department.code == code))
            ).scalar_one_or_none()
            if dept is None:
                dept = Department(code=code, name=name)
                session.add(dept)
                await session.flush()

            existing = (
                await session.execute(
                    select(FieldMapping)
                    .where(FieldMapping.department_id == dept.id)
                    .order_by(FieldMapping.version.desc())
                )
            ).scalars().first()
            if existing is None:
                session.add(
                    FieldMapping(department_id=dept.id, version=1, config=config)
                )
            else:
                existing.config = config
        await session.commit()
    print(f"Seeded {len(DEPARTMENTS)} departments with distinct field mappings")


if __name__ == "__main__":
    asyncio.run(main())
