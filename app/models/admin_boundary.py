from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AdminBoundary(Base, UUIDMixin, TimestampMixin):
    """A district, taluka or ward polygon.

    Self-referential via `parent_id` so a taluka can point at its district and a
    ward at its taluka -- one table for the whole administrative hierarchy rather
    than three near-identical ones.
    """

    __tablename__ = "admin_boundaries"
    __table_args__ = (Index("ix_admin_boundaries_geom", "geom", postgresql_using="gist"),)

    level: Mapped[str] = mapped_column(String(16))  # district | taluka | ward
    name: Mapped[str] = mapped_column(String(200), index=True)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_boundaries.id"), nullable=True
    )
    population: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # spatial_index=False: the GIST index is declared explicitly in __table_args__,
    # so GeoAlchemy2 must not add a second one of its own.
    geom: Mapped[Any] = mapped_column(
        Geography(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False)
    )
