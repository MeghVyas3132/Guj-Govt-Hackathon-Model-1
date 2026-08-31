from datetime import date, datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    CameraStatus,
    CameraTechnology,
    CameraType,
    Connectivity,
    LifecycleState,
    OwnershipClass,
    SiteType,
    SourceType,
)
from app.models.base import Base, TimestampMixin, UUIDMixin


class Camera(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "cameras"
    __table_args__ = (
        UniqueConstraint("department_id", "external_camera_id", name="uq_camera_dept_external"),
        Index("ix_cameras_location", "location", postgresql_using="gist"),
        Index("ix_cameras_status_since", "current_status", "status_since"),
        Index("ix_cameras_metadata", "metadata", postgresql_using="gin"),
    )

    camera_uid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    department_id: Mapped[UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    operator_department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    external_camera_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # spatial_index=False: the GIST index is declared explicitly in __table_args__,
    # so GeoAlchemy2 must not add a second one of its own.
    location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    district_id: Mapped[UUID | None] = mapped_column(nullable=True)

    camera_type: Mapped[str] = mapped_column(String(32), default=CameraType.FIXED)
    camera_technology: Mapped[str] = mapped_column(String(16), default=CameraTechnology.IP)
    azimuth_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    fov_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    range_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    has_night_vision: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    connectivity: Mapped[str] = mapped_column(String(16), default=Connectivity.UNKNOWN)
    storage_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ownership_class: Mapped[str] = mapped_column(String(16), default=OwnershipClass.GOVERNMENT)
    site_type: Mapped[str] = mapped_column(String(32), default=SiteType.OTHER)
    amc_vendor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amc_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    install_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    current_status: Mapped[str] = mapped_column(String(16), default=CameraStatus.UNKNOWN)
    status_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(24), default=LifecycleState.ACTIVE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # `metadata` is reserved by SQLAlchemy's Declarative API, hence the trailing underscore.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    source_type: Mapped[str] = mapped_column(String(16), default=SourceType.MANUAL)
    field_mapping_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
