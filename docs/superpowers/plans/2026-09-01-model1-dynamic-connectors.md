# Dynamic Source Connectors — Implementation Plan (Plan 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make onboarding a new department's camera source a database row rather than a Python class. No vendor name, URL, protocol, auth scheme or place name may remain hardcoded in application code.

**Why:** The challenge is 26 departments on different vendors, and the official criteria reward *"modular adapter frameworks"* and *"onboarding future vendors without major redesign."* Today `SentinelAdapter` is a named class, `if adapter_code != SentinelAdapter.code` gates the sync route, protocol→reachability policy is a dict in code, one vendor's URLs sit in global settings, and 42 place names live in a Python dict. Each of those is a redeploy standing between a department and onboarding.

The target demo beat: **onboard a 27th department live, in front of the judges, without touching the codebase.**

**Architecture:** Two config tables — `source_connectors` (how to fetch and interpret a catalogue) and `place_aliases` (how to resolve a place name) — plus a `credentials` table so secrets are referenced, never inlined. `SentinelAdapter` becomes a generic `RestCatalogueAdapter` driven entirely by a connector row; "sentinel" becomes seed data.

**Prerequisites:** Plans 1–4 complete.

---

## File structure

```
app/
  models/source_connector.py     SourceConnector, Credential
  models/place_alias.py          PlaceAlias
  schemas/connector.py           ConnectorConfig, AuthConfig, EndpointRule
  adapters/rest_catalogue.py     replaces sentinel_adapter.py
  services/credentials.py        credential_ref -> secret resolution
  api/v1/routers/connectors.py   CRUD + sync
seeds/
  connectors.py                  seeds the sentinel connector row
  place_aliases.py               seeds today's 42 aliases from the dict
```

---

## Task 1: Credentials, referenced not inlined

**Files:** Create `app/models/source_connector.py` (the `Credential` half), `app/services/credentials.py`; Test `tests/services/test_credentials.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from app.models.source_connector import Credential
from app.services.credentials import CredentialResolver


@pytest.mark.asyncio
async def test_resolves_a_stored_secret(session):
    session.add(Credential(name="sentinel_pw", value="DBAD-4UQP-BSTV"))
    await session.commit()
    assert await CredentialResolver(session).resolve("sentinel_pw") == "DBAD-4UQP-BSTV"


@pytest.mark.asyncio
async def test_an_environment_variable_overrides_the_stored_value(session, monkeypatch):
    """Deployments inject secrets by env var; the table is the fallback so a demo
    works without one."""
    session.add(Credential(name="sentinel_pw", value="stored"))
    await session.commit()
    monkeypatch.setenv("SENTINEL_PW", "from-env")
    assert await CredentialResolver(session).resolve("sentinel_pw") == "from-env"


@pytest.mark.asyncio
async def test_an_unknown_ref_resolves_to_none_rather_than_raising(session):
    assert await CredentialResolver(session).resolve("nope") is None
    assert await CredentialResolver(session).resolve(None) is None


@pytest.mark.asyncio
async def test_the_secret_is_never_included_in_the_models_repr(session):
    cred = Credential(name="sentinel_pw", value="DBAD-4UQP-BSTV")
    assert "DBAD" not in repr(cred)
```

- [ ] **Step 2: Run it, confirm it fails** — `ModuleNotFoundError: app.services.credentials`

- [ ] **Step 3: Implement**

```python
# app/models/source_connector.py (Credential)
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Credential(Base, UUIDMixin, TimestampMixin):
    """A secret referenced by name from connector config.

    Storing the value here keeps a demo self-contained. Production sources it from a
    secrets manager via the environment; the resolver prefers that. Never inline a
    secret into connector JSON -- config is readable by anyone with admin scope.
    """

    __tablename__ = "credentials"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[str] = mapped_column(String(2000))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __repr__(self) -> str:
        return f"<Credential {self.name!r}>"
```

```python
# app/services/credentials.py
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source_connector import Credential


class CredentialResolver:
    """Turns a credential_ref into a secret. Environment first, table second."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve(self, ref: str | None) -> str | None:
        if not ref:
            return None
        from_env = os.environ.get(ref.upper())
        if from_env:
            return from_env
        return (
            await self.session.execute(
                select(Credential.value).where(Credential.name == ref)
            )
        ).scalar_one_or_none()
```

- [ ] **Step 4: Run tests, confirm pass. Step 5: migration. Step 6: commit.**

---

## Task 2: The connector config schema

