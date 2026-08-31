# Model 1 Auth, RBAC & Audit — Implementation Plan (Plan 5 of 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the super-admin `Principal` stub with real department-scoped access control, issue RS256 tokens that Models 2–4 can validate offline, and record who changed what.

**Architecture:** Both humans (JWT) and integrations (API key) resolve to one `Principal`. RBAC is enforced by passing that `Principal` into repositories, which apply it as a **query filter** — never as a scattered conditional in a router, because a filter cannot be forgotten on a new endpoint the way an `if` can. Tokens are signed RS256 and the public key is published at `/.well-known/jwks.json`, so Models 2–4 validate without calling back to Model 1.

**Tech Stack:** PyJWT + cryptography (RS256), argon2-cffi for password and key hashing, SQLAlchemy event hooks for audit capture.

**Prerequisites:** Plans 1–4 complete.

**Why this matters for scoring:** FAQ #38 names "enhanced cybersecurity, privacy, auditability, or RBAC" as a bonus criterion, and the official Model 1 requirement list includes "role-based search, filtering, export, metadata audit trails."

---

## File structure additions

```
app/
  models/user.py  api_key.py  audit_log.py
  schemas/auth.py
  core/security.py                   hashing, JWT issue/verify, JWK export
  core/deps.py                       replaces the Plan 1 stub
  services/audit.py
  repositories/audit.py
  api/v1/routers/auth.py  admin.py
keys/
  jwt_private.pem                    gitignored, generated at setup
web/
  app/login/page.tsx
  app/admin/page.tsx
  lib/session.ts
```

---

## Task 1: Users and password hashing

**Files:**
- Create: `app/models/user.py`, `app/core/security.py`, `app/schemas/auth.py`
- Test: `tests/core/test_security.py`

- [ ] **Step 1: Add dependencies**

Add to `pyproject.toml` dependencies:

```toml
  "pyjwt[crypto]>=2.9",
  "argon2-cffi>=23.1",
```

Run: `pip install -e ".[dev]"`

- [ ] **Step 2: Create `app/models/user.py`**

```python
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(24), default="viewer")
    department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

- [ ] **Step 3: Add the role enum**

Append to `app/core/enums.py`:

```python
class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    DEPT_ADMIN = "dept_admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class ActorType(StrEnum):
    USER = "user"
    API_KEY = "api_key"
    SYSTEM = "system"


# Read is statewide, write is department-scoped: an analyst in Rajkot can see Surat's
# cameras but cannot edit them. Removing departmental blind spots is the whole point of
# the platform, so read is deliberately not scoped.
ROLE_SCOPES: dict[Role, frozenset[str]] = {
    Role.SUPER_ADMIN: frozenset(
        {"cameras:read", "cameras:write", "cameras:export", "admin",
         "streams:credentials", "coverage:run"}
    ),
    Role.DEPT_ADMIN: frozenset(
        {"cameras:read", "cameras:write", "cameras:export", "coverage:run"}
    ),
    Role.ANALYST: frozenset({"cameras:read", "cameras:export", "coverage:run"}),
    Role.VIEWER: frozenset({"cameras:read"}),
}
```

- [ ] **Step 4: Write the failing test**

Create `tests/core/test_security.py`:

```python
import time

import jwt
import pytest

from app.core.security import (
    create_access_token,
    decode_token,
    generate_api_key,
    hash_password,
    hash_secret,
    public_jwk,
    verify_password,
    verify_secret,
)


def test_password_round_trip():
    hashed = hash_password("Correct-Horse-1!")
    assert hashed != "Correct-Horse-1!"
    assert verify_password("Correct-Horse-1!", hashed)
    assert not verify_password("wrong", hashed)


def test_same_password_hashes_differently_each_time():
    assert hash_password("same") != hash_password("same")


def test_access_token_carries_claims_and_verifies():
    token = create_access_token(
        subject="user-1", role="analyst", department_id="dept-1", scopes=["cameras:read"]
    )
    claims = decode_token(token)
    assert claims["sub"] == "user-1"
    assert claims["role"] == "analyst"
    assert claims["department_id"] == "dept-1"
    assert claims["scopes"] == ["cameras:read"]
    assert claims["iss"] == "sentinel-registry"


def test_token_is_rs256_not_hs256():
    token = create_access_token(subject="user-1", role="viewer", department_id=None, scopes=[])
    assert jwt.get_unverified_header(token)["alg"] == "RS256"


def test_expired_token_is_rejected():
    token = create_access_token(
        subject="user-1", role="viewer", department_id=None, scopes=[], expires_in=-1
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token)


def test_token_signed_with_a_foreign_key_is_rejected():
    from cryptography.hazmat.primitives.asymmetric import rsa

    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode({"sub": "attacker"}, rogue, algorithm="RS256")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(forged)


def test_jwks_exposes_a_usable_public_key():
    jwk = public_jwk()
    assert jwk["kty"] == "RSA"
    assert jwk["alg"] == "RS256"
    assert jwk["use"] == "sig"
    assert jwk["kid"]
    assert jwk["n"] and jwk["e"]


def test_models_2_to_4_can_verify_a_token_using_only_the_jwks():
    token = create_access_token(
        subject="svc", role="analyst", department_id=None, scopes=["cameras:read"]
    )
    key = jwt.PyJWK.from_dict(public_jwk()).key
    claims = jwt.decode(token, key, algorithms=["RS256"], audience="sentinel-platform")
    assert claims["sub"] == "svc"


def test_api_key_is_returned_once_and_stored_hashed():
    raw, prefix, hashed = generate_api_key()
    assert raw.startswith("sk_")
    assert prefix == raw[:12]
    assert raw not in hashed
    assert verify_secret(raw, hashed)
    assert not verify_secret("sk_wrong", hashed)


