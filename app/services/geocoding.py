"""Resolve a free-text place name to a district-level point.

The Sentinel sandbox catalogue carries no coordinates -- only an id and a human name
like "08 majewadi-gate-junagadh". Rather than fabricate a position, this matches the
name against the real district boundaries already loaded in `admin_boundaries` and
returns that district's representative point, recording the imprecision alongside it.

The result is deliberately coarse and says so. A district-level point is honest about
what the source actually told us; a plausible-looking street-level coordinate would
not be. Because ingestion dedupes on (department_id, external_camera_id), importing a
CSV of surveyed coordinates later updates these rows in place.
"""

import re
from dataclasses import dataclass

from geoalchemy2 import Geometry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin_boundary import AdminBoundary
from app.models.source_connector import PlaceAlias


@dataclass(frozen=True)
class GeocodeResult:
    longitude: float
    latitude: float
    district_id: str
    district_name: str
    matched_on: str
    precision: str = "district"


def _representative_point():
    """A point guaranteed to lie inside the district polygon."""
    return func.ST_PointOnSurface(AdminBoundary.geom.cast(Geometry()))


def _normalise(text: str) -> str:
    """Lower-case and reduce every run of non-alphanumerics to one space, so
    'majewadi-gate-junagadh' and 'Majewadi Gate, Junagadh' match identically."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class DistrictGeocoder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._districts: dict[str, tuple[str, str]] | None = None
        self._aliases: dict[str, str] | None = None

    async def _load_aliases(self) -> dict[str, str]:
        """alias -> boundary id, read from place_aliases.

        Rows rather than a dict in code: a department whose cameras are named after
        villages nobody here has heard of is onboarded by adding aliases, not by a
        deploy. A wrong district puts a police camera in the wrong place, so an
        unrecognised name still declines rather than guessing.
        """
        if self._aliases is None:
            rows = (
                await self.session.execute(
                    select(PlaceAlias.alias, PlaceAlias.boundary_id)
                )
            ).all()
            self._aliases = {_normalise(a): str(b) for a, b in rows}
        return self._aliases

    async def _load(self) -> dict[str, tuple[str, str]]:
        if self._districts is None:
            rows = (
                await self.session.execute(
                    select(AdminBoundary.id, AdminBoundary.name).where(
                        AdminBoundary.level == "district"
                    )
                )
            ).all()
            self._districts = {
                _normalise(name): (str(bid), name) for bid, name in rows
            }
        return self._districts

    async def locate(self, text: str | None) -> GeocodeResult | None:
        if not text:
            return None

        haystack = f" {_normalise(text)} "
        districts = await self._load()
        aliases = await self._load_aliases()
        by_id = {bid: (bid, name) for _key, (bid, name) in districts.items()}

        # Longest candidate first, so "banas kantha" is not beaten by a stray "banas"
        # and a two-word alias wins over a one-word one.
        candidates = sorted(
            [(k, "district") for k in districts] + [(k, "alias") for k in aliases],
            key=lambda pair: len(pair[0]),
            reverse=True,
        )

        for needle, kind in candidates:
            if f" {needle} " not in haystack:
                continue
            found = (
                by_id.get(aliases[needle])
                if kind == "alias"
                else districts.get(needle)
            )
            if found is None:
                # An alias pointing at a boundary this deployment has not loaded.
                # Decline rather than falling through to a different district.
                continue
            district_id, canonical = found
            point = (
                await self.session.execute(
                    select(
                        func.ST_X(_representative_point()),
                        func.ST_Y(_representative_point()),
                    ).where(AdminBoundary.id == district_id)
                )
            ).one()
            return GeocodeResult(
                longitude=float(point[0]),
                latitude=float(point[1]),
                district_id=district_id,
                district_name=canonical,
                matched_on=needle,
            )
        return None
