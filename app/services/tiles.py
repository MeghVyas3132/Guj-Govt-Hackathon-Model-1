from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.filters import CameraFilter

# Below this zoom, individual markers are meaningless and slow -- serve grid-aggregated
# counts instead. ST_SnapToGrid quantises in Web Mercator metres.
CLUSTER_ZOOM_THRESHOLD = 11


def _predicates(filters: CameraFilter) -> tuple[str, dict[str, Any]]:
    """Build the tile query's extra WHERE clauses from the shared filter.

    The two halves are kept strictly apart: the *clause text* is a fixed literal
    chosen by a branch on the filter, and every *value* leaves via a bound parameter.
    Nothing derived from the request is ever formatted into SQL -- not the values, not
    the column names, not the operators. The values are additionally constrained to
    enum members and UUIDs by `camera_filter` before they arrive here.

    Every field `camera_filter` can produce is handled here, and each clause mirrors
    its counterpart in `CameraRepository._apply` exactly -- `lower(col) LIKE lower(q)`
    rather than the merely-similar `ILIKE`, the same four searched columns, the same
    boundary subquery. A field reaching the tile endpoint and being dropped here is
    not a smaller result set, it is a map showing markers the table has filtered out.

    The radius fields are the one exception, and they are unreachable rather than
    ignored: `camera_filter` does not expose them, so only /cameras/nearby ever sets
    a radius and no tile request can carry one.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.department_ids:
        clauses.append("c.department_id = ANY(:department_ids)")
        params["department_ids"] = [str(d) for d in filters.department_ids]
    if filters.camera_types:
        clauses.append("c.camera_type = ANY(:camera_types)")
        params["camera_types"] = [t.value for t in filters.camera_types]
    if filters.statuses:
        clauses.append("c.current_status = ANY(:statuses)")
        params["statuses"] = [s.value for s in filters.statuses]
    if filters.ownership_classes:
        clauses.append("c.ownership_class = ANY(:ownership_classes)")
        params["ownership_classes"] = [o.value for o in filters.ownership_classes]
    if filters.q:
        clauses.append(
            "(lower(c.camera_uid) LIKE :q"
            " OR lower(c.name) LIKE :q"
            " OR lower(c.address) LIKE :q"
            " OR lower(c.external_camera_id) LIKE :q)"
        )
        params["q"] = f"%{filters.q.lower()}%"
    if filters.district_id:
        clauses.append(
            "ST_Intersects("
            "c.location, (SELECT b.geom FROM admin_boundaries b WHERE b.id = :district_id)"
            ")"
        )
        params["district_id"] = str(filters.district_id)
    return ("".join(f"\n          AND {clause}" for clause in clauses), params)


_POINT_TEMPLATE = """
    WITH bounds AS (
        SELECT ST_TileEnvelope(:z, :x, :y) AS merc,
               ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)::geography AS geog
    )
    SELECT ST_AsMVT(tile, 'cameras', 4096, 'geom') FROM (
        SELECT
            ST_AsMVTGeom(
                ST_Transform(c.location::geometry, 3857), bounds.merc, 4096, 64, true
            ) AS geom,
            c.id::text          AS id,
            c.camera_uid        AS camera_uid,
            c.current_status    AS status,
            c.camera_type       AS camera_type,
            c.ownership_class   AS ownership_class,
            c.department_id::text AS department_id
        FROM cameras c, bounds
        WHERE c.is_active
          AND c.lifecycle_state = 'active'
          AND c.location && bounds.geog{predicates}
    ) AS tile
    """

_CLUSTER_TEMPLATE = """
    WITH bounds AS (
        SELECT ST_TileEnvelope(:z, :x, :y) AS merc,
               ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)::geography AS geog
    ),
    clustered AS (
        SELECT
            ST_SnapToGrid(ST_Transform(c.location::geometry, 3857), :cell, :cell) AS cell,
            count(*) AS camera_count,
            count(*) FILTER (WHERE c.current_status = 'offline') AS offline_count
        FROM cameras c, bounds
        WHERE c.is_active
          AND c.lifecycle_state = 'active'
          AND c.location && bounds.geog{predicates}
        GROUP BY 1
    )
    SELECT ST_AsMVT(tile, 'camera_clusters', 4096, 'geom') FROM (
        SELECT
            ST_AsMVTGeom(clustered.cell, bounds.merc, 4096, 64, true) AS geom,
            clustered.camera_count,
            clustered.offline_count
        FROM clustered, bounds
    ) AS tile
    """


class TileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cameras(self, z: int, x: int, y: int, filters: CameraFilter) -> bytes:
        predicates, params = _predicates(filters)
        params |= {"z": z, "x": x, "y": y}
        if z >= CLUSTER_ZOOM_THRESHOLD:
            sql = _POINT_TEMPLATE.format(predicates=predicates)
        else:
            # Roughly one cluster per 40 screen pixels at this zoom.
            params["cell"] = 40 * (2 * 20037508.34) / (256 * 2**z)
            sql = _CLUSTER_TEMPLATE.format(predicates=predicates)
        result = await self.session.execute(text(sql), params)
        # ST_AsMVT is an aggregate: over zero rows it returns NULL, not empty bytes.
        # The router turns the empty result into a 204.
        return bytes(result.scalar_one() or b"")
