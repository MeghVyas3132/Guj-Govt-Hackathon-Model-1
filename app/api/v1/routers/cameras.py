from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.enums import (
    CameraStatus,
    CameraType,
    Reachability,
    SourceType,
    StreamProtocol,
)
from app.schemas.camera import CameraRead, StreamEndpointRead
from app.schemas.common import Page

router = APIRouter(prefix="/cameras", tags=["cameras"])

_STUB_ID = UUID("00000000-0000-0000-0000-000000000001")
_STUB_DEPT = UUID("00000000-0000-0000-0000-0000000000aa")
_NOW = datetime(2026, 9, 1, tzinfo=UTC)

_STUB_STREAMS = [
    StreamEndpointRead(
        id=UUID("00000000-0000-0000-0000-0000000000b1"),
        protocol=StreamProtocol.HLS,
        url="https://cctv.corp8.cloud/cam04/index.m3u8",
        codec="h264",
        resolution="1920x1080",
        is_primary=True,
        reachability=Reachability.PUBLIC_CDN,
        requires_auth=True,
    ),
    StreamEndpointRead(
        id=UUID("00000000-0000-0000-0000-0000000000b2"),
        protocol=StreamProtocol.RTSP,
        url="rtsp://103.250.160.189:8554/stream/cam04",
        codec="h264",
        resolution="1920x1080",
        is_primary=False,
        reachability=Reachability.DIRECT_IP,
        requires_auth=False,
    ),
    StreamEndpointRead(
        id=UUID("00000000-0000-0000-0000-0000000000b3"),
        protocol=StreamProtocol.WHEP,
        url="http://103.250.160.189:8889/stream/cam04/whep",
        codec="h264",
        resolution="1920x1080",
        is_primary=False,
        reachability=Reachability.DIRECT_IP,
        requires_auth=False,
    ),
]

_STUB_CAMERA = CameraRead(
    id=_STUB_ID,
    camera_uid="GJ-POL-000001",
    department_id=_STUB_DEPT,
    department_code="POL",
    external_camera_id="cam04",
    name="Nehru Bridge East Approach",
    latitude=23.0225,
    longitude=72.5714,
    camera_type=CameraType.FIXED,
    azimuth_deg=135.0,
    fov_deg=90.0,
    range_m=100.0,
    current_status=CameraStatus.ONLINE,
    status_since=_NOW,
    source_type=SourceType.ADAPTER,
    stream_endpoints=_STUB_STREAMS,
    created_at=_NOW,
    updated_at=_NOW,
)


@router.get("", response_model=Page[CameraRead], summary="List and filter cameras")
async def list_cameras(
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
) -> Page[CameraRead]:
    return Page(items=[_STUB_CAMERA], total=1, limit=limit, offset=offset)


@router.get("/{camera_id}", response_model=CameraRead)
async def get_camera(camera_id: UUID) -> CameraRead:
    return _STUB_CAMERA


@router.get(
    "/{camera_id}/streams",
    response_model=list[StreamEndpointRead],
    summary="Stream endpoints for this camera",
    description=(
        "Entry point for Models 2-4. Prefer the endpoint whose reachability matches "
        "your network: public_cdn works anywhere, direct_ip needs gateway ports open."
    ),
)
async def get_camera_streams(camera_id: UUID) -> list[StreamEndpointRead]:
    return _STUB_STREAMS