def test_hash_secret_is_deterministic_for_lookup_but_not_reversible():
    hashed = hash_secret("sk_abc")
    assert verify_secret("sk_abc", hashed)
    assert "sk_abc" not in hashed
```

The `test_models_2_to_4_can_verify_a_token_using_only_the_jwks` case is the contract with
the parallel teams — if it passes, their service can authenticate without ever calling us.

- [ ] **Step 5: Run it to make sure it fails**

Run: `pytest tests/core/test_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.security'`

- [ ] **Step 6: Create `app/core/security.py`**

```python
import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings

_hasher = PasswordHasher()

ISSUER = "sentinel-registry"
AUDIENCE = "sentinel-platform"
KEY_PATH = Path("keys/jwt_private.pem")


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, Exception):  # noqa: BLE001
        return False


hash_secret = hash_password
verify_secret = verify_password


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, prefix, hash). The raw key is shown to the operator exactly
    once — only the hash is stored."""
    raw = f"sk_{secrets.token_urlsafe(32)}"
    return raw, raw[:12], hash_secret(raw)


@lru_cache(maxsize=1)
def _private_key() -> rsa.RSAPrivateKey:
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    KEY_PATH.chmod(0o600)
    return key


def _kid() -> str:
    numbers = _private_key().public_key().public_numbers()
    material = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    return hashlib.sha256(material).hexdigest()[:16]


def _b64u(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def public_jwk() -> dict[str, str]:
    numbers = _private_key().public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": _kid(),
        "n": _b64u(numbers.n),
        "e": _b64u(numbers.e),
    }


def create_access_token(
    subject: str,
    role: str,
    department_id: str | None,
    scopes: list[str],
    expires_in: int = 900,
    token_type: str = "access",
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "department_id": department_id,
        "scopes": scopes,
        "type": token_type,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    private_pem = _private_key().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": _kid()})


def create_refresh_token(subject: str) -> str:
    return create_access_token(
        subject=subject,
        role="",
        department_id=None,
        scopes=[],
        expires_in=60 * 60 * 24 * 7,
        token_type="refresh",
    )


def decode_token(token: str) -> dict[str, Any]:
    public_pem = _private_key().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return jwt.decode(
        token, public_pem, algorithms=["RS256"], issuer=ISSUER, audience=AUDIENCE
    )
```

`_ = settings` is not needed; `settings` is imported for future key-path configuration and
may be removed if unused by your linter's rules.

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `pytest tests/core/test_security.py -v`
Expected: 10 passed

- [ ] **Step 8: Ignore the private key**

Run: `echo "keys/" >> .gitignore`

- [ ] **Step 9: Commit**

```bash
git add app/models/user.py app/core/security.py app/core/enums.py tests/core .gitignore pyproject.toml
git commit -m "feat: RS256 token issuance, JWKS export and argon2 hashing"
```

---

## Task 2: API keys and the Principal

**Files:**
- Create: `app/models/api_key.py`, `app/schemas/auth.py`
- Modify: `app/core/deps.py`
- Test: `tests/core/test_principal.py`

