import pytest

from app.models.vocabulary import VocabularyTerm
from app.services.vocabulary import VocabularyService


@pytest.fixture
async def vocab(session):
    # The schema fixture seeds the shipped terms so camera_footprint works. These
    # tests control the vocabulary exactly, so clear it first.
    from sqlalchemy import delete

    await session.execute(delete(VocabularyTerm))
    session.add_all([
        VocabularyTerm(dimension="camera_type", code="fixed", label="Fixed",
                       coverage_range_m=100.0, coverage_fov_deg=90.0,
                       is_omnidirectional=False),
        VocabularyTerm(dimension="camera_type", code="ptz", label="PTZ",
                       coverage_range_m=250.0, is_omnidirectional=True),
        VocabularyTerm(dimension="camera_type", code="other", label="Other",
                       is_fallback=True, is_omnidirectional=True),
        VocabularyTerm(dimension="status", code="online", label="Online"),
        VocabularyTerm(dimension="status", code="unknown", label="Unknown",
                       is_fallback=True),
    ])
    await session.commit()
    return VocabularyService(session)


@pytest.mark.asyncio
async def test_a_known_term_resolves_to_itself(vocab):
    assert await vocab.resolve("camera_type", "ptz") == ("ptz", None)


@pytest.mark.asyncio
async def test_matching_is_case_insensitive(vocab):
    code, warning = await vocab.resolve("camera_type", "PTZ")
    assert code == "ptz"
    assert warning is None


@pytest.mark.asyncio
async def test_an_unknown_term_falls_back_and_warns_rather_than_raising(vocab):
    """A department shipping a camera type nobody anticipated must not break its
    own nightly sync."""
    code, warning = await vocab.resolve("camera_type", "fisheye-360")
    assert code == "other"
    assert "fisheye-360" in warning
    assert "vocabulary term" in warning


@pytest.mark.asyncio
async def test_an_empty_value_resolves_to_nothing(vocab):
    assert await vocab.resolve("camera_type", None) == (None, None)
    assert await vocab.resolve("camera_type", "") == (None, None)


@pytest.mark.asyncio
async def test_a_new_term_is_recognised_without_a_restart(session, vocab):
    """The whole point: a new camera type is an INSERT, not a deploy."""
    assert (await vocab.resolve("camera_type", "fisheye"))[0] == "other"

    session.add(VocabularyTerm(dimension="camera_type", code="fisheye", label="Fisheye"))
    await session.commit()

    fresh = VocabularyService(session)
    assert await fresh.resolve("camera_type", "fisheye") == ("fisheye", None)


@pytest.mark.asyncio
async def test_a_retired_term_stops_resolving(session, vocab):
    from sqlalchemy import update

    await session.execute(
        update(VocabularyTerm)
        .where(VocabularyTerm.code == "ptz")
        .values(is_active=False)
    )
    await session.commit()

    code, warning = await VocabularyService(session).resolve("camera_type", "ptz")
    assert code == "other"
    assert warning is not None


@pytest.mark.asyncio
async def test_coverage_geometry_comes_from_the_term(vocab):
    terms = await vocab.known("camera_type")
    assert terms["ptz"].coverage_range_m == 250.0
    assert terms["ptz"].is_omnidirectional is True
    assert terms["fixed"].coverage_range_m == 100.0
    assert terms["fixed"].coverage_fov_deg == 90.0
    assert terms["fixed"].is_omnidirectional is False


@pytest.mark.asyncio
async def test_dimensions_are_independent(vocab):
    assert (await vocab.resolve("status", "online"))[0] == "online"
    # 'online' is not a camera type, so it must not leak across dimensions.
    assert (await vocab.resolve("camera_type", "online"))[0] == "other"


@pytest.mark.asyncio
async def test_an_unconfigured_dimension_accepts_anything(session):
    from sqlalchemy import delete

    await session.execute(delete(VocabularyTerm))
    await session.commit()
    """Permissive when unconfigured, strict once configured. A registry with no
    vocabulary loaded must not null every controlled field it is handed."""
    service = VocabularyService(session)
    assert await service.resolve("camera_type", "whatever-vendor-says") == (
        "whatever-vendor-says",
        None,
    )


@pytest.mark.asyncio
async def test_configuring_one_dimension_does_not_constrain_another(session, vocab):
    """camera_type is configured in the fixture; connectivity is not."""
    assert (await vocab.resolve("camera_type", "nonsense"))[0] == "other"
    assert (await vocab.resolve("connectivity", "starlink"))[0] == "starlink"
