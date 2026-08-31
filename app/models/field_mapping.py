from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class FieldMapping(Base, UUIDMixin, TimestampMixin):
    """Per-department translation config. Onboarding a new department is a row here,
    not a code change. Versioned so an import is reproducible."""

    __tablename__ = "field_mappings"
    __table_args__ = (UniqueConstraint("department_id", "version"),)

    department_id: Mapped[UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
