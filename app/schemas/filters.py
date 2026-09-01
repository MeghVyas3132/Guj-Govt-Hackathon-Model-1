from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class CameraFilter(BaseModel):
    """One filter object, three consumers.

    The list endpoint, the CSV export and the MVT tile query all narrow the registry
    with this same object, so the map and the table can never show different result
    sets for the same query. An empty list means "do not constrain on this field",
    never "match nothing".
    """

    q: str | None = Field(default=None, description="Free text over uid, name, address.")
    department_ids: list[UUID] = Field(default_factory=list)
    camera_types: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
    ownership_classes: list[str] = Field(default_factory=list)
    district_id: UUID | None = None
    near_lat: float | None = Field(default=None, ge=-90, le=90)
    near_lon: float | None = Field(default=None, ge=-180, le=180)
    radius_m: float | None = Field(default=None, gt=0, le=200_000)

    @model_validator(mode="after")
    def radius_search_needs_all_three(self) -> "CameraFilter":
        """Reject a half-specified radius instead of quietly ignoring it.

        Dropping the predicate for `?near_lat=23&radius_m=500` would return the whole
        registry, and it would look like a legitimate answer to a proximity question.
        """
        provided = [self.near_lat, self.near_lon, self.radius_m]
        if any(v is not None for v in provided) and any(v is None for v in provided):
            raise ValueError("near_lat, near_lon and radius_m must be supplied together.")
        return self

    @property
    def has_radius(self) -> bool:
        return self.radius_m is not None
