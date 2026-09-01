"""Controlled vocabularies as data, not as Python enums.

Gujarat runs ~26 departments on unknown vendors. We do not know what camera types
exist in production -- fisheye, panoramic, body-worn, drone, ANPR variants nobody
has named yet -- nor what words each department uses for status or connectivity.

Baking those into an enum means an unknown value is flattened to `other` and the
real value is lost, and adding a term needs a deploy. Here a term is a row.

The `cameras` columns are plain VARCHAR, so a new term needs no migration either.
Enums remain in the codebase as the shipped defaults and as type hints for
internal code, but they are no longer the gate.
"""

from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin

# The dimensions a term can belong to. Adding a dimension is a code change because
# it implies a new column; adding a *term* is not, which is the point.
DIMENSIONS = (
    "camera_type",
    "status",
    "connectivity",
    "site_type",
    "ownership_class",
    "camera_technology",
    "storage_type",
    "stream_protocol",
    "reachability",
)


class VocabularyTerm(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "vocabulary_terms"
    __table_args__ = (
        UniqueConstraint("dimension", "code", name="uq_vocab_dimension_code"),
        Index("ix_vocab_dimension_active", "dimension", "is_active"),
    )

    dimension: Mapped[str] = mapped_column(String(64), index=True)
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Exactly one term per dimension is the fallback an unmapped value normalises to.
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")

    # camera_type terms carry their own coverage geometry defaults, so the gap
    # analysis stops hardcoding "PTZ means 250 m" in SQL.
    coverage_range_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_fov_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_omnidirectional: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Anything a department needs that this table does not model yet.
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )

    def __repr__(self) -> str:
        return f"<VocabularyTerm {self.dimension}:{self.code}>"
