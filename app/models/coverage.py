"""Persisted results of a coverage run.

A run is stored rather than recomputed on demand so that the map, the API summary and
the printed report all read the same numbers. Recomputing per request would let the
report disagree with the map it sits next to, because camera health moves underneath.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class CoverageRun(Base, UUIDMixin, TimestampMixin):
    """One gap analysis over one area of interest.

    Both coverage figures are recorded on the same row on purpose: the delta between
    them is coverage lost to outages, and it is only meaningful when the two halves
    were measured against the same cells in the same instant.
    """

    __tablename__ = "coverage_runs"

    boundary_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_boundaries.id"), nullable=True
    )
    # Denormalised so a finished run still prints a title after its boundary is
    # renamed or a boundary refresh replaces the row.
    boundary_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    hex_edge_m: Mapped[float] = mapped_column(Float, default=100.0)
    covered_threshold: Mapped[float] = mapped_column(Float, default=0.60)
    gap_threshold: Mapped[float] = mapped_column(Float, default=0.20)
    status: Mapped[str] = mapped_column(String(16), default="pending")

    total_cells: Mapped[int] = mapped_column(Integer, default=0)
    installed_coverage_pct: Mapped[float] = mapped_column(Float, default=0.0)
    effective_coverage_pct: Mapped[float] = mapped_column(Float, default=0.0)
    camera_count: Mapped[int] = mapped_column(Integer, default=0)
    online_camera_count: Mapped[int] = mapped_column(Integer, default=0)
    assumed_omnidirectional_count: Mapped[int] = mapped_column(Integer, default=0)

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class CoverageCell(Base, UUIDMixin):
    """One hexagon of an AOI, scored against the union of camera footprints."""

    __tablename__ = "coverage_cells"
    __table_args__ = (
        Index("ix_coverage_cells_run", "run_id"),
        # spatial_index=False on the column: the GIST index is declared here, so
        # GeoAlchemy2 must not add a second one of its own.
        Index("ix_coverage_cells_geom", "geom", postgresql_using="gist"),
    )

    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("coverage_runs.id", ondelete="CASCADE")
    )
    # MULTIPOLYGON, not POLYGON: cells are clipped to the AOI, and clipping a hexagon
    # against a real district with islands or a ragged coastline splits it into
    # several rings. A POLYGON typmod rejects those rows outright.
    geom: Mapped[Any] = mapped_column(
        Geography(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False)
    )
    installed_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    effective_fraction: Mapped[float] = mapped_column(Float, default=0.0)
    classification: Mapped[str] = mapped_column(String(16), default="gap")
    camera_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