**Files:** Create `app/schemas/connector.py`, the `SourceConnector` model; Test `tests/schemas/test_connector_config.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from pydantic import ValidationError

from app.schemas.connector import AuthConfig, ConnectorConfig, EndpointRule


def test_a_minimal_connector_validates():
    config = ConnectorConfig(catalogue_url="https://example/cameras.json")
    assert config.auth.type == "none"
    assert config.id_keys == ["id"]
    assert config.endpoint_rules == []


def test_cookie_auth_requires_a_name():
    with pytest.raises(ValidationError):
        AuthConfig(type="cookie", credential_ref="pw")


def test_header_auth_requires_a_name():
    with pytest.raises(ValidationError):
        AuthConfig(type="header", credential_ref="pw")


def test_bearer_auth_needs_no_name():
    assert AuthConfig(type="bearer", credential_ref="pw").name is None


def test_an_endpoint_rule_needs_a_key_or_a_template():
    """A rule that can neither read a URL nor build one is silently useless."""
    with pytest.raises(ValidationError):
        EndpointRule(protocol="hls", reachability="public_cdn")


def test_a_template_must_reference_the_id_placeholder():
    with pytest.raises(ValidationError):
        EndpointRule(
            protocol="hls", url_template="https://example/fixed.m3u8",
            reachability="public_cdn",
        )
```

- [ ] **Step 2: Run it, confirm it fails. Step 3: Implement**

```python
# app/schemas/connector.py
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.enums import Reachability, StreamProtocol

AuthType = Literal["none", "cookie", "header", "bearer", "basic"]


class AuthConfig(BaseModel):
    """How to authenticate to a source. `name` is the cookie or header name;
    `credential_ref` names a row in `credentials`, never the secret itself."""

    type: AuthType = "none"
    name: str | None = None
    credential_ref: str | None = None

    @model_validator(mode="after")
    def named_schemes_require_a_name(self) -> "AuthConfig":
        if self.type in ("cookie", "header") and not self.name:
            raise ValueError(f"auth.type={self.type!r} requires auth.name")
        if self.type != "none" and not self.credential_ref:
            raise ValueError(f"auth.type={self.type!r} requires auth.credential_ref")
        return self


class EndpointRule(BaseModel):
    """How to obtain one stream URL for a camera.

    `url_key` reads it from the catalogue entry when present -- always preferred,
    since the source is authoritative. `url_template` builds it when the catalogue
    omits URLs, as the Sentinel sandbox does.
    """

    protocol: StreamProtocol
    url_key: str | None = None
    url_template: str | None = None
    reachability: Reachability
    requires_auth: bool = False
    credential_ref: str | None = None
    is_primary: bool = False

    @model_validator(mode="after")
    def must_be_able_to_produce_a_url(self) -> "EndpointRule":
        if not self.url_key and not self.url_template:
            raise ValueError("an endpoint rule needs url_key, url_template, or both")
        if self.url_template and "{id}" not in self.url_template:
            raise ValueError("url_template must contain the {id} placeholder")
        return self


class ConnectorConfig(BaseModel):
    catalogue_url: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    # Where the camera array lives: None for a bare array, else a dotted path.
    root_path: str | None = None
    id_keys: list[str] = Field(default_factory=lambda: ["id"])
    endpoint_rules: list[EndpointRule] = Field(default_factory=list)
    request_timeout_s: float = 30.0
    extra: dict[str, Any] = Field(default_factory=dict)
```

```python
# app/models/source_connector.py (SourceConnector)
class SourceConnector(Base, UUIDMixin, TimestampMixin):
    """Everything needed to pull one department's catalogue. A new vendor is a row."""

    __tablename__ = "source_connectors"

    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Steps 4–6: tests pass, migration, commit.**

---

## Task 3: The generic REST catalogue adapter

**Files:** Create `app/adapters/rest_catalogue.py`; delete `app/adapters/sentinel_adapter.py`; Test `tests/adapters/test_rest_catalogue.py`

The existing `tests/adapters/test_sentinel_adapter.py` covers real behaviour — port every case to the generic adapter driven by a connector config, so the coverage is not lost.

- [ ] **Step 1: Write the failing tests** — port the existing suite, plus:

```python
@pytest.mark.asyncio
async def test_cookie_auth_sends_the_named_cookie():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, json=[{"id": "cam01"}])

    config = ConnectorConfig(
        catalogue_url="https://example/cameras.json",
        auth=AuthConfig(type="cookie", name="sentinel", credential_ref="pw"),
    )
    adapter = RestCatalogueAdapter(config, secret="s3cret",
                                  transport=httpx.MockTransport(handler))
    await adapter.fetch(uuid4())
    assert seen["cookie"] == "sentinel=s3cret"


@pytest.mark.asyncio
async def test_header_auth_sends_the_named_header():
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json=[{"id": "cam01"}])

    config = ConnectorConfig(
        catalogue_url="https://example/cameras.json",
        auth=AuthConfig(type="header", name="X-API-Key", credential_ref="pw"),
    )
    await RestCatalogueAdapter(config, secret="s3cret",
                               transport=httpx.MockTransport(handler)).fetch(uuid4())
    assert seen["key"] == "s3cret"