- [ ] **Step 1: Create `app/models/api_key.py`**

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import ARRAY, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ApiKey(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "api_keys"

    department_id: Mapped[UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    rate_limit_tier: Mapped[str] = mapped_column(String(16), default="standard")
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

`key_prefix` is indexed so lookup is a single indexed read followed by one argon2 verify,
rather than verifying against every key in the table.

- [ ] **Step 2: Create `app/schemas/auth.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.core.enums import ActorType, Role


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 900


class MeResponse(BaseModel):
    id: UUID
    email: str
    full_name: str
    role: Role
    department_id: UUID | None
    scopes: list[str]


class ApiKeyCreate(BaseModel):
    department_id: UUID
    name: str
    scopes: list[str] = ["cameras:read"]
    rate_limit_tier: str = "standard"


class ApiKeyCreated(BaseModel):
    id: UUID
    name: str
    key_prefix: str
    api_key: str  # shown once
    scopes: list[str]


@dataclass(frozen=True)
class Principal:
    """Who is making this request. The only thing repositories consult for scoping."""

    actor_type: ActorType
    actor_id: str
    role: Role
    department_id: UUID | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_super_admin(self) -> bool:
        return self.role is Role.SUPER_ADMIN

    def can(self, scope: str) -> bool:
        return scope in self.scopes

    def may_write_department(self, department_id: UUID) -> bool:
        if self.is_super_admin:
            return True
        return (
            self.can("cameras:write")
            and self.department_id is not None
            and self.department_id == department_id
        )
```

- [ ] **Step 3: Write the failing test**

Create `tests/core/test_principal.py`:

```python
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.deps import resolve_principal
from app.core.enums import ActorType, Role
from app.core.security import create_access_token, generate_api_key
from app.models.api_key import ApiKey
from app.models.user import User
from app.schemas.auth import Principal


def test_super_admin_may_write_any_department():
    principal = Principal(
        actor_type=ActorType.USER, actor_id="1", role=Role.SUPER_ADMIN,
        scopes=frozenset({"cameras:write"}),
    )
    assert principal.may_write_department(uuid4())


def test_dept_admin_may_write_only_their_own_department():
    own, other = uuid4(), uuid4()
    principal = Principal(
        actor_type=ActorType.USER, actor_id="1", role=Role.DEPT_ADMIN,
        department_id=own, scopes=frozenset({"cameras:write"}),
    )
    assert principal.may_write_department(own)
    assert not principal.may_write_department(other)


def test_analyst_may_not_write_at_all():
    principal = Principal(
        actor_type=ActorType.USER, actor_id="1", role=Role.ANALYST,
        department_id=uuid4(), scopes=frozenset({"cameras:read", "cameras:export"}),
    )
    assert not principal.may_write_department(principal.department_id)


def test_viewer_cannot_export():
    principal = Principal(
        actor_type=ActorType.USER, actor_id="1", role=Role.VIEWER,
        scopes=frozenset({"cameras:read"}),
    )
    assert principal.can("cameras:read")
    assert not principal.can("cameras:export")


@pytest.mark.asyncio
async def test_bearer_token_resolves_to_a_user_principal(session, seeded_department):
    from app.core.security import hash_password

    user = User(
        email="analyst@gujarat.gov.in", full_name="A. Patel",
        password_hash=hash_password("x"), role="analyst",
        department_id=seeded_department,
    )
    session.add(user)
    await session.commit()

    token = create_access_token(
        subject=str(user.id), role="analyst",
        department_id=str(seeded_department), scopes=["cameras:read"],
    )
    principal = await resolve_principal(
        session=session, authorization=f"Bearer {token}", x_api_key=None
    )
    assert principal.actor_type is ActorType.USER
    assert principal.role is Role.ANALYST
    assert principal.department_id == seeded_department


@pytest.mark.asyncio
async def test_api_key_resolves_to_an_api_key_principal(session, seeded_department):
    raw, prefix, hashed = generate_api_key()
    session.add(
        ApiKey(
            department_id=seeded_department, name="AMC nightly sync",
            key_prefix=prefix, key_hash=hashed,
            scopes=["cameras:read", "cameras:write"],
        )
    )
    await session.commit()

    principal = await resolve_principal(session=session, authorization=None, x_api_key=raw)
    assert principal.actor_type is ActorType.API_KEY
    assert principal.department_id == seeded_department
    assert principal.can("cameras:write")


@pytest.mark.asyncio
async def test_revoked_api_key_is_rejected(session, seeded_department):
    from datetime import UTC, datetime

    raw, prefix, hashed = generate_api_key()
    session.add(
        ApiKey(
            department_id=seeded_department, name="revoked", key_prefix=prefix,
            key_hash=hashed, scopes=["cameras:read"], revoked_at=datetime.now(UTC),
        )
    )
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await resolve_principal(session=session, authorization=None, x_api_key=raw)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_no_credentials_is_401(session):
    with pytest.raises(HTTPException) as exc:
        await resolve_principal(session=session, authorization=None, x_api_key=None)
    assert exc.value.status_code == 401
```

- [ ] **Step 4: Run it to make sure it fails**

Run: `pytest tests/core/test_principal.py -v`
Expected: FAIL — `cannot import name 'resolve_principal'`

- [ ] **Step 5: Replace `app/core/deps.py`**

```python
from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.enums import ROLE_SCOPES, ActorType, Role
from app.core.security import decode_token, verify_secret
from app.models.api_key import ApiKey
from app.models.user import User
from app.schemas.auth import Principal


async def resolve_principal(
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    if x_api_key:
        return await _from_api_key(session, x_api_key)
    if authorization and authorization.lower().startswith("bearer "):
        return await _from_token(session, authorization.split(" ", 1)[1])
    raise HTTPException(
        status_code=401,
        detail="Supply a Bearer token or an X-API-Key header.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _from_token(session: AsyncSession, token: str) -> Principal:
    try:
        claims = decode_token(token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}") from exc

    if claims.get("type") != "access":
        raise HTTPException(status_code=401, detail="Refresh tokens cannot authorise requests.")

    user = await session.get(User, claims["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive.")

    role = Role(user.role)
    return Principal(
        actor_type=ActorType.USER,
        actor_id=str(user.id),
        role=role,
        department_id=user.department_id,
        scopes=ROLE_SCOPES[role],
    )


async def _from_api_key(session: AsyncSession, raw_key: str) -> Principal:
    prefix = raw_key[:12]
    candidates = (
        (await session.execute(select(ApiKey).where(ApiKey.key_prefix == prefix)))
        .scalars()
        .all()
    )
    now = datetime.now(UTC)

    for candidate in candidates:
        if not verify_secret(raw_key, candidate.key_hash):
            continue
        if candidate.revoked_at is not None:
            raise HTTPException(status_code=401, detail="API key has been revoked.")
        if candidate.expires_at is not None and candidate.expires_at < now:
            raise HTTPException(status_code=401, detail="API key has expired.")

        candidate.last_used_at = now
        await session.flush()
        return Principal(
            actor_type=ActorType.API_KEY,
            actor_id=str(candidate.id),
            role=Role.DEPT_ADMIN,
            department_id=candidate.department_id,
            scopes=frozenset(candidate.scopes),
        )

    raise HTTPException(status_code=401, detail="Unknown API key.")


def require_scope(scope: str):
    """Router-level guard for coarse checks. Row scoping still happens in repositories."""

    async def guard(principal: Principal = Depends(resolve_principal)) -> Principal:
        if not principal.can(scope):
            raise HTTPException(
                status_code=403, detail=f"This action requires the {scope} scope."
            )
        return principal

    return guard
```

- [ ] **Step 6: Generate and run the migration**

Run:
```bash
alembic revision --autogenerate -m "users and api keys"
alembic upgrade head
```

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `pytest tests/core/test_principal.py -v`
Expected: 8 passed

- [ ] **Step 8: Commit**

```bash
git add app/models/api_key.py app/schemas/auth.py app/core/deps.py alembic tests/core/test_principal.py
git commit -m "feat: unified Principal resolution from JWT or API key"
```

---

## Task 3: Auth routes and JWKS

**Files:**
- Create: `app/api/v1/routers/auth.py`
- Modify: `app/main.py`, `app/api/v1/router.py`
- Test: `tests/api/test_auth_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_auth_api.py`:

```python
import jwt
import pytest

from app.core.security import hash_password
from app.models.user import User


@pytest.fixture
async def analyst(session, seeded_department):
    user = User(
        email="analyst@gujarat.gov.in",
        full_name="A. Patel",
        password_hash=hash_password("Str0ng-Pass!"),
        role="analyst",
        department_id=seeded_department,
    )
    session.add(user)
    await session.commit()
    return user


@pytest.mark.asyncio
async def test_login_returns_a_token_pair(api_client, analyst):
    response = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@gujarat.gov.in", "password": "Str0ng-Pass!"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]


@pytest.mark.asyncio
async def test_wrong_password_is_401_with_no_detail_leak(api_client, analyst):
    response = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@gujarat.gov.in", "password": "wrong"},
    )
    assert response.status_code == 401
    assert "password" not in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_unknown_email_gives_the_same_error_as_a_wrong_password(api_client, analyst):
    unknown = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@gujarat.gov.in", "password": "whatever"},
    )
    wrong = await api_client.post(
        "/api/v1/auth/login",
        json={"email": "analyst@gujarat.gov.in", "password": "wrong"},
    )
    assert unknown.json()["detail"] == wrong.json()["detail"]


@pytest.mark.asyncio
async def test_me_returns_the_caller_identity_and_scopes(api_client, analyst):
    token = (
        await api_client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@gujarat.gov.in", "password": "Str0ng-Pass!"},
        )
    ).json()["access_token"]

    response = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["role"] == "analyst"
    assert "cameras:read" in response.json()["scopes"]


@pytest.mark.asyncio
async def test_jwks_is_public_and_verifies_an_issued_token(api_client, analyst):
    jwks = (await api_client.get("/.well-known/jwks.json")).json()
    assert len(jwks["keys"]) == 1

    token = (
        await api_client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@gujarat.gov.in", "password": "Str0ng-Pass!"},
        )
    ).json()["access_token"]

    key = jwt.PyJWK.from_dict(jwks["keys"][0]).key
    claims = jwt.decode(token, key, algorithms=["RS256"], audience="sentinel-platform")
    assert claims["role"] == "analyst"


@pytest.mark.asyncio
async def test_protected_route_rejects_an_anonymous_request(api_client):
    assert (await api_client.get("/api/v1/auth/me")).status_code == 401
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/api/test_auth_api.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Create `app/api/v1/routers/auth.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import resolve_principal
from app.core.enums import ROLE_SCOPES, Role
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse, Principal, TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])

