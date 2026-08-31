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

# Towns, talukas and landmarks that name no district themselves, mapped to the district
# name as spelled in the 2011 Census boundary data. Extend this rather than loosening
# the matching: a wrong district is worse than no match.
PLACE_ALIASES: dict[str, str] = {
    # Common spellings that differ from the census form
    "ahmedabad": "Ahmadabad",
    "amdavad": "Ahmadabad",
    "mehsana": "Mahesana",
    "dahod": "Dohad",
    "banaskantha": "Banas Kantha",
    "banas kantha": "Banas Kantha",
    "sabarkantha": "Sabar Kantha",
    "panchmahal": "Panch Mahals",
    "kutch": "Kachchh",
    "kuchchh": "Kachchh",
    "dangs": "The Dangs",
    # Towns and talukas
    "bilimora": "Navsari",
    "gandevi": "Navsari",
    "khapariya": "Navsari",
    "khaparia": "Navsari",
    "gandhidham": "Kachchh",
    "bhuj": "Kachchh",
    "adalaj": "Gandhinagar",
    "dehgam": "Gandhinagar",
    "kalol": "Gandhinagar",
    "veraval": "Gir Somnath",
    "dolatpara": "Junagadh",
    "timbavadi": "Junagadh",
    "majewadi": "Junagadh",
    # Ahmedabad landmarks that name no city
    "chiman bhai": "Ahmadabad",
    "chimanbhai": "Ahmadabad",
    "janpath": "Ahmadabad",
    "paldi": "Ahmadabad",
    "visat": "Ahmadabad",
    "cn vidhyalaya": "Ahmadabad",
    "vastrapur": "Ahmadabad",
    "sarkhej": "Ahmadabad",
}


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

        # Longest candidate first, so "banas kantha" is not beaten by a stray "banas",
        # and a two-word alias wins over a one-word one.
        candidates: list[tuple[str, str]] = [
            (key, key) for key in districts
        ] + [(alias, alias) for alias in PLACE_ALIASES]
        candidates.sort(key=lambda pair: len(pair[0]), reverse=True)

        for needle, _ in candidates:
            if f" {needle} " not in haystack:
                continue
            district_name = PLACE_ALIASES.get(needle)
            key = _normalise(district_name) if district_name else needle
            found = districts.get(key)
            if found is None:
                # An alias pointing at a district this deployment has not loaded.
                # Decline rather than fall through to a different one.
                continue
            district_id, canonical = found
            point = (
                await self.session.execute(
                    select(
                        # PointOnSurface, not Centroid: a concave district's centroid
                        # can fall outside its own polygon and land in a neighbour.
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
