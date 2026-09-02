"""Settings a deployment must be able to change.

Each of these was hardcoded at some point, and each would have failed only after
deploying -- which is the worst time to find out.
"""

import os

import pytest


def test_cors_origins_are_configurable(monkeypatch):
    """Hardcoded to localhost:3000, this refused the deployed portal and any
    other model's browser client with an error that looks like the API being
    down."""
    from app.core.config import Settings

    monkeypatch.setenv(
        "SENTINEL_CORS_ORIGINS",
        "https://registry.example.gov.in,https://model2.example.gov.in",
    )
    assert Settings().cors_origin_list == [
        "https://registry.example.gov.in",
        "https://model2.example.gov.in",
    ]


def test_cors_origins_tolerate_spacing(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("SENTINEL_CORS_ORIGINS", " https://a.test , https://b.test ")
    assert Settings().cors_origin_list == ["https://a.test", "https://b.test"]


def test_the_app_uses_the_configured_origins(monkeypatch):
    from starlette.middleware.cors import CORSMiddleware

    monkeypatch.setenv("SENTINEL_CORS_ORIGINS", "https://deployed.example.gov.in")
    # Settings is read at import; rebuild it the way a fresh process would.
    import importlib

    import app.core.config as config

    importlib.reload(config)
    import app.main as main

    importlib.reload(main)

    app = main.create_app()
    origins = [
        m.kwargs.get("allow_origins")
        for m in app.user_middleware
        if m.cls is CORSMiddleware
    ]
    assert origins and "https://deployed.example.gov.in" in origins[0]

    # Leave the module tree as the rest of the suite expects it.
    monkeypatch.delenv("SENTINEL_CORS_ORIGINS")
    importlib.reload(config)
    importlib.reload(main)


def test_the_database_url_is_configurable(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv(
        "SENTINEL_DATABASE_URL", "postgresql+asyncpg://u:p@db.internal:5432/sentinel"
    )
    assert "db.internal" in Settings().database_url


def test_the_redis_url_is_configurable(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("SENTINEL_REDIS_URL", "redis://cache.internal:6379/2")
    assert Settings().redis_url == "redis://cache.internal:6379/2"


def test_a_signing_key_can_be_supplied_by_environment(monkeypatch, tmp_path):
    """Without this, a container with ephemeral storage mints a new key on every
    restart -- invalidating every issued token and breaking the offline JWKS
    verification Models 2-4 depend on."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    generated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = generated.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    monkeypatch.setenv("SENTINEL_JWT_PRIVATE_KEY_PEM", pem)
    import importlib

    import app.core.config as config
    import app.core.security as security

    importlib.reload(config)
    importlib.reload(security)

    # The key in use must be the one supplied, so a restart keeps the same kid.
    assert security._private_key().private_numbers() == generated.private_numbers()

    monkeypatch.delenv("SENTINEL_JWT_PRIVATE_KEY_PEM")
    importlib.reload(config)
    importlib.reload(security)


def test_the_key_path_is_configurable(monkeypatch, tmp_path):
    """The other way to keep one key: a mounted volume."""
    from app.core.config import Settings

    target = tmp_path / "mounted" / "jwt.pem"
    monkeypatch.setenv("SENTINEL_JWT_PRIVATE_KEY_PATH", str(target))
    assert Settings().jwt_private_key_path == str(target)