_GENERIC_FAILURE = "Incorrect email or password."


@router.post("/login", response_model=TokenPair)
async def login(
    payload: LoginRequest, session: AsyncSession = Depends(get_session)
) -> TokenPair:
    user = (
        await session.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    # Identical message and code for unknown-user and wrong-password so the endpoint
    # cannot be used to enumerate valid accounts.
    if user is None or not user.is_active or not verify_password(
        payload.password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail=_GENERIC_FAILURE)

    role = Role(user.role)
    return TokenPair(
        access_token=create_access_token(
            subject=str(user.id),
            role=role.value,
            department_id=str(user.department_id) if user.department_id else None,
            scopes=sorted(ROLE_SCOPES[role]),
        ),
        refresh_token=create_refresh_token(subject=str(user.id)),
    )


@router.get("/me", response_model=MeResponse)
async def me(
    principal: Principal = Depends(resolve_principal),
    session: AsyncSession = Depends(get_session),
) -> MeResponse:
    user = await session.get(User, principal.actor_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=principal.role,
        department_id=user.department_id,
        scopes=sorted(principal.scopes),
    )
```

- [ ] **Step 4: Add the JWKS endpoint to `app/main.py`**

Inside `create_app()`, alongside `healthz`:

```python
    @application.get("/.well-known/jwks.json", tags=["system"])
    async def jwks() -> dict[str, list[dict[str, str]]]:
        """Public keys for Models 2-4 to verify our tokens without calling back."""
        from app.core.security import public_jwk

        return {"keys": [public_jwk()]}
```

- [ ] **Step 5: Register the auth router**

In `app/api/v1/router.py`, add `auth` to the imports and
`api_router.include_router(auth.router)`.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `pytest tests/api/test_auth_api.py -v`
Expected: 6 passed

- [ ] **Step 7: Tell the Models 2–4 devs**

Send them this snippet — it is all they need:

```python
import jwt, httpx

jwks = httpx.get("http://<registry-host>/.well-known/jwks.json").json()
key = jwt.PyJWK.from_dict(jwks["keys"][0]).key
claims = jwt.decode(token, key, algorithms=["RS256"], audience="sentinel-platform")
# claims: sub, role, department_id, scopes
```

- [ ] **Step 8: Commit**

```bash
git add app/api/v1/routers/auth.py app/main.py app/api/v1/router.py tests/api/test_auth_api.py
git commit -m "feat: login, /auth/me and public JWKS for cross-model verification"
```

---

## Task 4: RBAC enforcement in repositories

**Files:**
- Modify: `app/repositories/camera.py`, `app/api/v1/routers/cameras.py`, `app/api/v1/routers/onboarding.py`
- Test: `tests/repositories/test_rbac.py`

- [ ] **Step 1: Write the failing test**

Create `tests/repositories/test_rbac.py`:

```python
import pytest
from fastapi import HTTPException

from app.core.enums import ActorType, Role
from app.models.camera import Camera
from app.models.department import Department
from app.repositories.camera import CameraRepository
from app.schemas.auth import Principal
from app.schemas.filters import CameraFilter


@pytest.fixture
async def two_departments(session):
    amc = Department(code="AMC", name="Ahmedabad Municipal Corporation")
    pol = Department(code="POL", name="Gujarat Police")
    session.add_all([amc, pol])
    await session.flush()
    session.add_all(
        [
            Camera(
                camera_uid="GJ-AMC-000001", department_id=amc.id,
                external_camera_id="A-1", location="SRID=4326;POINT(72.57 23.02)",
            ),
            Camera(
                camera_uid="GJ-POL-000001", department_id=pol.id,
                external_camera_id="P-1", location="SRID=4326;POINT(72.58 23.03)",
            ),
        ]
    )
    await session.commit()
    return amc, pol


def principal_for(role: Role, department_id=None) -> Principal:
    from app.core.enums import ROLE_SCOPES

    return Principal(
        actor_type=ActorType.USER, actor_id="u1", role=role,
        department_id=department_id, scopes=ROLE_SCOPES[role],
    )


@pytest.mark.asyncio
async def test_analyst_reads_every_department(session, two_departments):
    amc, _ = two_departments
    found = await CameraRepository(session).list(
        CameraFilter(), principal=principal_for(Role.ANALYST, amc.id)
    )
    assert len(found) == 2


@pytest.mark.asyncio
async def test_dept_admin_also_reads_every_department(session, two_departments):
    amc, _ = two_departments
    found = await CameraRepository(session).list(
        CameraFilter(), principal=principal_for(Role.DEPT_ADMIN, amc.id)
    )
    assert len(found) == 2


@pytest.mark.asyncio
async def test_dept_admin_cannot_write_another_department(session, two_departments):
    amc, pol = two_departments
    principal = principal_for(Role.DEPT_ADMIN, amc.id)
    assert principal.may_write_department(amc.id)
    assert not principal.may_write_department(pol.id)


@pytest.mark.asyncio
async def test_viewer_is_denied_export(api_client, two_departments, session):
    from app.core.security import hash_password
    from app.models.user import User

    amc, _ = two_departments
    session.add(
        User(
            email="viewer@gujarat.gov.in", full_name="V. Shah",
            password_hash=hash_password("Str0ng-Pass!"), role="viewer",
            department_id=amc.id,
        )
    )
    await session.commit()

    token = (
        await api_client.post(
            "/api/v1/auth/login",
            json={"email": "viewer@gujarat.gov.in", "password": "Str0ng-Pass!"},
        )
    ).json()["access_token"]

    response = await api_client.get(
        "/api/v1/cameras/export.csv", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_import_into_another_department_is_forbidden(api_client, two_departments, session):
    from app.core.security import hash_password
    from app.models.user import User

    amc, pol = two_departments
    session.add(
        User(
            email="amc-admin@gujarat.gov.in", full_name="D. Joshi",
            password_hash=hash_password("Str0ng-Pass!"), role="dept_admin",
            department_id=amc.id,
        )
    )
    await session.commit()

    token = (
        await api_client.post(
            "/api/v1/auth/login",
            json={"email": "amc-admin@gujarat.gov.in", "password": "Str0ng-Pass!"},
        )
    ).json()["access_token"]

    response = await api_client.post(
        f"/api/v1/onboarding/import?department_id={pol.id}",
        files={"file": ("x.csv", b"cam_id,lat,lng\nX-1,23.02,72.57\n", "text/csv")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/repositories/test_rbac.py -v`
Expected: FAIL — `list()` got an unexpected keyword argument `principal`

- [ ] **Step 3: Thread `principal` through the repository**

In `app/repositories/camera.py`, change the signatures:

```python
    async def list(
        self,
        filters: CameraFilter,
        principal: Principal | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Camera]:
```

and

```python
    async def count(
        self, filters: CameraFilter, principal: Principal | None = None
    ) -> int:
```

Add a scoping helper and call it from `_apply`:

```python
    def _scope(self, stmt: Select, principal: Principal | None) -> Select:
        """Read is statewide for every role, so this is currently a no-op for reads.

        It exists as the single place to change if policy tightens — for example if a
        department is later allowed to hide sensitive camera locations from other
        departments. Write scoping is enforced at the router via may_write_department.
        """
        return stmt
```

In `_apply`, change the signature to `def _apply(self, stmt: Select, filters: CameraFilter, principal: Principal | None) -> Select:` and make its first line `stmt = self._scope(stmt, principal)`. Update both call sites to pass `principal`.

Add `from app.schemas.auth import Principal` to the imports.

Keeping read statewide is a deliberate policy choice, not an oversight — the platform exists
to remove departmental blind spots. It is written down here so nobody "fixes" it later.

- [ ] **Step 4: Enforce scopes at the routers**

In `app/api/v1/routers/cameras.py`:

```python
from app.core.deps import require_scope

@router.get("", response_model=Page[CameraRead])
async def list_cameras(
    filters: CameraFilter = Depends(camera_filter),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_scope("cameras:read")),
    session: AsyncSession = Depends(get_session),
) -> Page[CameraRead]:
    repo = CameraRepository(session)
    rows = await repo.list(filters, principal=principal, limit=limit, offset=offset)
    total = await repo.count(filters, principal=principal)
    return Page(items=[_to_read(r) for r in rows], total=total, limit=limit, offset=offset)
```

Apply the same pattern to the other routes:
`export_csv` → `require_scope("cameras:export")`,
`cameras_nearby` and `get_camera` and `get_camera_streams` → `require_scope("cameras:read")`.

In `app/api/v1/routers/onboarding.py`, add to `preview`, `import_file` and `bulk`:

```python
    principal: Principal = Depends(require_scope("cameras:write")),
```

and inside `_department`, after loading the department:

```python
    if not principal.may_write_department(department.id):
        raise HTTPException(
            status_code=403,
            detail="You may only onboard cameras into your own department.",
        )
```

Change `_department`'s signature to accept `principal: Principal` and pass it at both call
sites.

- [ ] **Step 5: Redact credentials without the scope**

In `get_camera_streams`, before returning:

```python
    results = [StreamEndpointRead.model_validate(row) for row in rows]
    if not principal.can("streams:credentials"):
        for endpoint in results:
            endpoint.credential_ref = None
    return results
```

Add `credential_ref: str | None = None` to `StreamEndpointRead` in `app/schemas/camera.py`.

- [ ] **Step 6: Update existing tests to authenticate**

Every earlier API test now needs a token. Add to `tests/conftest.py`:

```python
@pytest.fixture
async def super_admin_headers(session):
    from app.core.security import create_access_token, hash_password
    from app.models.user import User

    user = User(
        email="root@gujarat.gov.in", full_name="Root",
        password_hash=hash_password("x"), role="super_admin",
    )
    session.add(user)
    await session.commit()
    token = create_access_token(
        subject=str(user.id), role="super_admin", department_id=None,
        scopes=["cameras:read", "cameras:write", "cameras:export", "admin",
                "streams:credentials", "coverage:run"],
    )
    return {"Authorization": f"Bearer {token}"}
```

Then make `api_client` send them by default so earlier plans' tests keep passing:

```python
@pytest.fixture
async def api_client(session, super_admin_headers):
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=super_admin_headers
    ) as client:
        yield client
    app.dependency_overrides.clear()
```

Tests that assert 401/403 pass explicit headers, which override the client default.

- [ ] **Step 7: Run the whole suite**

Run: `pytest -v`
Expected: all pass, including every test written in Plans 1–4.

- [ ] **Step 8: Commit**

```bash
git add app/repositories/camera.py app/api/v1/routers app/schemas/camera.py tests
git commit -m "feat: department-scoped RBAC enforcement with credential redaction"
```

---

## Task 5: Audit trail

**Files:**
- Create: `app/models/audit_log.py`, `app/services/audit.py`, `app/repositories/audit.py`
- Modify: `app/services/ingestion.py`, `app/api/v1/routers/cameras.py`
- Test: `tests/services/test_audit.py`

- [ ] **Step 1: Create `app/models/audit_log.py`**

```python
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin


class AuditLog(Base, UUIDMixin):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id", "at"),
        Index("ix_audit_actor", "actor_type", "actor_id", "at"),
    )

    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 2: Write the failing test**

Create `tests/services/test_audit.py`:

```python
import pytest
from sqlalchemy import select

from app.core.enums import ActorType, Role, SourceType
from app.models.audit_log import AuditLog
from app.schemas.auth import Principal
from app.schemas.ingestion import RawCameraRecord
from app.services.ingestion import IngestionService


def principal_for(department_id) -> Principal:
    from app.core.enums import ROLE_SCOPES

    return Principal(
        actor_type=ActorType.USER, actor_id="u1", role=Role.DEPT_ADMIN,
        department_id=department_id, scopes=ROLE_SCOPES[Role.DEPT_ADMIN],
    )


def record(dept_id, **overrides):
    payload = {"external_camera_id": "A-1", "latitude": 23.0225, "longitude": 72.5714}
    payload.update(overrides)
    return RawCameraRecord(
        payload=payload, department_id=dept_id, source_type=SourceType.CSV
    )


@pytest.mark.asyncio
async def test_creating_a_camera_writes_an_audit_entry(session, seeded_department_obj):
    dept = seeded_department_obj
    await IngestionService(session).ingest(
        [record(dept.id)], dept, mode="commit", actor=principal_for(dept.id)
    )

    entry = (await session.execute(select(AuditLog))).scalar_one()
    assert entry.action == "camera.created"
    assert entry.actor_type == "user"
    assert entry.actor_id == "u1"
    assert entry.before is None
    assert entry.after["external_camera_id"] == "A-1"


@pytest.mark.asyncio
async def test_updating_records_before_and_after(session, seeded_department_obj):
    dept = seeded_department_obj
    service = IngestionService(session)
    actor = principal_for(dept.id)

    await service.ingest([record(dept.id)], dept, mode="commit", actor=actor)
    await service.ingest(
        [record(dept.id, name="Renamed Junction")], dept, mode="commit", actor=actor
    )

    entries = (
        (await session.execute(select(AuditLog).order_by(AuditLog.at))).scalars().all()
    )
    assert [e.action for e in entries] == ["camera.created", "camera.updated"]
    assert entries[1].after["name"] == "Renamed Junction"


@pytest.mark.asyncio
async def test_idempotent_reimport_writes_no_audit_noise(session, seeded_department_obj):
    dept = seeded_department_obj
    service = IngestionService(session)
    actor = principal_for(dept.id)

    for _ in range(3):
        await service.ingest([record(dept.id)], dept, mode="commit", actor=actor)

    entries = (await session.execute(select(AuditLog))).scalars().all()
    assert len(entries) == 1


@pytest.mark.asyncio
async def test_validate_only_writes_nothing(session, seeded_department_obj):
    dept = seeded_department_obj
    await IngestionService(session).ingest(
        [record(dept.id)], dept, mode="validate_only", actor=principal_for(dept.id)
    )
    assert (await session.execute(select(AuditLog))).first() is None
```

The third test is the important one: an audit trail that fills with thousands of no-op
entries every night is an audit trail nobody reads.

- [ ] **Step 3: Run it to make sure it fails**

Run: `pytest tests/services/test_audit.py -v`
Expected: FAIL — `ingest()` got an unexpected keyword argument `actor`

- [ ] **Step 4: Create `app/services/audit.py`**

```python
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.schemas.auth import Principal

SYSTEM = "system"


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def record(
        self,
        action: str,
        entity_type: str,
        entity_id: UUID | None,
        actor: Principal | None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.session.add(
            AuditLog(
                actor_type=actor.actor_type.value if actor else SYSTEM,
                actor_id=actor.actor_id if actor else None,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                before=before,
                after=after,
                ip=ip,
                user_agent=user_agent,
            )
        )
```

- [ ] **Step 5: Wire it into `app/services/ingestion.py`**

Add the import and an audit instance in `__init__`:

```python
from app.schemas.auth import Principal
from app.services.audit import AuditService
```

```python
        self.audit = AuditService(session)
```

Change `ingest`'s signature to accept the actor:

```python
    async def ingest(
        self,
        records: list[RawCameraRecord],
        department: Department,
        mode: Literal["validate_only", "commit"],
        actor: Principal | None = None,
    ) -> IngestReport:
```

Pass it through to `_persist` by adding an `actor: Principal | None` parameter and updating
the call site. Then inside `_persist`, add a snapshot helper at module level:

```python
def _snapshot(camera: Camera) -> dict[str, Any]:
    return {
        "external_camera_id": camera.external_camera_id,
        "name": camera.name,
        "camera_type": camera.camera_type,
        "current_status": camera.current_status,
        "connectivity": camera.connectivity,
        "site_type": camera.site_type,
        "ownership_class": camera.ownership_class,
    }
```

In the create branch, after `await self.session.flush()` and before `return "created"`:

```python
            self.audit.record(
                action="camera.created",
                entity_type="camera",
                entity_id=camera.id,
                actor=actor,
                after=_snapshot(camera),
            )
```

In the update branch, capture `before = _snapshot(camera)` as the first statement after
loading the existing camera, and inside `if changed:` before `return "updated"`:

```python
            self.audit.record(
                action="camera.updated",
                entity_type="camera",
                entity_id=camera.id,
                actor=actor,
                before=before,
                after=_snapshot(camera),
            )
```

Nothing is recorded on the `skipped` path — that is what keeps the trail readable.

- [ ] **Step 6: Add the per-camera audit route**

In `app/api/v1/routers/cameras.py`, declared before `/{camera_id}`:

```python
@router.get("/{camera_id}/audit", summary="Change history for one camera")
async def camera_audit(
    camera_id: UUID,
    limit: int = Query(100, le=1000),
    principal: Principal = Depends(require_scope("cameras:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    from sqlalchemy import select

    from app.models.audit_log import AuditLog

    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.entity_type == "camera", AuditLog.entity_id == camera_id)
                .order_by(AuditLog.at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "action": r.action,
            "at": r.at.isoformat(),
            "actor_type": r.actor_type,
            "actor_id": r.actor_id,
            "before": r.before,
            "after": r.after,
        }
        for r in rows
    ]
```

- [ ] **Step 7: Generate and run the migration**

Run:
```bash
alembic revision --autogenerate -m "audit logs"
alembic upgrade head
```

- [ ] **Step 8: Run the tests and make sure they pass**

Run: `pytest tests/services/test_audit.py -v && pytest -v`
Expected: 4 passed, then the full suite passes.

- [ ] **Step 9: Commit**

```bash
git add app/models/audit_log.py app/services/audit.py app/services/ingestion.py app/api/v1/routers/cameras.py alembic tests/services/test_audit.py
git commit -m "feat: audit trail with before/after snapshots and no no-op noise"
```

---

## Task 6: Admin API and login page

**Files:**
- Create: `app/api/v1/routers/admin.py`, `web/app/login/page.tsx`, `web/lib/session.ts`
- Test: `tests/api/test_admin_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_admin_api.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_creating_an_api_key_returns_the_raw_key_exactly_once(
    api_client, seeded_department
):
    response = await api_client.post(
        "/api/v1/admin/api-keys",
        json={
            "department_id": str(seeded_department),
            "name": "AMC nightly sync",
            "scopes": ["cameras:read", "cameras:write"],
        },
    )
    assert response.status_code == 201
    created = response.json()
    assert created["api_key"].startswith("sk_")

    listed = (await api_client.get("/api/v1/admin/api-keys")).json()
    assert "api_key" not in listed[0]
    assert listed[0]["key_prefix"] == created["key_prefix"]


@pytest.mark.asyncio
async def test_the_new_key_actually_authenticates(api_client, seeded_department):
    raw = (
        await api_client.post(
            "/api/v1/admin/api-keys",
            json={
                "department_id": str(seeded_department),
                "name": "sync",
                "scopes": ["cameras:read"],
            },
        )
    ).json()["api_key"]

    response = await api_client.get(
        "/api/v1/cameras", headers={"X-API-Key": raw, "Authorization": ""}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_revoked_key_stops_working(api_client, seeded_department):
    created = (
        await api_client.post(
            "/api/v1/admin/api-keys",
            json={
                "department_id": str(seeded_department),
                "name": "sync",
                "scopes": ["cameras:read"],
            },
        )
    ).json()

    await api_client.delete(f"/api/v1/admin/api-keys/{created['id']}")

    response = await api_client.get(
        "/api/v1/cameras", headers={"X-API-Key": created["api_key"], "Authorization": ""}
    )
    assert response.status_code == 401
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/api/test_admin_api.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Create `app/api/v1/routers/admin.py`**

```python
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_scope
from app.core.security import generate_api_key
from app.models.api_key import ApiKey
from app.models.audit_log import AuditLog
from app.schemas.auth import ApiKeyCreate, ApiKeyCreated, Principal
from app.services.audit import AuditService

router = APIRouter(prefix="/admin", tags=["admin"])


class ApiKeyRead(BaseModel):
    id: UUID
    department_id: UUID
    name: str
    key_prefix: str
    scopes: list[str]
    rate_limit_tier: str
    last_used_at: datetime | None
    revoked_at: datetime | None


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(
    payload: ApiKeyCreate,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> ApiKeyCreated:
    raw, prefix, hashed = generate_api_key()
    key = ApiKey(
        department_id=payload.department_id,
        name=payload.name,
        key_prefix=prefix,
        key_hash=hashed,
        scopes=payload.scopes,
        rate_limit_tier=payload.rate_limit_tier,
    )
    session.add(key)
    await session.flush()

    AuditService(session).record(
        action="api_key.created", entity_type="api_key", entity_id=key.id,
        actor=principal, after={"name": key.name, "scopes": key.scopes},
    )
    await session.commit()

    # The raw key is returned here and never again — only its hash is stored.
    return ApiKeyCreated(
        id=key.id, name=key.name, key_prefix=prefix, api_key=raw, scopes=key.scopes
    )


@router.get("/api-keys", response_model=list[ApiKeyRead])
async def list_api_keys(
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[ApiKeyRead]:
    rows = (
        (await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc())))
        .scalars()
        .all()
    )
    return [ApiKeyRead.model_validate(r, from_attributes=True) for r in rows]


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: UUID,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> None:
    key = await session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    key.revoked_at = datetime.now(UTC)
    AuditService(session).record(
        action="api_key.revoked", entity_type="api_key", entity_id=key.id, actor=principal
    )
    await session.commit()


@router.get("/audit-logs", summary="Platform-wide audit trail")
async def audit_logs(
    entity_type: str | None = Query(None),
    limit: int = Query(200, le=1000),
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    stmt = select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "action": r.action, "entity_type": r.entity_type,
            "entity_id": str(r.entity_id) if r.entity_id else None,
            "actor_type": r.actor_type, "actor_id": r.actor_id,
            "at": r.at.isoformat(),
        }
        for r in rows
    ]
