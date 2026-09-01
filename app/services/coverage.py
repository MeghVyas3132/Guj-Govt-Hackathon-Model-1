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
# Coverage is computed with a spatial join rather than one global union.
#
# The obvious formulation -- ST_Union every footprint into a single geometry, then
# intersect each cell against it -- is quadratic in disguise: the union of a few
# thousand footprints has hundreds of thousands of vertices, and every one of the
# tens of thousands of cells is then clipped against all of them. On Bhavnagar at
# 80k cameras that had not finished after 142 seconds.
#
# Instead each cell unions only the handful of footprints that actually touch it,
# found through a GIST index. Temp tables rather than CTEs because PostgreSQL
# cannot index a CTE, and without an index the join degrades to a nested loop.

_CAMS_TABLE = text(
    """
    CREATE TEMP TABLE _cov_cams ON COMMIT DROP AS
    SELECT c.id,
           c.current_status,
           c.camera_type,
           c.azimuth_deg,
           c.fov_deg,
           (c.metadata->>'geocode_precision') AS geocode_precision,
           camera_footprint(
               c.location, c.camera_type, c.azimuth_deg, c.fov_deg, c.range_m
           )::geometry AS shape
    FROM cameras c
    WHERE c.is_active
      AND c.lifecycle_state = 'active'
      AND ST_DWithin(
          c.location,
          (SELECT geom FROM admin_boundaries WHERE id = :boundary_id),
          2000
      )
    """
)

_CAMS_INDEX = text("CREATE INDEX ON _cov_cams USING GIST (shape)")
_CAMS_ANALYZE = text("ANALYZE _cov_cams")

_CELLS_TABLE = text(
    """
    CREATE TEMP TABLE _cov_cells ON COMMIT DROP AS
    WITH aoi AS MATERIALIZED (
        SELECT geom::geometry AS geom FROM admin_boundaries WHERE id = :boundary_id
    ),
    cells AS MATERIALIZED (
        SELECT (ST_HexagonGrid(:edge_deg, aoi.geom)).geom AS cell FROM aoi
    )
    -- Most cells fall wholly inside the district and need no clipping. Testing that
    -- first matters because ST_ContainsProperly reuses PostGIS's prepared-geometry
    -- cache for the repeated AOI argument while ST_Intersection cannot.
    SELECT CASE
               WHEN ST_ContainsProperly(aoi.geom, cells.cell) THEN ST_Multi(cells.cell)
               ELSE ST_Multi(ST_CollectionExtract(ST_Intersection(cells.cell, aoi.geom), 3))
           END AS cell
    FROM cells, aoi
    WHERE ST_Intersects(cells.cell, aoi.geom)
    """
)

_CELLS_INDEX = text("CREATE INDEX ON _cov_cells USING GIST (cell)")
_CELLS_ANALYZE = text("ANALYZE _cov_cells")

_COMPUTE = text(
    """
    INSERT INTO coverage_cells
        (id, run_id, geom, installed_fraction, effective_fraction, classification,
         camera_count)
    SELECT
        gen_random_uuid(),
        :run_id,
        agg.cell::geography,
        agg.installed,
        agg.effective,
        CASE
            WHEN agg.installed >= :covered THEN 'covered'
            WHEN agg.installed >= :gap     THEN 'partial'
            ELSE 'gap'
        END,
        agg.camera_count
    FROM (
        SELECT
            cl.cell,
            COALESCE(
                ST_Area(ST_Intersection(cl.cell, ST_Union(cm.shape)))
                / NULLIF(ST_Area(cl.cell), 0), 0
            ) AS installed,
            COALESCE(
                ST_Area(
                    ST_Intersection(
                        cl.cell,
                        ST_Union(cm.shape) FILTER (WHERE cm.current_status = 'online')
                    )
                ) / NULLIF(ST_Area(cl.cell), 0), 0
            ) AS effective,
            count(cm.id) AS camera_count
        FROM _cov_cells cl
        LEFT JOIN _cov_cams cm ON ST_Intersects(cm.shape, cl.cell)
        -- A cell that only grazes the boundary clips to nothing. Storing it would add
        -- a zero-area row dragging down the district average for no reason.
        WHERE NOT ST_IsEmpty(cl.cell)
        GROUP BY cl.cell
    ) agg
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
    SELECT
        count(*)                                            AS camera_count,
        count(*) FILTER (WHERE current_status = 'online')   AS online_count,
        count(*) FILTER (
            WHERE geocode_precision = 'district'
        )                                                   AS district_located,
        -- A non-PTZ camera with no recorded bearing is treated as omnidirectional
        -- by camera_footprint, which OVERSTATES its contribution. Counted so the
        -- report can say so rather than quietly inflating the number.
        count(*) FILTER (
            WHERE camera_type NOT IN ('ptz', 'dome')
              AND (azimuth_deg IS NULL OR fov_deg IS NULL OR fov_deg >= 360)
        )                                                   AS assumed_omni
    FROM _cov_cams
    """
)

