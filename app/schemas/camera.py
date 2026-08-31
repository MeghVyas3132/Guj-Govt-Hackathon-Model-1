from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    CameraStatus,
    CameraTechnology,
    CameraType,
    Connectivity,
    LifecycleState,
    OwnershipClass,
    Reachability,
    SiteType,
    SourceType,
    StreamProtocol,
)


class StreamEndpointRead(BaseModel):
    """How Models 2-4 reach this camera. Credentials are omitted unless the caller
    holds the streams:credentials scope."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    protocol: StreamProtocol
    url: str = Field(examples=["rtsp://103.250.160.189:8554/stream/cam04"])
    codec: str | None = Field(default=None, examples=["h264"])
    resolution: str | None = Field(default=None, examples=["1920x1080"])
    is_primary: bool = True
    reachability: Reachability = Field(
        description="public_cdn works on any network; direct_ip needs gateway ports open."
    )
    requires_auth: bool = False
    verified_at: datetime | None = None


class CameraBase(BaseModel):
    external_camera_id: str = Field(examples=["cam04"])
    name: str | None = Field(default=None, examples=["Nehru Bridge East Approach"])
    latitude: float = Field(ge=-90, le=90, examples=[23.0225])
    longitude: float = Field(ge=-180, le=180, examples=[72.5714])
    address: str | None = None
    camera_type: CameraType = CameraType.FIXED
    camera_technology: CameraTechnology = CameraTechnology.IP
    azimuth_deg: float | None = Field(default=None, ge=0, lt=360, examples=[135.0])
    fov_deg: float | None = Field(default=None, gt=0, le=360, examples=[90.0])
    range_m: float | None = Field(default=None, gt=0, examples=[100.0])
    height_m: float | None = Field(default=None, gt=0)
    resolution: str | None = None
    has_night_vision: bool | None = None
    connectivity: Connectivity = Connectivity.UNKNOWN
    storage_type: str | None = Field(default=None, examples=["local"])
    retention_days: int | None = Field(default=None, ge=0, examples=[15])
    ownership_class: OwnershipClass = OwnershipClass.GOVERNMENT
    site_type: SiteType = SiteType.OTHER
    amc_vendor: str | None = None
    amc_expiry_date: date | None = None
    install_date: date | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Department-specific fields with no canonical home. Never dropped.",
    )


class CameraCreate(CameraBase):
    department_id: UUID
    operator_department_id: UUID | None = None


class CameraUpdate(BaseModel):
    name: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    camera_type: CameraType | None = None
    azimuth_deg: float | None = Field(default=None, ge=0, lt=360)
    fov_deg: float | None = Field(default=None, gt=0, le=360)
    range_m: float | None = Field(default=None, gt=0)
    connectivity: Connectivity | None = None
    site_type: SiteType | None = None
    metadata: dict[str, Any] | None = None


class CameraRead(CameraBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    camera_uid: str = Field(examples=["GJ-POL-000123"])
    department_id: UUID
    department_code: str | None = Field(default=None, examples=["POL"])
    operator_department_id: UUID | None = None
    district_id: UUID | None = None
    current_status: CameraStatus = CameraStatus.UNKNOWN
    status_since: datetime | None = None
    last_seen_at: datetime | None = None
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    source_type: SourceType
    stream_endpoints: list[StreamEndpointRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
