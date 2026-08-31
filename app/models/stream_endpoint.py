from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import Reachability
from app.models.base import Base, TimestampMixin, UUIDMixin


class StreamEndpoint(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "stream_endpoints"

    camera_id: Mapped[UUID] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True
    )
    protocol: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(1000))
    codec: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    reachability: Mapped[str] = mapped_column(String(16), default=Reachability.DIRECT_IP)
    requires_auth: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probe_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