# ST_HexagonGrid takes an edge length in the units of the input geometry, and boundaries
# are stored in degrees, so metres must be converted before they are handed over. Getting
# this wrong is not a subtle error: pass metres straight through and PostGIS is asked for
# hexagons 100 degrees across; divide by a thousand too little and a district becomes
# hundreds of millions of cells and the run never returns.
METRES_PER_DEGREE = 111_320.0


class CoverageTooLargeError(ValueError):
    """Raised when a run would produce more cells than the budget allows."""


# A hexagon of edge e covers 3*sqrt(3)/2 * e^2. Used to estimate the cell count
# before committing to a run that could take minutes.
_HEX_AREA_FACTOR = 2.598076211353316

# Beyond this a run stops being interactive. Kachchh at the 25m floor is roughly
# 26 million cells; a request that appears to hang is worse than one that declines
# and says which edge length would work.
DEFAULT_MAX_CELLS = 250_000


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

    async def estimate_cells(self, boundary_id, hex_edge_m: float) -> int:
        """Approximate cell count for an AOI, without tessellating it."""
        area_m2 = (
            await self.session.execute(
                text("SELECT ST_Area(geom) FROM admin_boundaries WHERE id = :bid"),
                {"bid": boundary_id},
            )
        ).scalar_one_or_none()
        if not area_m2:
            return 0
        return int(area_m2 / (_HEX_AREA_FACTOR * hex_edge_m**2))

    async def run(
        self,
        request: CoverageRunRequest,
        max_cells: int = DEFAULT_MAX_CELLS,
    ) -> CoverageRun:
        boundary = await self.session.get(AdminBoundary, request.boundary_id)
        if boundary is None:
            raise ValueError(f"Boundary {request.boundary_id} not found")

        estimated = await self.estimate_cells(boundary.id, request.hex_edge_m)
        if estimated > max_cells:
            # Suggest the edge that lands just inside the budget, so the caller has
            # a working next step rather than a refusal.
            suggested = int(
                (
                    (estimated * _HEX_AREA_FACTOR * request.hex_edge_m**2)
                    / (max_cells * _HEX_AREA_FACTOR)
                )
                ** 0.5
            )
            raise CoverageTooLargeError(
                f"{boundary.name} at hex_edge_m={request.hex_edge_m:.0f} would produce "
                f"about {estimated:,} cells, over the {max_cells:,} budget. "
                f"Retry with hex_edge_m of at least {suggested + 1}."
            )

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

            await self.session.execute(_CAMS_TABLE, {"boundary_id": boundary.id})
            await self.session.execute(_CAMS_INDEX)
            await self.session.execute(_CAMS_ANALYZE)
            await self.session.execute(
                _CELLS_TABLE,
                {"boundary_id": boundary.id, "edge_deg": edge_deg},
            )
            await self.session.execute(_CELLS_INDEX)
            await self.session.execute(_CELLS_ANALYZE)
            await self.session.execute(
                _COMPUTE,
                {
                    "run_id": run.id,
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
            run.district_located_camera_count = stats.district_located
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

    async def classification_counts(self, run_id) -> dict[str, int]:
        """How many cells fell into each band, for the report's headline table."""
        rows = (
            await self.session.execute(
                text(
                    "SELECT classification, count(*) FROM coverage_cells "
                    "WHERE run_id = :run_id GROUP BY 1"
                ),
                {"run_id": run_id},
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    async def outage_cells(self, run_id, limit: int = 15) -> list[dict]:
        """Cells where coverage exists on paper but nothing is watching.

        Ranking by the installed-minus-effective gap answers the only question in
        this report that can be acted on this week: which places a repair crew
        should visit first. A list of cells with no coverage at all is a purchase
        decision, and there are usually thousands of them.
        """
        rows = (
            await self.session.execute(
                text(
                    "SELECT installed_fraction, effective_fraction, camera_count, "
                    "       installed_fraction - effective_fraction AS lost "
                    "FROM coverage_cells "
                    "WHERE run_id = :run_id AND installed_fraction > effective_fraction "
                    "ORDER BY lost DESC LIMIT :limit"
                ),
                {"run_id": run_id, "limit": limit},
            )
        ).all()
        return [
            {
                "installed_fraction": float(r[0]),
                "effective_fraction": float(r[1]),
                "camera_count": int(r[2]),
                "lost": float(r[3]),
            }
            for r in rows
        ]

    async def zero_coverage_cells(self, run_id) -> int:
        return (
            await self.session.execute(
                text(
                    "SELECT count(*) FROM coverage_cells "
                    "WHERE run_id = :run_id AND installed_fraction = 0"
                ),
                {"run_id": run_id},
            )
        ).scalar_one()

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
