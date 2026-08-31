from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Below this zoom, individual markers are meaningless and slow -- serve grid-aggregated
# counts instead. ST_SnapToGrid quantises in Web Mercator metres.
CLUSTER_ZOOM_THRESHOLD = 11

_POINT_SQL = text(
    """
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
          AND c.location && bounds.geog
    ) AS tile
    """
)

_CLUSTER_SQL = text(
    """
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
          AND c.location && bounds.geog
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
)


class TileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cameras(self, z: int, x: int, y: int) -> bytes:
        if z >= CLUSTER_ZOOM_THRESHOLD:
            result = await self.session.execute(_POINT_SQL, {"z": z, "x": x, "y": y})
        else:
            # Roughly one cluster per 40 screen pixels at this zoom.
            cell = 40 * (2 * 20037508.34) / (256 * 2**z)
            result = await self.session.execute(
                _CLUSTER_SQL, {"z": z, "x": x, "y": y, "cell": cell}
            )
        # ST_AsMVT is an aggregate: over zero rows it returns NULL, not empty bytes.
        # The router turns the empty result into a 204.
        return bytes(result.scalar_one() or b"")
