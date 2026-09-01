"""Credential resolution.

This gates every authenticated sync, and it was shipped untested. The behaviour
that matters across environments: a deployment injects secrets by environment
variable, a demo keeps them in the table, and neither should silently fall back
to the other in a way that makes a sync fail with an unhelpful error.
"""

import pytest

from app.models.source_connector import Credential
from app.services.credentials import CredentialResolver


@pytest.mark.asyncio
async def test_a_stored_secret_resolves(session):
    session.add(Credential(name="vendor_key", value="s3cret"))
    await session.commit()
    assert await CredentialResolver(session).resolve("vendor_key") == "s3cret"


@pytest.mark.asyncio
async def test_the_environment_wins_over_the_table(session, monkeypatch):
    """Production injects secrets by env var. If the table shadowed it, rotating a
    secret in the deployment would silently keep using a stale database row."""
    session.add(Credential(name="vendor_key", value="stale-from-demo-seed"))
    await session.commit()
    monkeypatch.setenv("VENDOR_KEY", "rotated-in-prod")
    assert await CredentialResolver(session).resolve("vendor_key") == "rotated-in-prod"


@pytest.mark.asyncio
async def test_the_env_var_name_is_the_uppercased_ref(session, monkeypatch):
    monkeypatch.setenv("SENTINEL_SESSION", "abc")
    assert await CredentialResolver(session).resolve("sentinel_session") == "abc"


@pytest.mark.asyncio
async def test_a_lowercase_env_var_is_not_picked_up(session, monkeypatch):
    """One convention, so a missing secret is a missing secret rather than a
    works-on-my-machine difference between shells."""
    monkeypatch.setenv("vendor_key", "lower")
    assert await CredentialResolver(session).resolve("vendor_key") is None


@pytest.mark.asyncio
async def test_an_empty_env_var_falls_through_to_the_table(session, monkeypatch):
    """An unset variable often materialises as empty string in a container."""
    session.add(Credential(name="vendor_key", value="from-table"))
    await session.commit()
    monkeypatch.setenv("VENDOR_KEY", "")
    assert await CredentialResolver(session).resolve("vendor_key") == "from-table"


@pytest.mark.asyncio
async def test_an_unknown_ref_resolves_to_none_rather_than_raising(session):
    assert await CredentialResolver(session).resolve("nope") is None


@pytest.mark.asyncio
async def test_a_none_or_empty_ref_resolves_to_none(session):
    resolver = CredentialResolver(session)
    assert await resolver.resolve(None) is None
    assert await resolver.resolve("") is None


@pytest.mark.asyncio
async def test_refs_are_case_sensitive_in_the_table(session):
    session.add(Credential(name="vendor_key", value="lower"))
    await session.commit()
    assert await CredentialResolver(session).resolve("Vendor_Key") is None


@pytest.mark.asyncio
async def test_a_secret_containing_whitespace_survives_intact(session):
    """Session cookies and PEM blocks legitimately contain padding and newlines."""
    value = "  token with spaces \n and a newline  "
    session.add(Credential(name="k", value=value))
    await session.commit()
    assert await CredentialResolver(session).resolve("k") == value


@pytest.mark.asyncio
async def test_a_long_secret_survives(session):
    value = "x" * 1900
    session.add(Credential(name="k", value=value))
    await session.commit()
    assert await CredentialResolver(session).resolve("k") == value


@pytest.mark.asyncio
async def test_a_unicode_secret_survives(session):
    session.add(Credential(name="k", value="пароль-ગુજરાત-🔑"))
    await session.commit()
    assert await CredentialResolver(session).resolve("k") == "пароль-ગુજરાત-🔑"


@pytest.mark.asyncio
async def test_the_value_is_never_in_the_models_repr(session):
    """A stray log line must not leak a secret."""
    credential = Credential(name="vendor_key", value="TOP-SECRET-VALUE")
    assert "TOP-SECRET-VALUE" not in repr(credential)
    assert "vendor_key" in repr(credential)


@pytest.mark.asyncio
async def test_two_refs_do_not_bleed_into_each_other(session):
    session.add_all([
        Credential(name="a_key", value="AAA"),
        Credential(name="b_key", value="BBB"),
    ])
    await session.commit()
    resolver = CredentialResolver(session)
    assert await resolver.resolve("a_key") == "AAA"
    assert await resolver.resolve("b_key") == "BBB"
