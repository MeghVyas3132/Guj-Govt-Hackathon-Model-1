from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StreamEndpointRead(BaseModel):
    """How Models 2-4 reach this camera. Credentials are omitted unless the caller
    holds the streams:credentials scope."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    protocol: str
    url: str = Field(examples=["rtsp://103.250.160.189:8554/stream/cam04"])
    codec: str | None = Field(default=None, examples=["h264"])
    resolution: str | None = Field(default=None, examples=["1920x1080"])
    is_primary: bool = True
    reachability: str = Field(
        description="public_cdn works on any network; direct_ip needs gateway ports open."
    )
    requires_auth: bool = False
    verified_at: datetime | None = None


# Vocabulary-backed fields are plain strings at the API boundary. Typing them as
# Python enums would make FastAPI reject a value the vocabulary tables accept --
# the registry refusing a camera type it is perfectly capable of recording, purely
# because that word was not known when this file was written. VocabularyService is
# the single gate; it resolves unknown terms to the dimension's fallback and keeps
# the original in metadata.
class CameraBase(BaseModel):
    external_camera_id: str = Field(examples=["cam04"])
    name: str | None = Field(default=None, examples=["Nehru Bridge East Approach"])
    latitude: float = Field(ge=-90, le=90, examples=[23.0225])
    longitude: float = Field(ge=-180, le=180, examples=[72.5714])
    address: str | None = None
    camera_type: str = "fixed"
    camera_technology: str = "ip"
    azimuth_deg: float | None = Field(default=None, ge=0, lt=360, examples=[135.0])
    fov_deg: float | None = Field(default=None, gt=0, le=360, examples=[90.0])
    range_m: float | None = Field(default=None, gt=0, examples=[100.0])
    height_m: float | None = Field(default=None, gt=0)
    resolution: str | None = None
    has_night_vision: bool | None = None
    connectivity: str = "unknown"
    storage_type: str | None = Field(default=None, examples=["local"])
    retention_days: int | None = Field(default=None, ge=0, examples=[15])
    ownership_class: str = "government"
    site_type: str = "other"
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


class CameraBulkItem(CameraBase):
    """One record in a bulk API onboarding request.

    `department_id` is optional here, unlike CameraCreate: the bulk endpoint
    takes the department as a query parameter, and that is the value the
    permission check runs against. Requiring it per record would make an
    integrator repeat the same UUID on every row of a nightly export. Supplying
    a *different* one is rejected rather than ignored.
    """

    department_id: UUID | None = None
    operator_department_id: UUID | None = None


class CameraUpdate(BaseModel):
    name: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    camera_type: str | None = None
    azimuth_deg: float | None = Field(default=None, ge=0, lt=360)
    fov_deg: float | None = Field(default=None, gt=0, le=360)
    range_m: float | None = Field(default=None, gt=0)
    connectivity: str | None = None
    site_type: str | None = None
    metadata: dict[str, Any] | None = None


class CameraRead(CameraBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    camera_uid: str = Field(examples=["GJ-POL-000123"])
    department_id: UUID
    department_code: str | None = Field(default=None, examples=["POL"])
    operator_department_id: UUID | None = None
    district_id: UUID | None = None
    current_status: str = "unknown"
    status_since: datetime | None = None
    last_seen_at: datetime | None = None
    lifecycle_state: str = "active"
    source_type: str
    stream_endpoints: list[StreamEndpointRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class EnrichmentResult(BaseModel):
    """What derivation found for one camera.

    `metadata` is deliberately open: what a stream can tell us about itself
    differs by protocol and by gateway, and pinning it to a fixed shape here
    would mean discarding whatever a future source volunteers.
    """

    camera_id: str
    external_camera_id: str | None = None
    updated: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class EnrichmentReport(BaseModel):
    checked: int
    updated: int
    failed: int
    results: list[EnrichmentResult] = Field(default_factory=list)


class CameraBounds(BaseModel):
    """The extent of a set of cameras, for framing a map.

    All four edges are None when nothing matched. A client should read that as
    "keep the current view" rather than as an error or as a zero-area box at
    Null Island.
    """

    west: float | None = None
    south: float | None = None
    east: float | None = None
    north: float | None = None
    count: int = 0
