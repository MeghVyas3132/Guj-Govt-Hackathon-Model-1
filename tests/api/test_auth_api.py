"""Login, token issuance, and the JWKS contract other services depend on."""

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import hash_password
from app.main import app
from app.models.user import User


@pytest.fixture
async def analyst(session):
    user = User(
        email="analyst@gujarat.gov.in",
        full_name="A. Patel",
        password_hash=hash_password("Str0ng-Pass!"),
        role="analyst",
    )
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
async def anon_client(session):
    from app.core.db import get_session

    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


async def login(client, password="Str0ng-Pass!"):
    return await client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@gujarat.gov.in", "password": password},
    )


@pytest.mark.asyncio
async def test_login_returns_a_token_pair(anon_client, analyst):
    response = await login(anon_client)
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] == 900


@pytest.mark.asyncio
async def test_a_wrong_password_is_401(anon_client, analyst):
    assert (await login(anon_client, "wrong")).status_code == 401


@pytest.mark.asyncio
async def test_an_unknown_email_gives_the_identical_error(anon_client, analyst):
    """Otherwise the endpoint enumerates which accounts exist."""
    unknown = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@gujarat.gov.in", "password": "whatever"},
    )
    wrong = await login(anon_client, "wrong")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


@pytest.mark.asyncio
async def test_a_deactivated_user_cannot_log_in(session, anon_client, analyst):
    analyst.is_active = False
    await session.commit()
    assert (await login(anon_client)).status_code == 401


@pytest.mark.asyncio
async def test_me_reports_identity_and_scopes(anon_client, analyst):
    token = (await login(anon_client)).json()["access_token"]
    response = await anon_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "analyst@gujarat.gov.in"
    assert body["role"] == "analyst"
    assert "cameras:read" in body["scopes"]
    assert "cameras:write" not in body["scopes"]


@pytest.mark.asyncio
async def test_a_refresh_token_yields_a_new_pair(anon_client, analyst):
    refresh = (await login(anon_client)).json()["refresh_token"]
    response = await anon_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


@pytest.mark.asyncio
async def test_an_access_token_cannot_be_exchanged_for_tokens(anon_client, analyst):
    access = (await login(anon_client)).json()["access_token"]
    response = await anon_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": access}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_jwks_is_public_and_verifies_an_issued_token(anon_client, analyst):
    """The contract with the analytics layers: they verify our tokens offline, so
    their login does not fail when this service restarts."""
    jwks = (await anon_client.get("/.well-known/jwks.json")).json()
    assert len(jwks["keys"]) == 1
    assert jwks["keys"][0]["alg"] == "RS256"
    assert jwks["keys"][0]["kid"]

    token = (await login(anon_client)).json()["access_token"]
    key = jwt.PyJWK.from_dict(jwks["keys"][0]).key
    claims = jwt.decode(
        token, key, algorithms=["RS256"], audience="sentinel-platform"
    )
    assert claims["role"] == "analyst"
    assert claims["iss"] == "sentinel-registry"


@pytest.mark.asyncio
async def test_tokens_are_rs256_not_hmac(anon_client, analyst):
    """HS256 would mean handing every consumer a key that can also mint tokens."""
    token = (await login(anon_client)).json()["access_token"]
    assert jwt.get_unverified_header(token)["alg"] == "RS256"


@pytest.mark.asyncio
async def test_jwks_never_exposes_the_private_key(anon_client):
    body = (await anon_client.get("/.well-known/jwks.json")).text
    assert "PRIVATE" not in body
    assert '"d"' not in body  # the RSA private exponent
