from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.routers.cameras import camera_filter
from app.core.db import get_session
from app.schemas.filters import CameraFilter
from app.services.tiles import TileService

router = APIRouter(prefix="/tiles", tags=["tiles"])


@router.get(
    "/cameras/{z}/{x}/{y}.mvt",
    summary="Mapbox Vector Tile of cameras",
    description=(
        "Clustered counts below zoom 11, individual cameras at zoom 11 and above. "
        "Returns 204 when the tile contains no cameras. Accepts the same filter "
        "query parameters as the list endpoint, so the map and the table always "
        "show the same result set."
    ),
    response_class=Response,
)
async def camera_tile(
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    filters: CameraFilter = Depends(camera_filter),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # Same dependency as `GET /cameras`, so a tile can never disagree with the table.
    tile = await TileService(session).cameras(z, x, y, filters)
    if not tile:
        return Response(status_code=204)
    return Response(
        content=tile,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=60"},
    )
