"""Hexagonal coverage estimation, computed twice per run.

The whole point of running it twice -- once over every camera and once over only the
cameras currently online -- is the delta between the two. That number says how much of a
district went dark because equipment is broken rather than absent, which is a repair
budget rather than a procurement budget. It exists only because registry and health data
sit in one database.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_boundary import AdminBoundary
from app.models.coverage import CoverageCell, CoverageRun
from app.schemas.coverage import CoverageRunRequest

# One statement does the whole computation: tessellate the AOI, build each camera's
# footprint, union the footprints once for all cameras and once for online cameras only,
# then measure each hexagon against both. Doing this in SQL avoids shipping thousands of
# polygons into Python.
#
# ST_CollectionExtract(..., 3) around the clip is not decoration. Clipping a hexagon to a
# real district boundary yields a POLYGON most of the time, a MULTIPOLYGON where the
# boundary has islands or a ragged coast, and a GEOMETRYCOLLECTION or a bare LINESTRING
# where a cell only grazes the edge. Normalising to MULTIPOLYGON and dropping the empties
# is what lets the geometry column keep a typmod at all.
#
# Every CTE below says MATERIALIZED, and on a real district that word is the difference
# between a run that finishes and one that does not. PostgreSQL inlines a CTE referenced
# once, which pulls `clipped`'s ST_Intersection up into the outer query -- where
# `clipped.cell` appears seven times, so the clip is recomputed seven times per cell.
# Against Ahmadabad that turned a 2-second step into minutes.
_COMPUTE = text(
    """
    WITH aoi AS MATERIALIZED (
        SELECT geom::geometry AS geom FROM admin_boundaries WHERE id = :boundary_id
    ),
    cells AS MATERIALIZED (
        SELECT (ST_HexagonGrid(:edge_deg, aoi.geom)).geom AS cell
        FROM aoi
    ),
    -- Most cells fall wholly inside the district and need no clipping at all. Testing
    -- that first matters because ST_ContainsProperly reuses PostGIS's prepared-geometry
    -- cache for the repeated AOI argument while ST_Intersection cannot: on Ahmadabad's
    -- 9,495-vertex boundary, clipping every cell costs 24s and clipping only the fringe
    -- costs 2s for an identical result.
    clipped AS MATERIALIZED (
        SELECT CASE
                   WHEN ST_ContainsProperly(aoi.geom, cells.cell)
                   THEN ST_Multi(cells.cell)
                   ELSE ST_Multi(
                       ST_CollectionExtract(ST_Intersection(cells.cell, aoi.geom), 3)
                   )
               END AS cell
        FROM cells, aoi
        WHERE ST_Intersects(cells.cell, aoi.geom)
    ),
    cams AS MATERIALIZED (
        SELECT c.id,
               c.current_status,
               camera_footprint(
                   c.location, c.camera_type, c.azimuth_deg, c.fov_deg, c.range_m
               )::geometry AS shape
        FROM cameras c, aoi
        WHERE c.is_active
          AND c.lifecycle_state = 'active'
          AND ST_DWithin(c.location, aoi.geom::geography, 2000)
    ),
    installed AS MATERIALIZED (SELECT ST_Union(shape) AS shape FROM cams),
    effective AS MATERIALIZED (
        SELECT ST_Union(shape) AS shape FROM cams WHERE current_status = 'online'
    )
    INSERT INTO coverage_cells
        (id, run_id, geom, installed_fraction, effective_fraction, classification, camera_count)
    SELECT
        gen_random_uuid(),
        :run_id,
        clipped.cell::geography,
        inst.fraction,
        eff.fraction,
        CASE
            WHEN inst.fraction >= :covered THEN 'covered'
            WHEN inst.fraction >= :gap     THEN 'partial'
            ELSE 'gap'
        END,
        (SELECT count(*) FROM cams WHERE ST_Intersects(cams.shape, clipped.cell))
    FROM clipped
    CROSS JOIN LATERAL (
        SELECT COALESCE(
            ST_Area(ST_Intersection(clipped.cell, installed.shape)) /
            NULLIF(ST_Area(clipped.cell), 0), 0
        ) AS fraction
        FROM installed
    ) inst
    CROSS JOIN LATERAL (
        SELECT COALESCE(
            ST_Area(ST_Intersection(clipped.cell, effective.shape)) /
            NULLIF(ST_Area(clipped.cell), 0), 0
        ) AS fraction
        FROM effective
    ) eff
    -- A cell that only touches the boundary clips to nothing. Storing it would add a
    -- zero-area row that counts against the district average for no reason.
    WHERE NOT ST_IsEmpty(clipped.cell)
    """
)

_SUMMARISE = text(
    """
    SELECT
        count(*)                                         AS total_cells,
        COALESCE(avg(installed_fraction), 0) * 100       AS installed_pct,
        COALESCE(avg(effective_fraction), 0) * 100       AS effective_pct
    FROM coverage_cells
    WHERE run_id = :run_id
    """
)

# The buffer, the activity filters and the omnidirectional predicate are repeated from
# _COMPUTE rather than shared, because these counts must describe exactly the camera set
# the geometry was built from. A stat that quietly counts a different population than the
# map is worse than no stat.
_CAMERA_STATS = text(
    """
    WITH aoi AS (
        SELECT geom::geometry AS geom FROM admin_boundaries WHERE id = :boundary_id
    )
    SELECT
        count(*)                                                       AS camera_count,
        count(*) FILTER (WHERE c.current_status = 'online')            AS online_count,
        count(*) FILTER (
            WHERE c.camera_type NOT IN ('ptz', 'dome')
              AND (
                  c.azimuth_deg IS NULL
                  OR c.fov_deg IS NULL
                  OR c.fov_deg >= 360
              )
        )                                                              AS assumed_omni
    FROM cameras c, aoi
    WHERE c.is_active
      AND c.lifecycle_state = 'active'
      AND ST_DWithin(c.location, aoi.geom::geography, 2000)
    """
)

# ST_HexagonGrid takes an edge length in the units of the input geometry, and boundaries
# are stored in degrees, so metres must be converted before they are handed over. Getting
# this wrong is not a subtle error: pass metres straight through and PostGIS is asked for
# hexagons 100 degrees across; divide by a thousand too little and a district becomes
# hundreds of millions of cells and the run never returns.
METRES_PER_DEGREE = 111_320.0


class CoverageService:
    """Grid-based coverage estimation.

    Stated limitations, repeated in every generated report: 2D only, no terrain or
    building occlusion, nominal range rather than optics-derived, and the recorded
    bearing is assumed accurate.

    One more, not visible in the geometry: cameras geocoded from a name alone carry
    ``metadata.geocode_precision == "district"`` and sit on their district's
    representative point, not where the camera physically stands. They are counted here
    deliberately -- the camera is real and its coverage is real -- but the blob appears
    in the wrong place, so a run over a district with many such rows shows coverage
    clustered at the centroid. The report must disclose that alongside the other caveats.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run(self, request: CoverageRunRequest) -> CoverageRun:
        boundary = await self.session.get(AdminBoundary, request.boundary_id)
        if boundary is None:
            raise ValueError(f"Boundary {request.boundary_id} not found")

        run = CoverageRun(
            boundary_id=boundary.id,
            boundary_name=boundary.name,
            hex_edge_m=request.hex_edge_m,
            covered_threshold=request.covered_threshold,
            gap_threshold=request.gap_threshold,
            status="running",
            parameters=request.model_dump(mode="json"),
        )
        self.session.add(run)
        await self.session.flush()

        try:
            # Converting via degrees of latitude distorts cell area slightly with
            # longitude; across Gujarat's 4.8-degree span the error is under 2% and is
            # documented rather than corrected. Projecting the AOI to EPSG:32643 first
            # would remove it entirely and is the right fix given time.
            edge_deg = request.hex_edge_m / METRES_PER_DEGREE

            await self.session.execute(
                _COMPUTE,
                {
                    "boundary_id": boundary.id,
                    "run_id": run.id,
                    "edge_deg": edge_deg,
                    "covered": request.covered_threshold,
                    "gap": request.gap_threshold,
                },
            )

            summary = (await self.session.execute(_SUMMARISE, {"run_id": run.id})).one()
            stats = (
                await self.session.execute(_CAMERA_STATS, {"boundary_id": boundary.id})
            ).one()

            run.total_cells = summary.total_cells
            run.installed_coverage_pct = round(summary.installed_pct, 2)
            run.effective_coverage_pct = round(summary.effective_pct, 2)
            run.camera_count = stats.camera_count
            run.online_camera_count = stats.online_count
            run.assumed_omnidirectional_count = stats.assumed_omni
            run.status = "done"
        except Exception as exc:
            # A failed statement poisons the transaction, so the half-written run has to
            # be rolled back before the failure can be recorded at all. Rollback returns
            # the pending run object to transient, so re-adding it writes the row fresh --
            # this time with nothing but the failure on it.
            await self.session.rollback()
            run.status = "failed"
            run.error = str(exc)[:1000]
            run.finished_at = datetime.now(UTC)
            self.session.add(run)
            await self.session.commit()
            raise

        run.finished_at = datetime.now(UTC)
        await self.session.commit()
        return run

    async def worst_cells(self, run_id: UUID, limit: int = 20) -> list[dict]:
        """The emptiest gap cells of a run, for the report's ranked table."""
        stmt = (
            select(CoverageCell)
            .where(CoverageCell.run_id == run_id, CoverageCell.classification == "gap")
            .order_by(CoverageCell.installed_fraction.asc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [
            {
                "installed_fraction": round(r.installed_fraction, 3),
                "effective_fraction": round(r.effective_fraction, 3),
                "camera_count": r.camera_count,
            }
            for r in rows
        ]
