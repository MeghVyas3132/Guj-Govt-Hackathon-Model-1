"""Generate a statewide synthetic camera fleet for scale demonstration.

Points are generated *inside the real district polygons* with PostGIS
`ST_GeneratePoints`, not scattered around hardcoded centroids. Every camera
therefore genuinely falls within the district it is attributed to, and the
distribution follows the actual shape of Gujarat rather than a bounding box.

Every row is stamped `metadata.synthetic = true`, so synthetic cameras can always
be told apart from the real ones onboarded from a department source. Nothing here
should ever be mistaken for surveyed data.

Three properties exist for the demo rather than for realism, and are declared so
nobody reads them as findings:
  * two districts are deliberately under-served, so gap analysis has something to find
  * one district is heavily offline, so installed-vs-effective coverage diverges visibly
  * camera density is weighted towards the metros, so the map has dense cores and
    sparse edges instead of uniform noise
"""

import asyncio
import sys

from sqlalchemy import text

from app.core.db import SessionLocal

DEFAULT_TOTAL = 80_000
SEED = 2026

# Relative density multipliers. Everything not named here gets 1.0 and is sized by
# district area alone. Synthetic-data tuning, not a claim about real deployments.
URBAN_WEIGHT: dict[str, float] = {
    "Ahmadabad": 9.0, "Surat": 8.0, "Vadodara": 5.0, "Rajkot": 4.5,
    "Bhavnagar": 2.5, "Jamnagar": 2.0, "Junagadh": 1.8, "Gandhinagar": 2.2,
    "Anand": 1.6, "Mahesana": 1.4, "Bharuch": 1.4, "Navsari": 1.3, "Valsad": 1.3,
}

# Deliberately sparse: the visible coverage gaps in the demo.
GAP_DISTRICTS = {"Dohad": 0.06, "Narmada": 0.05, "The Dangs": 0.05}

# Deliberately unreliable: makes the installed-vs-effective delta dramatic where
# it is demonstrated.
OUTAGE_DISTRICT = "Bhavnagar"
OUTAGE_RATE = 0.34
BASE_OUTAGE_RATE = 0.09

DEPARTMENTS = [("POL", 0.34), ("MUN", 0.30), ("GSRTC", 0.16), ("HLTH", 0.12), ("PANCH", 0.08)]