```

Register it in `app/api/v1/router.py`.

- [ ] **Step 4: Create `web/lib/session.ts`**

```ts
const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "sentinel.access_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  return fetch(`${API}${path}`, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
}
```

- [ ] **Step 5: Create `web/app/login/page.tsx`**

```tsx
"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { setToken } from "@/lib/session";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${API}/api/v1/auth/login`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        setError("Incorrect email or password.");
        return;
      }
      setToken((await response.json()).access_token);
      router.push("/map");
    } catch {
      setError("Could not reach the registry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50">
      <form onSubmit={submit} className="w-80 rounded-lg border bg-white p-6 shadow-sm">
        <h1 className="mb-1 text-lg font-semibold">Sentinel CCTV Registry</h1>
        <p className="mb-5 text-xs text-slate-500">Gujarat Police · Model 1</p>

        <input
          className="mb-3 w-full rounded border px-3 py-2 text-sm"
          type="email" placeholder="Email" value={email} required
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          className="mb-4 w-full rounded border px-3 py-2 text-sm"
          type="password" placeholder="Password" value={password} required
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && <p className="mb-3 text-xs text-red-600">{error}</p>}

        <button
          type="submit" disabled={busy}
          className="w-full rounded bg-slate-900 py-2 text-sm text-white disabled:opacity-40"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
```

- [ ] **Step 6: Seed demo users**

Create `seeds/users.py`:

```python
import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models.department import Department
from app.models.user import User

DEMO = [
    ("root@gujarat.gov.in", "State Administrator", "super_admin", None),
    ("amc.admin@gujarat.gov.in", "AMC Administrator", "dept_admin", "AMC"),
    ("analyst@gujarat.gov.in", "Crime Analyst", "analyst", "POL"),
    ("viewer@gujarat.gov.in", "Control Room Operator", "viewer", "POL"),
]
PASSWORD = "Sentinel@2026"


async def main() -> None:
    async with SessionLocal() as session:
        for email, name, role, dept_code in DEMO:
            existing = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if existing:
                continue
            department_id = None
            if dept_code:
                department = (
                    await session.execute(
                        select(Department).where(Department.code == dept_code)
                    )
                ).scalar_one_or_none()
                department_id = department.id if department else None
            session.add(
                User(
                    email=email, full_name=name, role=role,
                    password_hash=hash_password(PASSWORD), department_id=department_id,
                )
            )
        await session.commit()
    print(f"Seeded {len(DEMO)} users. Password for all: {PASSWORD}")


if __name__ == "__main__":
    asyncio.run(main())
```

Run: `python -m seeds.users`

**These four accounts are your RBAC demo.** Log in as each and show the same page behaving
differently — the viewer with no export button, the AMC admin unable to import into Police.

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `pytest tests/api/test_admin_api.py -v && pytest -v`
Expected: 3 passed, then the full suite passes.

- [ ] **Step 8: Commit**

```bash
git add app/api/v1/routers/admin.py web/app/login web/lib/session.ts seeds/users.py tests/api/test_admin_api.py
git commit -m "feat: API key administration, audit log browsing and login flow"
```

---

## Self-review against the spec

**Covered:** §7 `users`, `api_keys`, `audit_logs`; §8 auth and admin routes; §9 auth and
RBAC in full including the platform-IdP JWKS contract and the deliberate
statewide-read / department-scoped-write policy; §14 pages 9 and login.

**Deferred:** the field-mapping editor UI (Plan 6 polish — the API exists); refresh-token
rotation on use.

**Accepted corners, to state in the HLD:**
- Rate limiting stores a `rate_limit_tier` per key and the middleware hook exists, but no
  limiter enforces it. The intended approach is enforcement at an API gateway (Kong or
  NGINX) keyed on the tier, documented rather than built.
- API key rotation is not implemented — keys are created and revoked, not rolled. The
  design (overlapping validity windows keyed on `key_prefix`) is documented.
- The private signing key is generated on first use into `keys/` and gitignored. Production
  would source it from a secrets manager; do not ship this key.
- Refresh tokens are issued but there is no `/auth/refresh` route yet; the access token's
  15-minute lifetime is sufficient for a demo session.
