"""Resolving values against the vocabulary tables.

The rule that matters: a value we have never seen is **recorded, not discarded**.
It normalises to the dimension's fallback term for querying, the original text is
preserved in the camera's metadata, and the row carries a warning so an operator
can promote it to a real term with one INSERT.

Flattening an unknown camera type to `other` and forgetting what it said is how a
registry quietly loses the very information it exists to hold.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vocabulary import VocabularyTerm


@dataclass(frozen=True)
class TermInfo:
    code: str
    label: str
    coverage_range_m: float | None = None
    coverage_fov_deg: float | None = None
    is_omnidirectional: bool | None = None


class VocabularyService:
    """Loads a dimension's terms once per request and answers lookups from memory."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._cache: dict[str, dict[str, TermInfo]] = {}
        self._fallbacks: dict[str, str] = {}

    async def _load(self, dimension: str) -> dict[str, TermInfo]:
        if dimension not in self._cache:
            rows = (
                await self.session.execute(
                    select(VocabularyTerm).where(
                        VocabularyTerm.dimension == dimension,
                        VocabularyTerm.is_active,
                    )
                )
            ).scalars().all()
            self._cache[dimension] = {
                row.code: TermInfo(
                    code=row.code,
                    label=row.label,
                    coverage_range_m=row.coverage_range_m,
                    coverage_fov_deg=row.coverage_fov_deg,
                    is_omnidirectional=row.is_omnidirectional,
                )
                for row in rows
            }
            fallback = next((r.code for r in rows if r.is_fallback), None)
            if fallback is not None:
                self._fallbacks[dimension] = fallback
        return self._cache[dimension]

    async def known(self, dimension: str) -> dict[str, TermInfo]:
        return await self._load(dimension)

    async def fallback(self, dimension: str) -> str | None:
        await self._load(dimension)
        return self._fallbacks.get(dimension)

    async def is_valid(self, dimension: str, code: str | None) -> bool:
        if code is None:
            return True
        return code in await self._load(dimension)

    async def resolve(self, dimension: str, value: str | None) -> tuple[str | None, str | None]:
        """Returns (canonical_code, warning).

        An exact match resolves. A case-insensitive match resolves. Anything else
        falls back and warns -- it never raises, because one unrecognised word in a
        nightly departmental sync must not stop the sync.
        """
        if value in (None, ""):
            return None, None

        terms = await self._load(dimension)
        text = str(value).strip()

        # An unconfigured dimension accepts anything. A registry with no vocabulary
        # loaded must not silently null every controlled field it is handed --
        # permissive when unconfigured, strict once configured.
        if not terms:
            return text, None
        if text in terms:
            return text, None

        lowered = text.lower()
        for code in terms:
            if code.lower() == lowered:
                return code, None

        fallback = self._fallbacks.get(dimension)
        return (
            fallback,
            f"Unknown {dimension} {text!r}; recorded as {fallback!r} and preserved "
            f"in metadata. Add it as a vocabulary term to classify it properly.",
        )
