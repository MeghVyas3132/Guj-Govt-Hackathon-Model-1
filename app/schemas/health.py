from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import CameraStatus


class HealthObservationIn(BaseModel):
    """One observation pushed by a department system or produced by our prober."""

    external_camera_id: str | None = Field(
        default=None, description="Use this when pushing by the department's own id."
    )
    camera_id: UUID | None = None
    status: CameraStatus
    observed_at: datetime | None = None
    latency_ms: int | None = None
    detail: dict = Field(default_factory=dict)


class HealthObservationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    camera_id: UUID
    status: CameraStatus
    observed_at: datetime
    source: str
    latency_ms: int | None = None


class OfflineCamera(BaseModel):
    camera_id: UUID
    camera_uid: str
    name: str | None
    department_code: str | None
    latitude: float
    longitude: float
    status_since: datetime | None
    downtime_seconds: float


class HealthSummary(BaseModel):
    total: int
    online: int
    offline: int
    unknown: int
    maintenance: int
    offline_over_24h: int
    offline_over_7d: int
