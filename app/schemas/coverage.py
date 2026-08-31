from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CoverageRunRequest(BaseModel):
    boundary_id: UUID = Field(description="District or taluka to analyse.")
    # The 25 m floor is a guard, not a preference: cell count grows with the square of
    # the inverse edge length, so a district at 25 m is already millions of hexagons.
    hex_edge_m: float = Field(default=100.0, ge=25, le=2000)
    covered_threshold: float = Field(default=0.60, gt=0, le=1)
    gap_threshold: float = Field(default=0.20, ge=0, lt=1)


class CoverageRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    boundary_id: UUID | None
    boundary_name: str | None
    status: str
    hex_edge_m: float
    total_cells: int
    installed_coverage_pct: float
    effective_coverage_pct: float
    camera_count: int
    online_camera_count: int
    assumed_omnidirectional_count: int
    created_at: datetime
    finished_at: datetime | None
    error: str | None

    @property
    def outage_gap_pct(self) -> float:
        """Coverage lost purely because cameras are offline."""
        return round(self.installed_coverage_pct - self.effective_coverage_pct, 2)