@pytest.mark.asyncio
async def test_bearer_auth_sends_an_authorization_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[{"id": "cam01"}])

    config = ConnectorConfig(
        catalogue_url="https://example/cameras.json",
        auth=AuthConfig(type="bearer", credential_ref="pw"),
    )
    await RestCatalogueAdapter(config, secret="tok",
                               transport=httpx.MockTransport(handler)).fetch(uuid4())
    assert seen["auth"] == "Bearer tok"


@pytest.mark.asyncio
async def test_a_nested_root_path_is_followed():
    payload = {"result": {"cameras": [{"id": "cam01"}]}}
    config = ConnectorConfig(
        catalogue_url="https://example/cameras.json", root_path="result.cameras"
    )
    adapter = RestCatalogueAdapter(
        config, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    )
    assert len(await adapter.fetch(uuid4())) == 1


@pytest.mark.asyncio
async def test_endpoint_rules_drive_protocols_and_reachability():
    """Nothing about hls/rtsp/whep is known to the adapter; it does what the row says."""
    config = ConnectorConfig(
        catalogue_url="https://example/cameras.json",
        endpoint_rules=[
            EndpointRule(protocol="onvif", url_template="http://cam/{id}/onvif",
                         reachability="lan_only", requires_auth=True,
                         credential_ref="onvif_pw", is_primary=True),
        ],
    )
    adapter = RestCatalogueAdapter(
        config,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[{"id": "c1"}])),
    )
    endpoints = adapter.endpoints_for({"id": "c1"})
    assert endpoints[0]["protocol"] == "onvif"
    assert endpoints[0]["reachability"] == "lan_only"
    assert endpoints[0]["url"] == "http://cam/c1/onvif"
```

- [ ] **Step 2: Run, confirm failure. Step 3: Implement `RestCatalogueAdapter`**

Key behaviours: build auth from `AuthConfig` + resolved secret; follow `root_path` (dotted, falling back to `cameras`/`items`/`data` for a bare object); resolve the id from `id_keys` in order; for each `EndpointRule`, prefer `url_key` from the entry, else format `url_template` with the id, skipping the rule if neither yields a URL. Strip every consumed `url_key` from the payload so URLs are not duplicated into `metadata`. Never template from a display name.

- [ ] **Steps 4–6: tests pass, delete the old adapter, commit.**

---

## Task 4: Connector CRUD and a code-free sync route

**Files:** Create `app/api/v1/routers/connectors.py`; Modify `app/api/v1/routers/onboarding.py`

- [ ] Replace `if adapter_code != SentinelAdapter.code` with a lookup: load the `SourceConnector` by code, 404 if absent or inactive, resolve its credential, build a `RestCatalogueAdapter`, ingest. No vendor name in code.
- [ ] `POST /api/v1/connectors` validates `config` through `ConnectorConfig` and returns 422 on a bad shape, so a malformed connector is rejected at write time rather than at sync time.
- [ ] `GET /api/v1/connectors` must **not** return resolved secrets — only `credential_ref`.
- [ ] Test: creating a connector via the API and syncing it onboards cameras, with no code change anywhere.

---

## Task 5: Place aliases as data

**Files:** Create `app/models/place_alias.py`, `seeds/place_aliases.py`; Modify `app/services/geocoding.py`

- [ ] `place_aliases`: `alias` (unique, normalised), `boundary_id` FK, `source` (how it was established), `confidence`.
- [ ] `DistrictGeocoder` loads aliases from the table instead of `PLACE_ALIASES`. Keep the longest-match-first rule.
- [ ] `seeds/place_aliases.py` seeds today's 42 entries, each carrying its `source` note — the seven looked-up ones keep their evidence.
- [ ] `POST /api/v1/boundaries/{id}/aliases` so a new place is a row.
- [ ] Test: an alias added at runtime is picked up without a restart; an unknown place still declines.

---

## Task 6: Seed and verify

- [ ] `seeds/connectors.py` inserts the sentinel connector row and its credential, reading the secret from `SENTINEL_PW` when set.
- [ ] Verify end to end against the live catalogue: 29 of 30 cameras onboarded, second sync creates 0.
- [ ] **The demo rehearsal:** create a *second* connector via the API against a locally served catalogue with a different shape, different auth scheme and different protocol names, and onboard it — proving a new vendor needs no code.

---

## Self-review

**Removes:** the `SentinelAdapter` class, the `adapter_code` conditional, `_PROTOCOL_KEYS`, `_ID_KEYS`/`_URL_ID_KEYS`, the four `sentinel_*` settings, and the 42-entry `PLACE_ALIASES` dict.

**Accepted corners:** credentials are stored in a table with env override rather than a secrets manager — documented, not built. `root_path` supports dotted paths but not full JSONPath. XML and SOAP catalogues are out of scope; the `SourceAdapter` protocol leaves room for a sibling implementation.
