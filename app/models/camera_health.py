from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class CameraHealth(Base, UUIDMixin):
    """Append-only observation log. Never updated, never deleted.

    Monthly range partitioning on observed_at is the growth path at 80k cameras;
    documented in the spec, not implemented here.
    """

    __tablename__ = "camera_health"
    __table_args__ = (
        # (camera_id, observed_at) ascending rather than descending: PostgreSQL scans a
        # b-tree backwards at no cost, so this serves "latest first" history reads, and
        # ascending order also serves the sparkline query which reads a window forwards.
        Index("ix_camera_health_camera_observed", "camera_id", "observed_at"),
    )

    camera_id: Mapped[UUID] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source: Mapped[str] = mapped_column(String(16), default="probe")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