_INSERT = text(
    """
WITH pts AS (
    SELECT (ST_Dump(ST_GeneratePoints(:geom, :n, :seed))).geom AS pt,
           row_number() OVER () AS idx
),
rolled AS (
    SELECT pt, idx,
           random()                                   AS r_dept,
           random()                                   AS r_type,
           random()                                   AS r_status,
           random()                                   AS r_tech,
           random()                                   AS r_own,
           random()                                   AS r_conn,
           random() * 360.0                           AS azimuth
    FROM pts
),
typed AS (
    SELECT rolled.*,
           CASE WHEN r_type < 0.44 THEN 'fixed'
                WHEN r_type < 0.62 THEN 'ptz'
                WHEN r_type < 0.78 THEN 'dome'
                WHEN r_type < 0.92 THEN 'bullet'
                WHEN r_type < 0.98 THEN 'anpr'
                ELSE 'thermal' END                    AS cam_type,
           CASE WHEN r_dept < 0.34 THEN 'POL'
                WHEN r_dept < 0.64 THEN 'MUN'
                WHEN r_dept < 0.80 THEN 'GSRTC'
                WHEN r_dept < 0.92 THEN 'HLTH'
                ELSE 'PANCH' END                      AS dept_code
    FROM rolled
)
INSERT INTO cameras (
    id, camera_uid, department_id, external_camera_id, name, location,
    camera_type, camera_technology, azimuth_deg, fov_deg, range_m,
    resolution, has_night_vision, connectivity, storage_type, retention_days,
    ownership_class, site_type, current_status, status_since, lifecycle_state,
    is_active, metadata, source_type, created_at, updated_at
)
SELECT
    gen_random_uuid(),
    'GJ-SYN-' || lpad(nextval('synthetic_cam_seq')::text, 7, '0'),
    d.id,
    'SYN-' || typed.dept_code || '-' || lpad(currval('synthetic_cam_seq')::text, 7, '0'),
    :district || ' '
        || (ARRAY['Junction','Chowk','Circle','Gate','Depot','Bridge','Market'])
             [1 + (typed.idx % 7)]
        || ' ' || (1 + typed.idx % 97),
    ST_SetSRID(typed.pt, 4326)::geography,
    typed.cam_type,
    CASE WHEN typed.r_tech < 0.18 THEN 'analog' ELSE 'ip' END,
    -- Directional types get a bearing; sweeping types deliberately do not.
    CASE WHEN typed.cam_type IN ('fixed','bullet','anpr') THEN typed.azimuth END,
    CASE WHEN typed.cam_type IN ('fixed','bullet','anpr')
         THEN (ARRAY[60.0,75.0,90.0,110.0])[1 + (typed.idx % 4)] END,
    CASE WHEN typed.cam_type IN ('fixed','bullet','anpr')
         THEN (ARRAY[80.0,100.0,120.0])[1 + (typed.idx % 3)]
         ELSE (ARRAY[200.0,250.0,300.0])[1 + (typed.idx % 3)] END,
    (ARRAY['1280x720','1920x1080','2560x1440','3840x2160'])[1 + (typed.idx % 4)],
    (typed.idx % 5) <> 0,
    CASE WHEN typed.r_conn < 0.52 THEN 'fiber'
         WHEN typed.r_conn < 0.78 THEN '4g'
         WHEN typed.r_conn < 0.92 THEN 'lan'
         ELSE 'wifi' END,
    CASE WHEN typed.idx % 3 = 0 THEN 'cloud' ELSE 'local' END,
    (ARRAY[7,15,15,30])[1 + (typed.idx % 4)],
    CASE WHEN typed.r_own < 0.09 THEN 'private' ELSE 'government' END,
    CASE WHEN typed.dept_code = 'GSRTC' THEN 'bus_depot'
         WHEN typed.dept_code = 'HLTH' THEN 'hospital'
         WHEN typed.dept_code = 'PANCH' THEN 'office'
         WHEN typed.idx % 3 = 0 THEN 'traffic_junction'
         ELSE 'public_space' END,
    CASE WHEN typed.r_status < :outage_rate THEN 'offline'
         WHEN typed.r_status < :outage_rate + 0.04 THEN 'maintenance'
         WHEN typed.r_status < :outage_rate + 0.06 THEN 'unknown'
         ELSE 'online' END,
    now() - (interval '1 hour' * (typed.idx % 400)),
    'active', true,
    jsonb_build_object('synthetic', true, 'seed_district', :district),
    'csv', now(), now()
FROM typed
JOIN departments d ON d.code = typed.dept_code
"""
)


async def main(total: int = DEFAULT_TOTAL) -> None:
    async with SessionLocal() as session:
        await session.execute(
            text("DELETE FROM cameras WHERE metadata->>'synthetic' = 'true'")
        )
        # Restarted each run so uids are stable across reseeds.
        await session.execute(text("DROP SEQUENCE IF EXISTS synthetic_cam_seq"))
        await session.execute(text("CREATE SEQUENCE synthetic_cam_seq START 1"))
        await session.commit()

        districts = (
            await session.execute(
                text(
                    "SELECT name, geom::geometry AS geom, ST_Area(geom) AS area "
                    "FROM admin_boundaries WHERE level='district'"
                )
            )
        ).all()
        if not districts:
            raise SystemExit("No districts loaded. Run: python -m seeds.boundaries")

        weights = {
            row.name: row.area * URBAN_WEIGHT.get(row.name, 1.0) * GAP_DISTRICTS.get(row.name, 1.0)
            for row in districts
        }
        scale = total / sum(weights.values())

        await session.execute(text("SELECT setseed(:s)"), {"s": 0.42})
        offset = 0
        for row in districts:
            n = max(1, round(weights[row.name] * scale))
            await session.execute(
                _INSERT,
                {
                    "geom": row.geom, "n": n, "seed": SEED,
                    "district": row.name,
                    "outage_rate": OUTAGE_RATE if row.name == OUTAGE_DISTRICT else BASE_OUTAGE_RATE,
                },
            )
            offset += n
            print(f"  {row.name:20} {n:6,}")
        await session.commit()
        print(f"\nSeeded {offset:,} synthetic cameras inside real district polygons")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TOTAL))
