from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(24), default="viewer")
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    def __repr__(self) -> str:
        return f"<User {self.email!r} role={self.role}>"


class ApiKey(Base, UUIDMixin, TimestampMixin):
    """A per-integration credential.

    key_prefix is indexed so verification is one indexed read plus one argon2
    check, rather than hashing the candidate against every key in the table.
    """

    __tablename__ = "api_keys"

    department_id: Mapped[UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    rate_limit_tier: Mapped[str] = mapped_column(
        String(16), default="standard", server_default="standard"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<ApiKey {self.key_prefix}… {self.name!r}>"


class AuditLog(Base, UUIDMixin):
    """Who changed what, and what it looked like before.

    Append-only. Recording the before and after states rather than a message means
    a reviewer can answer "what did this camera look like last Tuesday" without
    replaying the whole history.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id", "at"),
        Index("ix_audit_actor", "actor_type", "actor_id", "at"),
        Index("ix_audit_at", "at"),
    )

    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
