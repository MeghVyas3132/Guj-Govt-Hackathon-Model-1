from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.services.tiles import TileService

router = APIRouter(prefix="/tiles", tags=["tiles"])


@router.get(
    "/cameras/{z}/{x}/{y}.mvt",
    summary="Mapbox Vector Tile of cameras",
    description=(
        "Clustered counts below zoom 11, individual cameras at zoom 11 and above. "
        "Returns 204 when the tile contains no cameras."
    ),
    response_class=Response,
)
async def camera_tile(
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    session: AsyncSession = Depends(get_session),
) -> Response:
    tile = await TileService(session).cameras(z, x, y)
    if not tile:
        return Response(status_code=204)
    return Response(
        content=tile,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=60"},
    )
