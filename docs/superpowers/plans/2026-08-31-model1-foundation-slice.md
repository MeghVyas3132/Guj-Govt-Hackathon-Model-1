# Model 1 Foundation Slice — Implementation Plan (Plan 1 of 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship one complete vertical slice — publish the API contract, then onboard a camera through the shared ingestion pipeline from CSV, form, API and the live Sentinel sandbox, and see it on a MapLibre map served by PostGIS vector tiles.

**Architecture:** FastAPI with strict layering (routers → services → repositories). Every onboarding path constructs a `RawCameraRecord` and calls one `IngestionService.ingest()` function, so validation and normalization cannot diverge by source. Per-department `field_mappings` JSONB config translates foreign vocabularies to canonical enums, making new-department onboarding a config row rather than a code change. Camera geometry is `GEOGRAPHY(POINT,4326)` in PostGIS, served to the browser as Mapbox Vector Tiles via `ST_AsMVT`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + GeoAlchemy2, Alembic, PostgreSQL 16 + PostGIS 3.4, Redis + arq, Next.js 15 (App Router), MapLibre GL JS, pytest + pytest-asyncio + testcontainers.

**Reference spec:** `docs/superpowers/specs/2026-08-31-cctv-registry-gis-design.md`

---

## Roadmap — where this plan sits

| Plan | Scope | File |
|---|---|---|
| **1. Foundation slice** | Scaffold, contract, schema, ingestion pipeline, CSV + manual + API + Sentinel adapter, MVT tiles, map | **this document** |
| 2. GIS depth | Stream endpoint persistence, district boundaries, spatial search, filters, export, departments CRUD + adapter sync route, filter panel, PMTiles offline basemap | `2026-08-31-model1-gis-depth.md` |
| 3. Health monitoring | Health time series, status-transition semantics, batch push API, probe ladder, offline dashboard | `2026-08-31-model1-health-monitoring.md` |
| 4. Coverage & gap analysis | Directional footprint SQL, hexagon tessellation, installed vs effective coverage, report generation | `2026-08-31-model1-coverage-analysis.md` |
| 5. Auth, RBAC & audit | RS256 + JWKS, API keys, Principal resolution, query scoping, audit trail, login + admin | `2026-08-31-model1-auth-rbac-audit.md` |
| 6. Webhooks & delivery | Transactional outbox, HMAC delivery worker, 80k seed, five department fixtures, docs, demo rehearsal | `2026-08-31-model1-webhooks-seed-delivery.md` |

Execute in order — each plan assumes the previous ones are complete.

**Auth note:** Tasks in this plan run with a `Principal` stub that always returns a super-admin. Plan 5 replaces the stub with real resolution. Every repository method already takes `principal` so no signature churn happens later. This is deliberate, not an oversight.

---

## File structure

```
docker-compose.yml                     Postgres+PostGIS, Redis, api, worker, web
pyproject.toml
alembic.ini
alembic/versions/                      migrations
app/
  main.py                              FastAPI app factory, router registration
  core/
    config.py                          pydantic-settings
    db.py                              async engine + session dependency
    deps.py                            Principal dependency (stubbed in Plan 1)
    enums.py                           canonical enums, single source of truth
  models/
    base.py                            DeclarativeBase + timestamp mixin
    department.py  camera.py  field_mapping.py
    stream_endpoint.py  import_job.py
  schemas/
    camera.py                          CameraRead, CameraCreate, CameraUpdate
    ingestion.py                       RawCameraRecord, IngestReport, RowResult
    common.py                          Page, ErrorDetail
  services/
    normalization.py                   FieldMappingResolver
    validation.py                      CameraValidator
    ingestion.py                       IngestionService — the core
    tiles.py                           MVT SQL
  repositories/
    camera.py  department.py  import_job.py
  adapters/
    base.py                            SourceAdapter protocol
    csv_adapter.py
    sentinel_adapter.py
  api/v1/
    router.py                          aggregates all v1 routers
    routers/
      cameras.py  onboarding.py  tiles.py  departments.py
  workers/
    tasks.py                           arq task defs
tests/
  conftest.py                          testcontainers Postgres+PostGIS fixture
  services/  repositories/  api/  adapters/
web/
  app/map/page.tsx                     MapLibre map
  lib/api.ts
seeds/
  departments.py
```

---

## Task 1: Project scaffold and a live database

**Files:**
- Create: `pyproject.toml`, `docker-compose.yml`, `app/main.py`, `app/core/config.py`, `app/core/db.py`
- Test: `tests/test_health.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "sentinel-registry"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "sqlalchemy[asyncio]>=2.0.36",
  "asyncpg>=0.30",
  "geoalchemy2>=0.15",
  "alembic>=1.14",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "shapely>=2.0",
  "python-multipart>=0.0.12",
  "httpx>=0.27",
  "arq>=0.26",
  "openpyxl>=3.1",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "testcontainers[postgres]>=4.8", "ruff>=0.7"]

[tool.setuptools]
packages = ["app"]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  db:
    image: ${POSTGIS_IMAGE:-postgis/postgis:16-3.4}
    environment:
      POSTGRES_USER: sentinel
      POSTGRES_PASSWORD: sentinel
      POSTGRES_DB: sentinel
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sentinel"]
      interval: 5s
      retries: 10
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
volumes:
  pgdata:
```

- [ ] **Step 3: Create `app/core/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    redis_url: str = "redis://localhost:6379"
    api_v1_prefix: str = "/api/v1"
    gujarat_bbox: tuple[float, float, float, float] = (68.0, 20.0, 74.6, 24.8)


settings = Settings()
```

- [ ] **Step 4: Create `app/core/db.py`**

```python
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
```

- [ ] **Step 5: Write the failing test**

Create `tests/test_health.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_healthz_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 6: Run it to make sure it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 7: Create `app/main.py`**

```python
from fastapi import FastAPI

from app.core.config import settings


def create_app() -> FastAPI:
    application = FastAPI(
        title="Sentinel CCTV Registry",
        description="Model 1 — Centralised CCTV Registry & GIS Foundation.",
        version="1.0.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    @application.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
```

- [ ] **Step 8: Run the test and make sure it passes**

Run: `pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 9: Verify the database is up with PostGIS**

Run:
```bash
docker compose up -d db redis
docker compose exec db psql -U sentinel -d sentinel -c "CREATE EXTENSION IF NOT EXISTS postgis; SELECT postgis_version();"
```
Expected: a version string beginning `3.4`

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml docker-compose.yml app tests
git commit -m "feat: project scaffold with FastAPI, PostGIS and health check"
```

---

## Task 2: Canonical enums

Every other task depends on these. They are the vocabulary that `field_mappings` translates *into*.

**Files:**
- Create: `app/core/enums.py`
- Test: `tests/test_enums.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_enums.py`:

```python
from app.core.enums import CameraStatus, CameraType, OwnershipClass


def test_enums_are_lowercase_strings():
    assert CameraStatus.ONLINE.value == "online"
    assert CameraType.PTZ.value == "ptz"
    assert OwnershipClass.GOVERNMENT.value == "government"


def test_unknown_is_available_for_every_soft_enum():
    assert CameraStatus.UNKNOWN.value == "unknown"
    assert CameraType.OTHER.value == "other"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/test_enums.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.enums'`

- [ ] **Step 3: Create `app/core/enums.py`**

```python
from enum import StrEnum


class CameraStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"
    MAINTENANCE = "maintenance"


class CameraType(StrEnum):
    FIXED = "fixed"
    PTZ = "ptz"
    DOME = "dome"
    BULLET = "bullet"
    ANPR = "anpr"
    THERMAL = "thermal"
    OTHER = "other"


class CameraTechnology(StrEnum):
    ANALOG = "analog"
    IP = "ip"
    UNKNOWN = "unknown"


class Connectivity(StrEnum):
    FIBER = "fiber"
    FOUR_G = "4g"
    FIVE_G = "5g"
    WIFI = "wifi"
    LAN = "lan"
    UNKNOWN = "unknown"


class OwnershipClass(StrEnum):
    GOVERNMENT = "government"
    PRIVATE = "private"
    PPP = "ppp"


class SiteType(StrEnum):
    TRAFFIC_JUNCTION = "traffic_junction"
    GODOWN = "godown"
    PDS_SHOP = "pds_shop"
    RTO_CHECKPOINT = "rto_checkpoint"
    OFFICE = "office"
    HOSPITAL = "hospital"
    BUS_DEPOT = "bus_depot"
    BORDER_CHECKPOST = "border_checkpost"
    PUBLIC_SPACE = "public_space"
    OTHER = "other"


class StreamProtocol(StrEnum):
    RTSP = "rtsp"
    HLS = "hls"
    WHEP = "whep"
    ONVIF = "onvif"
    SNAPSHOT = "snapshot"


class Reachability(StrEnum):
    PUBLIC_CDN = "public_cdn"
    DIRECT_IP = "direct_ip"
    LAN_ONLY = "lan_only"


class SourceType(StrEnum):
    CSV = "csv"
    MANUAL = "manual"
    API = "api"
    ADAPTER = "adapter"


class LifecycleState(StrEnum):
    ACTIVE = "active"
    DECOMMISSIONED = "decommissioned"


# Which enums a field_mappings value_map may target, and their fallback member.
SOFT_ENUMS: dict[str, tuple[type[StrEnum], StrEnum]] = {
    "status": (CameraStatus, CameraStatus.UNKNOWN),
    "camera_type": (CameraType, CameraType.OTHER),
    "camera_technology": (CameraTechnology, CameraTechnology.UNKNOWN),
    "connectivity": (Connectivity, Connectivity.UNKNOWN),
    "ownership_class": (OwnershipClass, OwnershipClass.GOVERNMENT),
    "site_type": (SiteType, SiteType.OTHER),
}
```

- [ ] **Step 4: Run the test and make sure it passes**

Run: `pytest tests/test_enums.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/enums.py tests/test_enums.py
git commit -m "feat: canonical camera enums"
```

---

## Task 3: Publish the API contract — highest priority

Models 2–4 are being built in parallel. Every day they code against nothing is integration debt. This task ships stub routers returning realistic fixtures so the other devs get a working Swagger page and a mock server **before the database exists**.

**Files:**
- Create: `app/schemas/common.py`, `app/schemas/camera.py`, `app/api/v1/router.py`, `app/api/v1/routers/cameras.py`
- Modify: `app/main.py`
- Test: `tests/api/test_contract.py`

- [ ] **Step 1: Create `app/schemas/common.py`**

```python
from pydantic import BaseModel, Field


class Page[T](BaseModel):
    items: list[T]
    total: int = Field(description="Total rows matching the filter, ignoring pagination.")
    limit: int
    offset: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None
```

- [ ] **Step 2: Create `app/schemas/camera.py`**

```python
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import (
    CameraStatus,
    CameraTechnology,
    CameraType,
    Connectivity,
    LifecycleState,
    OwnershipClass,
    Reachability,
    SiteType,
    SourceType,
    StreamProtocol,
)


class StreamEndpointRead(BaseModel):
    """How Models 2-4 reach this camera. Credentials are omitted unless the caller
    holds the streams:credentials scope."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    protocol: StreamProtocol
    url: str = Field(examples=["rtsp://103.250.160.189:8554/stream/cam04"])
    codec: str | None = Field(default=None, examples=["h264"])
    resolution: str | None = Field(default=None, examples=["1920x1080"])
    is_primary: bool = True
    reachability: Reachability = Field(
        description="public_cdn works on any network; direct_ip needs gateway ports open."
    )
    requires_auth: bool = False
    verified_at: datetime | None = None


class CameraBase(BaseModel):
    external_camera_id: str = Field(examples=["cam04"])
    name: str | None = Field(default=None, examples=["Nehru Bridge East Approach"])
    latitude: float = Field(ge=-90, le=90, examples=[23.0225])
    longitude: float = Field(ge=-180, le=180, examples=[72.5714])
    address: str | None = None
    camera_type: CameraType = CameraType.FIXED
    camera_technology: CameraTechnology = CameraTechnology.IP
    azimuth_deg: float | None = Field(default=None, ge=0, lt=360, examples=[135.0])
    fov_deg: float | None = Field(default=None, gt=0, le=360, examples=[90.0])
    range_m: float | None = Field(default=None, gt=0, examples=[100.0])
    height_m: float | None = Field(default=None, gt=0)
    resolution: str | None = None
    has_night_vision: bool | None = None
    connectivity: Connectivity = Connectivity.UNKNOWN
    storage_type: str | None = Field(default=None, examples=["local"])
    retention_days: int | None = Field(default=None, ge=0, examples=[15])
    ownership_class: OwnershipClass = OwnershipClass.GOVERNMENT
    site_type: SiteType = SiteType.OTHER
    amc_vendor: str | None = None
    amc_expiry_date: date | None = None
    install_date: date | None = None
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Department-specific fields with no canonical home. Never dropped.",
    )


class CameraCreate(CameraBase):
    department_id: UUID
    operator_department_id: UUID | None = None


class CameraUpdate(BaseModel):
    name: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    camera_type: CameraType | None = None
    azimuth_deg: float | None = Field(default=None, ge=0, lt=360)
    fov_deg: float | None = Field(default=None, gt=0, le=360)
    range_m: float | None = Field(default=None, gt=0)
    connectivity: Connectivity | None = None
    site_type: SiteType | None = None
    metadata: dict[str, Any] | None = None


class CameraRead(CameraBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    camera_uid: str = Field(examples=["GJ-POL-000123"])
    department_id: UUID
    department_code: str | None = Field(default=None, examples=["POL"])
    operator_department_id: UUID | None = None
    district_id: UUID | None = None
    current_status: CameraStatus = CameraStatus.UNKNOWN
    status_since: datetime | None = None
    last_seen_at: datetime | None = None
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    source_type: SourceType
    stream_endpoints: list[StreamEndpointRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 3: Write the failing contract test**

Create `tests/api/test_contract.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_openapi_exposes_versioned_camera_routes(client):
    spec = (await client.get("/openapi.json")).json()
    assert "/api/v1/cameras" in spec["paths"]
    assert "/api/v1/cameras/{camera_id}/streams" in spec["paths"]


@pytest.mark.asyncio
async def test_list_cameras_returns_a_page(client):
    response = await client.get("/api/v1/cameras")
    assert response.status_code == 200
    body = response.json()
    assert {"items", "total", "limit", "offset"} <= body.keys()


@pytest.mark.asyncio
async def test_streams_endpoint_reports_reachability(client):
    response = await client.get(
        "/api/v1/cameras/00000000-0000-0000-0000-000000000001/streams"
    )
    assert response.status_code == 200
    protocols = {s["protocol"] for s in response.json()}
    assert {"rtsp", "hls", "whep"} <= protocols
    assert all("reachability" in s for s in response.json())
```

- [ ] **Step 4: Run it to make sure it fails**

Run: `pytest tests/api/test_contract.py -v`
Expected: FAIL — 404 on `/api/v1/cameras`

- [ ] **Step 5: Create the stub router `app/api/v1/routers/cameras.py`**

The `STUB_*` constants are removed in Task 8, Step 10. They exist so parallel teams have a mock server today.

```python
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query

from app.core.enums import (
    CameraStatus,
    CameraType,
    Reachability,
    SourceType,
    StreamProtocol,
)
from app.schemas.camera import CameraRead, StreamEndpointRead
from app.schemas.common import Page

router = APIRouter(prefix="/cameras", tags=["cameras"])

_STUB_ID = UUID("00000000-0000-0000-0000-000000000001")
_STUB_DEPT = UUID("00000000-0000-0000-0000-0000000000aa")
_NOW = datetime(2026, 9, 1, tzinfo=UTC)

_STUB_STREAMS = [
    StreamEndpointRead(
        id=UUID("00000000-0000-0000-0000-0000000000b1"),
        protocol=StreamProtocol.HLS,
        url="https://cctv.corp8.cloud/cam04/index.m3u8",
        codec="h264",
        resolution="1920x1080",
        is_primary=True,
        reachability=Reachability.PUBLIC_CDN,
        requires_auth=True,
    ),
    StreamEndpointRead(
        id=UUID("00000000-0000-0000-0000-0000000000b2"),
        protocol=StreamProtocol.RTSP,
        url="rtsp://103.250.160.189:8554/stream/cam04",
        codec="h264",
        resolution="1920x1080",
        is_primary=False,
        reachability=Reachability.DIRECT_IP,
        requires_auth=False,
    ),
    StreamEndpointRead(
        id=UUID("00000000-0000-0000-0000-0000000000b3"),
        protocol=StreamProtocol.WHEP,
        url="http://103.250.160.189:8889/stream/cam04/whep",
        codec="h264",
        resolution="1920x1080",
        is_primary=False,
        reachability=Reachability.DIRECT_IP,
        requires_auth=False,
    ),
]

_STUB_CAMERA = CameraRead(
    id=_STUB_ID,
    camera_uid="GJ-POL-000001",
    department_id=_STUB_DEPT,
    department_code="POL",
    external_camera_id="cam04",
    name="Nehru Bridge East Approach",
    latitude=23.0225,
    longitude=72.5714,
    camera_type=CameraType.FIXED,
    azimuth_deg=135.0,
    fov_deg=90.0,
    range_m=100.0,
    current_status=CameraStatus.ONLINE,
    status_since=_NOW,
    source_type=SourceType.ADAPTER,
    stream_endpoints=_STUB_STREAMS,
    created_at=_NOW,
    updated_at=_NOW,
)


@router.get("", response_model=Page[CameraRead], summary="List and filter cameras")
async def list_cameras(
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
) -> Page[CameraRead]:
    return Page(items=[_STUB_CAMERA], total=1, limit=limit, offset=offset)


@router.get("/{camera_id}", response_model=CameraRead)
async def get_camera(camera_id: UUID) -> CameraRead:
    return _STUB_CAMERA


@router.get(
    "/{camera_id}/streams",
    response_model=list[StreamEndpointRead],
    summary="Stream endpoints for this camera",
    description=(
        "Entry point for Models 2-4. Prefer the endpoint whose reachability matches "
        "your network: public_cdn works anywhere, direct_ip needs gateway ports open."
    ),
)
async def get_camera_streams(camera_id: UUID) -> list[StreamEndpointRead]:
    return _STUB_STREAMS
```

- [ ] **Step 6: Create `app/api/v1/router.py`**

```python
from fastapi import APIRouter

from app.api.v1.routers import cameras

api_router = APIRouter()
api_router.include_router(cameras.router)
```

- [ ] **Step 7: Register it in `app/main.py`**

Add these two lines inside `create_app()`, before `return application`:

```python
    from app.api.v1.router import api_router

    application.include_router(api_router, prefix=settings.api_v1_prefix)
```

- [ ] **Step 8: Run the tests and make sure they pass**

Run: `pytest tests/api/test_contract.py -v`
Expected: 3 passed

- [ ] **Step 9: Publish the contract to the other devs**

Run:
```bash
mkdir -p docs/api
uvicorn app.main:app --port 8000 &
until curl -sf localhost:8000/healthz >/dev/null; do sleep 0.3; done
curl -s localhost:8000/openapi.json > docs/api/openapi.json
kill %1
```

Send the team: the Swagger URL `http://<your-ip>:8000/docs`, the committed `docs/api/openapi.json`, and this note — *"`GET /api/v1/cameras/{id}/streams` is your entry point. Pick the endpoint whose `reachability` matches your network. Responses are stubbed until Task 6; the shape is final."*

- [ ] **Step 10: Commit**

```bash
mkdir -p docs/api
git add app/schemas app/api docs/api/openapi.json tests/api app/main.py
git commit -m "feat: publish v1 camera API contract with stub responses"
```

---

## Task 4: Database schema and migrations

**Files:**
- Create: `alembic.ini`, `alembic/env.py`, `app/models/base.py`, `app/models/department.py`, `app/models/field_mapping.py`, `app/models/camera.py`, `app/models/stream_endpoint.py`
- Test: `tests/conftest.py`, `tests/repositories/test_schema.py`

- [ ] **Step 1: Create `app/models/base.py`**

```python
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDMixin:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Create `app/models/department.py`**

```python
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Department(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "departments"

    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    dept_type: Mapped[str] = mapped_column(String(64), default="government")
    contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

- [ ] **Step 3: Create `app/models/field_mapping.py`**

```python
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class FieldMapping(Base, UUIDMixin, TimestampMixin):
    """Per-department translation config. Onboarding a new department is a row here,
    not a code change. Versioned so an import is reproducible."""

    __tablename__ = "field_mappings"
    __table_args__ = (UniqueConstraint("department_id", "version"),)

    department_id: Mapped[UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

- [ ] **Step 4: Create `app/models/camera.py`**

```python
from datetime import date, datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    CameraStatus, CameraTechnology, CameraType, Connectivity,
    LifecycleState, OwnershipClass, SiteType, SourceType,
)
from app.models.base import Base, TimestampMixin, UUIDMixin


class Camera(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "cameras"
    __table_args__ = (
        UniqueConstraint("department_id", "external_camera_id", name="uq_camera_dept_external"),
        Index("ix_cameras_location", "location", postgresql_using="gist"),
        Index("ix_cameras_status_since", "current_status", "status_since"),
        Index("ix_cameras_metadata", "metadata", postgresql_using="gin"),
    )

    camera_uid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    department_id: Mapped[UUID] = mapped_column(ForeignKey("departments.id"), index=True)
    operator_department_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    external_camera_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)

    location: Mapped[Any] = mapped_column(
        # spatial_index=False because __table_args__ declares the GIST index
        # explicitly; leaving the default True creates it twice.
        Geography(geometry_type="POINT", srid=4326, spatial_index=False)
    )
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    district_id: Mapped[UUID | None] = mapped_column(nullable=True)

    camera_type: Mapped[str] = mapped_column(String(32), default=CameraType.FIXED)
    camera_technology: Mapped[str] = mapped_column(String(16), default=CameraTechnology.IP)
    azimuth_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    fov_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    range_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    has_night_vision: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    connectivity: Mapped[str] = mapped_column(String(16), default=Connectivity.UNKNOWN)
    storage_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ownership_class: Mapped[str] = mapped_column(String(16), default=OwnershipClass.GOVERNMENT)
    site_type: Mapped[str] = mapped_column(String(32), default=SiteType.OTHER)
    amc_vendor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    amc_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    install_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    current_status: Mapped[str] = mapped_column(String(16), default=CameraStatus.UNKNOWN)
    status_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(24), default=LifecycleState.ACTIVE)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # `metadata` is reserved by SQLAlchemy's Declarative API, hence the trailing underscore.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)

    source_type: Mapped[str] = mapped_column(String(16), default=SourceType.MANUAL)
    field_mapping_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 5: Create `app/models/stream_endpoint.py`**

```python
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import Reachability
from app.models.base import Base, TimestampMixin, UUIDMixin


class StreamEndpoint(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "stream_endpoints"

    camera_id: Mapped[UUID] = mapped_column(
        ForeignKey("cameras.id", ondelete="CASCADE"), index=True
    )
    protocol: Mapped[str] = mapped_column(String(16))
    url: Mapped[str] = mapped_column(String(1000))
    codec: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    reachability: Mapped[str] = mapped_column(String(16), default=Reachability.DIRECT_IP)
    requires_auth: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probe_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
```

- [ ] **Step 6: Initialise Alembic and enable PostGIS in the first migration**

Run: `alembic init -t async alembic`

Then in `alembic/env.py`, replace the `target_metadata = None` line with:

```python
from app.core.config import settings
from app.models import camera, department, field_mapping, stream_endpoint  # noqa: F401
from app.models.base import Base

# alembic.ini ships a placeholder URL, so nothing runs until this is set.
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def include_object(obj, name, obj_type, reflected, compare_to) -> bool:
    """Keep autogenerate to our own tables.

    The PostGIS image installs postgis_topology and postgis_tiger_geocoder and puts
    the `topology` and `tiger` schemas on the search_path, so reflection sees ~40
    extension-owned tables. Without this filter every autogenerate run proposes
    dropping all of them. GeoAlchemy2's own include_object helper does not cover
    these -- it only filters spatial_ref_sys, geometry_columns and SpatiaLite names.
    """
    return not (
        obj_type == "table" and reflected and name not in target_metadata.tables
    )
```

Pass `include_object=include_object` to **both** `context.configure(...)` calls in
`run_migrations_offline()` and `do_run_migrations()`. This is load-bearing for every
later plan, not just this one.

- [ ] **Step 7: Generate the migration**

Run: `alembic revision --autogenerate -m "core registry schema"`

Then hand-edit the generated file so `upgrade()` starts with the extension and GeoAlchemy2's
spatial index is not double-created:

```python
def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    # ... autogenerated create_table calls follow
```

Alembic renders `geoalchemy2.types.Geography(...)` in the migration but emits no import
for it, so the file raises `NameError` when it runs. Add `import geoalchemy2` to the
migration, or once to `alembic/script.py.mako` so every future migration has it.

- [ ] **Step 8: Write the failing schema test**

Create `tests/conftest.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from app.models.base import Base


@pytest.fixture(scope="session")
def postgres_url() -> str:
    with PostgresContainer("postgis/postgis:16-3.4", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def session(postgres_url: str) -> AsyncSession:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()
```

Create `tests/repositories/test_schema.py`:

```python
import pytest
from geoalchemy2.shape import to_shape
from sqlalchemy import select

from app.models.camera import Camera
from app.models.department import Department


@pytest.mark.asyncio
async def test_camera_round_trips_with_geography(session):
    dept = Department(code="POL", name="Gujarat Police")
    session.add(dept)
    await session.flush()

    session.add(
        Camera(
            camera_uid="GJ-POL-000001",
            department_id=dept.id,
            external_camera_id="cam04",
            location="SRID=4326;POINT(72.5714 23.0225)",
            metadata_={"vendor_note": "installed under VISWAS phase II"},
        )
    )
    await session.commit()

    camera = (await session.execute(select(Camera))).scalar_one()
    point = to_shape(camera.location)
    assert round(point.x, 4) == 72.5714
    assert round(point.y, 4) == 23.0225
    assert camera.metadata_["vendor_note"].startswith("installed under")


@pytest.mark.asyncio
async def test_duplicate_external_id_in_same_department_is_rejected(session):
    from sqlalchemy.exc import IntegrityError

    dept = Department(code="AMC", name="Ahmedabad Municipal Corporation")
    session.add(dept)
    await session.flush()

    for uid in ("GJ-AMC-000001", "GJ-AMC-000002"):
        session.add(
            Camera(
                camera_uid=uid,
                department_id=dept.id,
                external_camera_id="DUP-1",
                location="SRID=4326;POINT(72.5 23.0)",
            )
        )

    with pytest.raises(IntegrityError):
        await session.commit()
```

- [ ] **Step 9: Run the tests**

Run: `pytest tests/repositories/test_schema.py -v`
Expected: 2 passed. The uniqueness test is what makes idempotent ingestion possible in Task 8.

- [ ] **Step 10: Commit**

```bash
git add alembic alembic.ini app/models tests/conftest.py tests/repositories
git commit -m "feat: core registry schema with PostGIS geography and dedupe constraint"
```

---

## Task 5: FieldMappingResolver — the "anything" mechanism

**Files:**
- Create: `app/services/normalization.py`
- Test: `tests/services/test_normalization.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_normalization.py`:

```python
from app.core.enums import CameraStatus, CameraType
from app.services.normalization import FieldMappingResolver

AMC_CONFIG = {
    "column_map": {
        "cam_id": "external_camera_id",
        "lat": "latitude",
        "lng": "longitude",
        "cam_kind": "camera_type",
        "state": "status",
    },
    "value_maps": {
        "status": {"ACTIVE": "online", "DOWN": "offline", "AMC": "maintenance"},
        "camera_type": {"PTZ-DOME": "ptz", "BULLET": "fixed"},
    },
    "defaults": {"connectivity": "fiber"},
    "passthrough_to_metadata": True,
}


def test_renames_columns_to_canonical_names():
    result = FieldMappingResolver(AMC_CONFIG).resolve(
        {"cam_id": "A-1", "lat": "23.02", "lng": "72.57"}
    )
    assert result.values["external_camera_id"] == "A-1"
    assert result.values["latitude"] == "23.02"


def test_translates_department_vocabulary_to_canonical_enums():
    result = FieldMappingResolver(AMC_CONFIG).resolve(
        {"cam_id": "A-1", "state": "ACTIVE", "cam_kind": "PTZ-DOME"}
    )
    assert result.values["status"] == CameraStatus.ONLINE
    assert result.values["camera_type"] == CameraType.PTZ


def test_unmapped_value_falls_back_and_warns_but_does_not_fail():
    result = FieldMappingResolver(AMC_CONFIG).resolve({"cam_id": "A-1", "state": "FLAKY"})
    assert result.values["status"] == CameraStatus.UNKNOWN
    assert any("FLAKY" in w for w in result.warnings)


def test_unmapped_columns_are_preserved_in_metadata_not_dropped():
    result = FieldMappingResolver(AMC_CONFIG).resolve(
        {"cam_id": "A-1", "pole_number": "P-77", "ward_engineer": "R. Patel"}
    )
    assert result.metadata == {"pole_number": "P-77", "ward_engineer": "R. Patel"}


def test_defaults_apply_only_when_field_absent():
    resolver = FieldMappingResolver(AMC_CONFIG)
    assert resolver.resolve({"cam_id": "A-1"}).values["connectivity"] == "fiber"
    supplied = resolver.resolve({"cam_id": "A-1", "connectivity": "4g"})
    assert supplied.values["connectivity"] == "4g"


def test_passthrough_disabled_drops_unmapped_columns():
    config = {**AMC_CONFIG, "passthrough_to_metadata": False}
    result = FieldMappingResolver(config).resolve({"cam_id": "A-1", "pole_number": "P-77"})
    assert result.metadata == {}


def test_dms_coordinates_are_converted_to_decimal_degrees():
    config = {**AMC_CONFIG, "coordinate_format": "dms"}
    result = FieldMappingResolver(config).resolve(
        {"cam_id": "A-1", "lat": "23 01 21.0 N", "lng": "72 34 17.0 E"}
    )
    assert round(result.values["latitude"], 4) == 23.0225
    assert round(result.values["longitude"], 4) == 72.5714
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/services/test_normalization.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.normalization'`

- [ ] **Step 3: Create `app/services/normalization.py`**

```python
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.enums import SOFT_ENUMS

# The separator before the hemisphere must exclude NSEW, or a greedy class swallows
# the letter and every southern/western coordinate silently comes back positive.
_DMS = re.compile(
    r"^\s*(\d+)[^\d]+(\d+)[^\d]+([\d.]+)[^\dNSEWnsew]*([NSEW])?\s*$", re.IGNORECASE
)


def _parse_dms(value: str) -> float:
    match = _DMS.match(value)
    if not match:
        return float(value)
    degrees, minutes, seconds, hemisphere = match.groups()
    decimal = int(degrees) + int(minutes) / 60 + float(seconds) / 3600
    if hemisphere and hemisphere.upper() in {"S", "W"}:
        decimal = -decimal
    return decimal


@dataclass
class ResolveResult:
    values: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class FieldMappingResolver:
    """Translates one department's field names and vocabulary into canonical form.

    Two rules make this safe to run unattended against a nightly departmental sync:
    unmapped columns are preserved in metadata rather than dropped, and unmapped
    values fall back to the enum's neutral member with a warning rather than raising.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.column_map: dict[str, str] = config.get("column_map", {})
        self.value_maps: dict[str, dict[str, str]] = config.get("value_maps", {})
        self.defaults: dict[str, Any] = config.get("defaults", {})
        self.coordinate_format: str = config.get("coordinate_format", "decimal_degrees")
        self.passthrough: bool = config.get("passthrough_to_metadata", True)

    def resolve(self, raw: dict[str, Any]) -> ResolveResult:
        result = ResolveResult()

        for source_key, value in raw.items():
            target = self.column_map.get(source_key)
            if target is None:
                # An already-canonical key passes through untouched; anything else
                # is department-specific and belongs in metadata.
                if source_key in SOFT_ENUMS or source_key in _KNOWN_CANONICAL:
                    result.values[source_key] = value
                elif self.passthrough:
                    result.metadata[source_key] = value
                continue
            result.values[target] = value

        for field_name, mapping in self.value_maps.items():
            if field_name not in result.values:
                continue
            raw_value = result.values[field_name]
            if raw_value is None:
                continue
            key = str(raw_value).strip()
            enum_cls, fallback = SOFT_ENUMS[field_name]
            mapped = mapping.get(key, mapping.get(key.upper()))
            if mapped is not None:
                result.values[field_name] = enum_cls(mapped)
                continue
            try:
                result.values[field_name] = enum_cls(key.lower())
            except ValueError:
                result.values[field_name] = fallback
                result.warnings.append(
                    f"Unmapped value {key!r} for field {field_name!r}; "
                    f"defaulted to {fallback.value!r}."
                )

        for field_name, default in self.defaults.items():
            result.values.setdefault(field_name, default)

        if self.coordinate_format == "dms":
            for coord in ("latitude", "longitude"):
                raw_coord = result.values.get(coord)
                if not isinstance(raw_coord, str):
                    continue
                try:
                    result.values[coord] = _parse_dms(raw_coord)
                except ValueError:
                    # Leave the raw value in place so CameraValidator reports it as an
                    # invalid_coordinate row error. One unparseable cell must not raise
                    # out of resolve() and abort the whole import batch.
                    result.warnings.append(
                        f"Could not parse {coord} {raw_coord!r} as "
                        "degrees-minutes-seconds; left for validation."
                    )

        return result


_KNOWN_CANONICAL = {
    "external_camera_id", "name", "latitude", "longitude", "address",
    "azimuth_deg", "fov_deg", "range_m", "height_m", "resolution",
    "has_night_vision", "storage_type", "retention_days", "amc_vendor",
    "amc_expiry_date", "install_date",
}
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `pytest tests/services/test_normalization.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/normalization.py tests/services/test_normalization.py
git commit -m "feat: field mapping resolver translating department vocabularies"
```

---

## Task 6: CameraValidator

**Files:**
- Create: `app/services/validation.py`
- Test: `tests/services/test_validation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_validation.py`:

```python
from app.services.validation import CameraValidator


def valid_row() -> dict:
    return {"external_camera_id": "A-1", "latitude": 23.0225, "longitude": 72.5714}


def test_accepts_a_well_formed_row():
    result = CameraValidator().validate(valid_row())
    assert result.is_valid
    assert result.errors == []


def test_rejects_missing_external_camera_id():
    row = valid_row()
    del row["external_camera_id"]
    result = CameraValidator().validate(row)
    assert not result.is_valid
    assert any(e.field == "external_camera_id" for e in result.errors)


def test_rejects_unparseable_coordinates():
    row = valid_row() | {"latitude": "not-a-number"}
    result = CameraValidator().validate(row)
    assert not result.is_valid
    assert any(e.code == "invalid_coordinate" for e in result.errors)


def test_rejects_coordinates_outside_gujarat():
    row = valid_row() | {"latitude": 28.6139, "longitude": 77.2090}  # New Delhi
    result = CameraValidator().validate(row)
    assert not result.is_valid
    assert any(e.code == "outside_gujarat" for e in result.errors)


def test_rejects_out_of_range_azimuth():
    result = CameraValidator().validate(valid_row() | {"azimuth_deg": 400})
    assert not result.is_valid
    assert any(e.field == "azimuth_deg" for e in result.errors)


def test_accepts_boundary_azimuth_values():
    assert CameraValidator().validate(valid_row() | {"azimuth_deg": 0}).is_valid
    assert CameraValidator().validate(valid_row() | {"azimuth_deg": 359.9}).is_valid
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/services/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.validation'`

- [ ] **Step 3: Create `app/services/validation.py`**

```python
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.schemas.common import ErrorDetail


@dataclass
class ValidationResult:
    values: dict[str, Any] = field(default_factory=dict)
    errors: list[ErrorDetail] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


class CameraValidator:
    REQUIRED = ("external_camera_id", "latitude", "longitude")

    def validate(self, values: dict[str, Any]) -> ValidationResult:
        result = ValidationResult(values=dict(values))

        for name in self.REQUIRED:
            if values.get(name) in (None, ""):
                result.errors.append(
                    ErrorDetail(
                        code="missing_required_field",
                        message=f"{name} is required.",
                        field=name,
                    )
                )

        coords: dict[str, float] = {}
        for name, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
            raw = values.get(name)
            if raw in (None, ""):
                continue
            try:
                coords[name] = float(raw)
            except (TypeError, ValueError):
                result.errors.append(
                    ErrorDetail(
                        code="invalid_coordinate",
                        message=f"{name} {raw!r} is not a number.",
                        field=name,
                    )
                )
                continue
            if not low <= coords[name] <= high:
                result.errors.append(
                    ErrorDetail(
                        code="invalid_coordinate",
                        message=f"{name} {coords[name]} is out of range.",
                        field=name,
                    )
                )

        if {"latitude", "longitude"} <= coords.keys():
            min_lon, min_lat, max_lon, max_lat = settings.gujarat_bbox
            inside = (
                min_lat <= coords["latitude"] <= max_lat
                and min_lon <= coords["longitude"] <= max_lon
            )
            if not inside:
                result.errors.append(
                    ErrorDetail(
                        code="outside_gujarat",
                        message=(
                            f"Point ({coords['latitude']}, {coords['longitude']}) falls "
                            "outside the Gujarat bounding box."
                        ),
                        field="location",
                    )
                )
            else:
                result.values["latitude"] = coords["latitude"]
                result.values["longitude"] = coords["longitude"]

        for name, low, high, inclusive_high in (
            ("azimuth_deg", 0, 360, False),
            ("fov_deg", 0, 360, True),
            ("range_m", 0, 100_000, True),
            ("height_m", 0, 200, True),
        ):
            raw = values.get(name)
            if raw in (None, ""):
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError):
                result.errors.append(
                    ErrorDetail(
                        code="invalid_number", message=f"{name} {raw!r} is not a number.",
                        field=name,
                    )
                )
                continue
            upper_ok = number <= high if inclusive_high else number < high
            lower_ok = number >= low if name == "azimuth_deg" else number > low
            if not (lower_ok and upper_ok):
                result.errors.append(
                    ErrorDetail(
                        code="out_of_range",
                        message=f"{name} {number} is out of range.",
                        field=name,
                    )
                )
            else:
                result.values[name] = number

        return result
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `pytest tests/services/test_validation.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/validation.py tests/services/test_validation.py
git commit -m "feat: camera validator with Gujarat bbox and optics range checks"
```

---

## Task 7: IngestionService — one pipeline for every source

**Files:**
- Create: `app/schemas/ingestion.py`, `app/repositories/camera.py`, `app/services/ingestion.py`
- Test: `tests/services/test_ingestion.py`

- [ ] **Step 1: Create `app/schemas/ingestion.py`**

```python
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.core.enums import SourceType
from app.schemas.common import ErrorDetail


@dataclass
class RawCameraRecord:
    """The single entry shape for every onboarding path."""

    payload: dict[str, Any]
    department_id: UUID
    source_type: SourceType
    source_ref: str | None = None
    row_number: int | None = None


class RowResult(BaseModel):
    row_number: int | None = None
    external_camera_id: str | None = None
    outcome: str  # created | updated | skipped | failed
    errors: list[ErrorDetail] = []
    warnings: list[str] = []


class IngestReport(BaseModel):
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    rows: list[RowResult] = []

    @property
    def is_dry_run_clean(self) -> bool:
        return self.failed == 0
```

- [ ] **Step 2: Write the failing test**

Create `tests/services/test_ingestion.py`:

```python
import pytest
from sqlalchemy import select

from app.core.enums import SourceType
from app.models.camera import Camera
from app.models.department import Department
from app.models.field_mapping import FieldMapping
from app.schemas.ingestion import RawCameraRecord
from app.services.ingestion import IngestionService


@pytest.fixture
async def department(session) -> Department:
    dept = Department(code="AMC", name="Ahmedabad Municipal Corporation")
    session.add(dept)
    await session.flush()
    session.add(
        FieldMapping(
            department_id=dept.id,
            version=1,
            config={
                "column_map": {"cam_id": "external_camera_id", "lat": "latitude",
                               "lng": "longitude", "state": "status"},
                "value_maps": {"status": {"ACTIVE": "online", "DOWN": "offline"}},
                "passthrough_to_metadata": True,
            },
        )
    )
    await session.commit()
    return dept


def record(dept, **overrides) -> RawCameraRecord:
    payload = {"cam_id": "A-1", "lat": "23.0225", "lng": "72.5714", "state": "ACTIVE"}
    payload.update(overrides)
    return RawCameraRecord(
        payload=payload, department_id=dept.id, source_type=SourceType.CSV
    )


@pytest.mark.asyncio
async def test_validate_only_does_not_write(session, department):
    service = IngestionService(session)
    report = await service.ingest([record(department)], department, mode="validate_only")
    assert report.total == 1
    assert report.failed == 0
    assert (await session.execute(select(Camera))).first() is None


@pytest.mark.asyncio
async def test_commit_creates_a_camera(session, department):
    service = IngestionService(session)
    report = await service.ingest([record(department)], department, mode="commit")
    assert report.created == 1
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.external_camera_id == "A-1"
    assert camera.current_status == "online"
    assert camera.camera_uid.startswith("GJ-AMC-")


@pytest.mark.asyncio
async def test_reimporting_identical_data_is_idempotent(session, department):
    service = IngestionService(session)
    await service.ingest([record(department)], department, mode="commit")
    second = await service.ingest([record(department)], department, mode="commit")
    assert second.created == 0
    assert second.skipped == 1
    assert len((await session.execute(select(Camera))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_changed_field_produces_an_update_not_a_duplicate(session, department):
    service = IngestionService(session)
    await service.ingest([record(department)], department, mode="commit")
    second = await service.ingest(
        [record(department, state="DOWN")], department, mode="commit"
    )
    assert second.updated == 1
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.current_status == "offline"


@pytest.mark.asyncio
async def test_invalid_row_fails_without_blocking_valid_rows(session, department):
    service = IngestionService(session)
    report = await service.ingest(
        [record(department, cam_id="A-1"), record(department, cam_id="A-2", lat="99.9")],
        department,
        mode="commit",
    )
    assert report.created == 1
    assert report.failed == 1
    assert len((await session.execute(select(Camera))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_unmapped_columns_land_in_metadata(session, department):
    service = IngestionService(session)
    await service.ingest(
        [record(department, pole_number="P-77")], department, mode="commit"
    )
    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.metadata_["pole_number"] == "P-77"
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `pytest tests/services/test_ingestion.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ingestion'`

- [ ] **Step 4: Create `app/repositories/camera.py`**

```python
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera


class CameraRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_external_id(
        self, department_id: UUID, external_camera_id: str
    ) -> Camera | None:
        stmt = select(Camera).where(
            Camera.department_id == department_id,
            Camera.external_camera_id == external_camera_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def next_uid(self, department_code: str) -> str:
        stmt = select(func.count()).select_from(Camera).where(
            Camera.camera_uid.like(f"GJ-{department_code}-%")
        )
        count = (await self.session.execute(stmt)).scalar_one()
        return f"GJ-{department_code}-{count + 1:06d}"

    def add(self, camera: Camera) -> None:
        self.session.add(camera)
```

- [ ] **Step 5: Create `app/services/ingestion.py`**

```python
from datetime import UTC, datetime
from typing import Any, Literal

from geoalchemy2.elements import WKBElement, WKTElement
from geoalchemy2.shape import to_shape
from shapely import wkt as shapely_wkt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.camera import Camera
from app.models.department import Department
from app.models.field_mapping import FieldMapping
from app.repositories.camera import CameraRepository
from app.schemas.ingestion import IngestReport, RawCameraRecord, RowResult
from app.services.normalization import FieldMappingResolver
from app.services.validation import CameraValidator

# Columns the persister is allowed to write from a normalized draft.
_WRITABLE = {
    "name", "address", "camera_type", "camera_technology", "azimuth_deg", "fov_deg",
    "range_m", "height_m", "resolution", "has_night_vision", "connectivity",
    "storage_type", "retention_days", "ownership_class", "site_type", "amc_vendor",
    "amc_expiry_date", "install_date",
}


# ~1.1 cm at the equator — finer than any coordinate a department can supply, coarse
# enough to absorb float noise from the PostGIS round trip.
_COORD_PRECISION = 7


def _point_moved(stored: Any, longitude: float, latitude: float) -> bool:
    """Has the camera actually moved?

    `Camera.location` is written as `"SRID=4326;POINT(lon lat)"` but comes back from
    PostGIS as a GeoAlchemy2 `WKBElement` whose `str()` is hex WKB — GeoAlchemy2 expires
    geography attributes on flush, so even the row just inserted in this session reloads
    that way. Comparing string forms would therefore always differ, reporting every
    re-import as `updated` and silently breaking the idempotency guarantee. Compare
    decoded coordinates instead.
    """
    if stored is None:
        return True
    if isinstance(stored, WKBElement | WKTElement):
        point = to_shape(stored)
    elif isinstance(stored, str):
        # Assigned in this session and not yet flushed: "SRID=4326;POINT(lon lat)".
        try:
            point = shapely_wkt.loads(stored.split(";", 1)[-1])
        except Exception:
            return True
    else:
        return True
    return (round(point.x, _COORD_PRECISION), round(point.y, _COORD_PRECISION)) != (
        round(longitude, _COORD_PRECISION),
        round(latitude, _COORD_PRECISION),
    )


class IngestionService:
    """The one function every onboarding path calls.

    CSV upload, manual form, REST POST and adapter pulls all build RawCameraRecord
    and land here, so validation and normalization cannot drift apart by source.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.cameras = CameraRepository(session)
        self.validator = CameraValidator()

    async def _resolver(self, department: Department) -> tuple[FieldMappingResolver, int]:
        stmt = (
            select(FieldMapping)
            .where(FieldMapping.department_id == department.id, FieldMapping.is_active)
            .order_by(FieldMapping.version.desc())
        )
        mapping = (await self.session.execute(stmt)).scalars().first()
        if mapping is None:
            return FieldMappingResolver({}), 0
        return FieldMappingResolver(mapping.config), mapping.version

    async def ingest(
        self,
        records: list[RawCameraRecord],
        department: Department,
        mode: Literal["validate_only", "commit"],
    ) -> IngestReport:
        resolver, mapping_version = await self._resolver(department)
        report = IngestReport(total=len(records))

        for record in records:
            resolved = resolver.resolve(record.payload)
            validated = self.validator.validate(resolved.values)
            warnings = resolved.warnings + validated.warnings
            external_id = resolved.values.get("external_camera_id")

            if not validated.is_valid:
                report.failed += 1
                report.rows.append(
                    RowResult(
                        row_number=record.row_number,
                        external_camera_id=external_id,
                        outcome="failed",
                        errors=validated.errors,
                        warnings=warnings,
                    )
                )
                continue

            if mode == "validate_only":
                existing = await self.cameras.get_by_external_id(
                    department.id, str(external_id)
                )
                outcome = "updated" if existing else "created"
                setattr(report, outcome, getattr(report, outcome) + 1)
                report.rows.append(
                    RowResult(
                        row_number=record.row_number,
                        external_camera_id=external_id,
                        outcome=outcome,
                        warnings=warnings,
                    )
                )
                continue

            outcome = await self._persist(
                validated.values, resolved.metadata, record, department, mapping_version
            )
            setattr(report, outcome, getattr(report, outcome) + 1)
            report.rows.append(
                RowResult(
                    row_number=record.row_number,
                    external_camera_id=external_id,
                    outcome=outcome,
                    warnings=warnings,
                )
            )

        if mode == "commit":
            await self.session.commit()
        return report

    async def _persist(
        self,
        values: dict[str, Any],
        metadata: dict[str, Any],
        record: RawCameraRecord,
        department: Department,
        mapping_version: int,
    ) -> str:
        external_id = str(values["external_camera_id"])
        wkt = f"SRID=4326;POINT({values['longitude']} {values['latitude']})"
        status = values.get("status")

        camera = await self.cameras.get_by_external_id(department.id, external_id)
        if camera is None:
            camera = Camera(
                camera_uid=await self.cameras.next_uid(department.code),
                department_id=department.id,
                external_camera_id=external_id,
                location=wkt,
                metadata_=metadata,
                source_type=record.source_type,
                field_mapping_version=mapping_version,
            )
            if status is not None:
                camera.current_status = str(status)
                camera.status_since = datetime.now(UTC)
            for key in _WRITABLE & values.keys():
                setattr(camera, key, values[key])
            self.cameras.add(camera)
            await self.session.flush()
            return "created"

        changed = False
        if _point_moved(
            camera.location, float(values["longitude"]), float(values["latitude"])
        ):
            camera.location = wkt
            changed = True
        for key in _WRITABLE & values.keys():
            if getattr(camera, key) != values[key]:
                setattr(camera, key, values[key])
                changed = True
        if metadata and camera.metadata_ != {**camera.metadata_, **metadata}:
            camera.metadata_ = {**camera.metadata_, **metadata}
            changed = True
        if status is not None and camera.current_status != str(status):
            camera.current_status = str(status)
            camera.status_since = datetime.now(UTC)
            changed = True

        if changed:
            await self.session.flush()
            return "updated"
        return "skipped"
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `pytest tests/services/test_ingestion.py -v`
Expected: 7 passed

`_point_moved` exists because `camera.location` reads back as a GeoAlchemy2 `WKBElement`,
not the `SRID=4326;POINT(...)` string that was written — GeoAlchemy2 expires geography
attributes on flush, so even a row inserted in this same session reloads as WKB. A naive
`str(camera.location) != wkt` is therefore unconditionally true, every re-import reports
`updated`, and idempotency is silently lost.

The idempotency test is the one that matters most — it is what makes a nightly departmental
sync safe to re-run, and it is worth demonstrating live to the judges.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/ingestion.py app/repositories/camera.py app/services/ingestion.py tests/services/test_ingestion.py
git commit -m "feat: idempotent ingestion pipeline shared by every onboarding path"
```

---

## Task 8: CSV adapter and the validate/preview/import wizard API

**Files:**
- Create: `app/adapters/base.py`, `app/adapters/csv_adapter.py`, `app/api/v1/routers/onboarding.py`
- Modify: `app/api/v1/router.py`
- Test: `tests/adapters/test_csv_adapter.py`, `tests/api/test_onboarding.py`

- [ ] **Step 1: Create `app/adapters/base.py`**

```python
from typing import Protocol
from uuid import UUID

from app.schemas.ingestion import RawCameraRecord


class SourceAdapter(Protocol):
    """Turns some external representation into RawCameraRecords.

    Adapters never validate or normalize — that is IngestionService's job. This keeps
    every source funnelling through identical rules.
    """

    code: str

    async def fetch(self, department_id: UUID) -> list[RawCameraRecord]: ...
```

- [ ] **Step 2: Write the failing test**

Create `tests/adapters/test_csv_adapter.py`:

```python
from uuid import uuid4

from app.adapters.csv_adapter import CsvAdapter
from app.core.enums import SourceType


def test_parses_rows_with_one_based_row_numbers():
    csv_bytes = b"cam_id,lat,lng\nA-1,23.02,72.57\nA-2,22.30,73.19\n"
    dept_id = uuid4()

    records = CsvAdapter(csv_bytes, filename="amc.csv").parse(dept_id)

    assert len(records) == 2
    assert records[0].payload == {"cam_id": "A-1", "lat": "23.02", "lng": "72.57"}
    assert records[0].row_number == 2  # header is row 1
    assert records[0].source_type == SourceType.CSV
    assert records[0].source_ref == "amc.csv"


def test_strips_whitespace_from_headers_and_values():
    csv_bytes = b" cam_id , lat \n A-1 , 23.02 \n"
    records = CsvAdapter(csv_bytes, filename="x.csv").parse(uuid4())
    assert records[0].payload == {"cam_id": "A-1", "lat": "23.02"}


def test_skips_fully_blank_rows():
    csv_bytes = b"cam_id,lat\nA-1,23.02\n,\n"
    records = CsvAdapter(csv_bytes, filename="x.csv").parse(uuid4())
    assert len(records) == 1
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `pytest tests/adapters/test_csv_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.csv_adapter'`

- [ ] **Step 4: Create `app/adapters/csv_adapter.py`**

```python
import csv
import io
from uuid import UUID

from app.core.enums import SourceType
from app.schemas.ingestion import RawCameraRecord


class CsvAdapter:
    code = "csv"

    def __init__(self, content: bytes, filename: str) -> None:
        self.content = content
        self.filename = filename

    def parse(self, department_id: UUID) -> list[RawCameraRecord]:
        text = self.content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        records: list[RawCameraRecord] = []

        for offset, row in enumerate(reader, start=2):
            payload = {
                (key or "").strip(): (value or "").strip()
                for key, value in row.items()
                if key is not None
            }
            if not any(payload.values()):
                continue
            records.append(
                RawCameraRecord(
                    payload=payload,
                    department_id=department_id,
                    source_type=SourceType.CSV,
                    source_ref=self.filename,
                    row_number=offset,
                )
            )
        return records
```

- [ ] **Step 5: Run the adapter tests**

Run: `pytest tests/adapters/test_csv_adapter.py -v`
Expected: 3 passed

- [ ] **Step 6: Write the failing wizard API test**

Create `tests/api/test_onboarding.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_preview_reports_row_errors_without_writing(api_client, seeded_department):
    csv_bytes = b"cam_id,lat,lng\nA-1,23.02,72.57\nA-2,99.9,72.57\n"
    response = await api_client.post(
        f"/api/v1/onboarding/preview?department_id={seeded_department}",
        files={"file": ("amc.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["failed"] == 1
    assert body["rows"][1]["errors"][0]["code"] == "outside_gujarat"

    listing = await api_client.get("/api/v1/cameras")
    assert listing.json()["total"] == 0


@pytest.mark.asyncio
async def test_import_commits_valid_rows(api_client, seeded_department):
    csv_bytes = b"cam_id,lat,lng\nA-1,23.02,72.57\n"
    response = await api_client.post(
        f"/api/v1/onboarding/import?department_id={seeded_department}",
        files={"file": ("amc.csv", csv_bytes, "text/csv")},
    )
    assert response.json()["created"] == 1

    listing = await api_client.get("/api/v1/cameras")
    assert listing.json()["total"] == 1
```

Add these fixtures to `tests/conftest.py`:

```python
@pytest.fixture
async def api_client(session):
    from httpx import ASGITransport, AsyncClient

    from app.core.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def seeded_department(session):
    from app.models.department import Department
    from app.models.field_mapping import FieldMapping

    dept = Department(code="AMC", name="Ahmedabad Municipal Corporation")
    session.add(dept)
    await session.flush()
    session.add(
        FieldMapping(
            department_id=dept.id,
            version=1,
            config={
                "column_map": {
                    "cam_id": "external_camera_id",
                    "lat": "latitude",
                    "lng": "longitude",
                }
            },
        )
    )
    await session.commit()
    return dept.id
```

- [ ] **Step 7: Run it to make sure it fails**

Run: `pytest tests/api/test_onboarding.py -v`
Expected: FAIL — 404 on `/api/v1/onboarding/preview`

- [ ] **Step 8: Create `app/api/v1/routers/onboarding.py`**

```python
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.adapters.csv_adapter import CsvAdapter
from app.models.department import Department
from app.schemas.ingestion import IngestReport, RawCameraRecord
from app.core.enums import SourceType
from app.schemas.camera import CameraCreate
from app.services.ingestion import IngestionService

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


async def _department(session: AsyncSession, department_id: UUID) -> Department:
    department = await session.get(Department, department_id)
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")
    return department


async def _run_csv(
    session: AsyncSession, department_id: UUID, file: UploadFile, mode: str
) -> IngestReport:
    department = await _department(session, department_id)
    records = CsvAdapter(await file.read(), file.filename or "upload.csv").parse(
        department_id
    )
    return await IngestionService(session).ingest(records, department, mode=mode)


@router.post(
    "/preview",
    response_model=IngestReport,
    summary="Validate a file and report row-level results without writing",
)
async def preview(
    department_id: UUID = Query(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    return await _run_csv(session, department_id, file, "validate_only")


@router.post("/import", response_model=IngestReport, summary="Commit a validated file")
async def import_file(
    department_id: UUID = Query(...),
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    return await _run_csv(session, department_id, file, "commit")


@router.post(
    "/bulk",
    response_model=IngestReport,
    summary="API onboarding for departmental systems",
    description="Same validation and normalization as CSV upload — only the source differs.",
)
async def bulk(
    payload: list[CameraCreate],
    department_id: UUID = Query(...),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    department = await _department(session, department_id)
    records = [
        RawCameraRecord(
            payload=item.model_dump(mode="json", exclude_none=True),
            department_id=department_id,
            source_type=SourceType.API,
        )
        for item in payload
    ]
    return await IngestionService(session).ingest(records, department, mode="commit")
```

- [ ] **Step 9: Register the router**

In `app/api/v1/router.py`, add:

```python
from app.api.v1.routers import cameras, onboarding

api_router.include_router(onboarding.router)
```

- [ ] **Step 10: Replace the stub camera list with a real query**

In `app/api/v1/routers/cameras.py`, delete the `_STUB_CAMERA` constant and replace
`list_cameras` with:

```python
@router.get("", response_model=Page[CameraRead], summary="List and filter cameras")
async def list_cameras(
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[CameraRead]:
    from geoalchemy2.shape import to_shape
    from sqlalchemy import func, select

    from app.models.camera import Camera

    total = (
        await session.execute(select(func.count()).select_from(Camera))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(Camera).order_by(Camera.camera_uid).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    items = []
    for row in rows:
        point = to_shape(row.location)
        items.append(
            CameraRead.model_validate(
                {
                    **row.__dict__,
                    "latitude": point.y,
                    "longitude": point.x,
                    "metadata": row.metadata_,
                    "stream_endpoints": [],
                }
            )
        )
    return Page(items=items, total=total, limit=limit, offset=offset)
```

Add the imports `from fastapi import Depends` and
`from sqlalchemy.ext.asyncio import AsyncSession` and `from app.core.db import get_session`
at the top of the file.

- [ ] **Step 11: Run the whole suite**

Run: `pytest -v`
Expected: all pass. The contract test from Task 3 still passes because the response *shape*
is unchanged — that is the point of building contract-first.

- [ ] **Step 12: Commit**

```bash
git add app/adapters app/api/v1 tests/adapters tests/api tests/conftest.py
git commit -m "feat: CSV validate/preview/import wizard and API bulk onboarding"
```

---

## Task 9: Sentinel sandbox adapter

Onboards the live government cameras from `cameras.json` through the same pipeline. This is
the strongest demo moment in Model 1.

**Files:**
- Create: `app/adapters/sentinel_adapter.py`
- Test: `tests/adapters/test_sentinel_adapter.py`

- [ ] **Step 1: Capture a real catalogue sample**

While signed in to the portal, save the catalogue as a fixture:

```bash
mkdir -p tests/fixtures
# In the browser, open https://cctv.corp8.cloud/cameras.json and save the body to:
#   tests/fixtures/sentinel_cameras.json
```

If the catalogue has no latitude/longitude, note it — Plan 2 adds an offline Gujarat
gazetteer geocoding stage. The adapter below reads coordinates from the fields named in the
department's `field_mappings` config, so no adapter code changes when the shape differs.

- [ ] **Step 2: Write the failing test**

Create `tests/adapters/test_sentinel_adapter.py`:

```python
from uuid import uuid4

import httpx
import pytest

from app.adapters.sentinel_adapter import SentinelAdapter
from app.core.enums import SourceType

CATALOGUE = [
    {
        "id": "cam04",
        "name": "Nehru Bridge",
        "lat": 23.0225,
        "lon": 72.5714,
        "codec": "h264",
        "resolution": "1920x1080",
        "live": True,
        "hls": "https://cctv.corp8.cloud/cam04/index.m3u8",
        "rtsp": "rtsp://103.250.160.189:8554/stream/cam04",
        "whep": "http://103.250.160.189:8889/stream/cam04/whep",
    }
]


@pytest.mark.asyncio
async def test_fetch_maps_catalogue_entries_to_raw_records():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=CATALOGUE))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )

    records = await adapter.fetch(uuid4())

    assert len(records) == 1
    assert records[0].payload["id"] == "cam04"
    assert records[0].source_type == SourceType.ADAPTER
    assert records[0].source_ref == "sentinel:cam04"


@pytest.mark.asyncio
async def test_fetch_collects_the_three_stream_endpoints_with_reachability():
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=CATALOGUE))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )

    endpoints = adapter.endpoints_for(CATALOGUE[0])

    by_protocol = {e["protocol"]: e for e in endpoints}
    assert by_protocol["hls"]["reachability"] == "public_cdn"
    assert by_protocol["hls"]["requires_auth"] is True
    assert by_protocol["rtsp"]["reachability"] == "direct_ip"
    assert by_protocol["whep"]["reachability"] == "direct_ip"


@pytest.mark.asyncio
async def test_catalogue_wrapped_in_an_object_is_also_accepted():
    payload = {"cameras": CATALOGUE}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    adapter = SentinelAdapter(
        catalogue_url="https://cctv.corp8.cloud/cameras.json", transport=transport
    )
    assert len(await adapter.fetch(uuid4())) == 1
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `pytest tests/adapters/test_sentinel_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.adapters.sentinel_adapter'`

- [ ] **Step 4: Create `app/adapters/sentinel_adapter.py`**

```python
from typing import Any
from uuid import UUID

import httpx

from app.core.enums import Reachability, SourceType, StreamProtocol
from app.schemas.ingestion import RawCameraRecord

# The integrator's guide is explicit: "the catalogue is the contract, the URL pattern
# is not." We therefore read every endpoint from the catalogue and never template one.
_PROTOCOL_KEYS: dict[str, tuple[StreamProtocol, Reachability, bool]] = {
    "hls": (StreamProtocol.HLS, Reachability.PUBLIC_CDN, True),
    "rtsp": (StreamProtocol.RTSP, Reachability.DIRECT_IP, False),
    "whep": (StreamProtocol.WHEP, Reachability.DIRECT_IP, False),
}


class SentinelAdapter:
    code = "sentinel"

    def __init__(
        self,
        catalogue_url: str,
        session_cookie: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.catalogue_url = catalogue_url
        self.session_cookie = session_cookie
        self.transport = transport

    async def _get_catalogue(self) -> list[dict[str, Any]]:
        cookies = {"session": self.session_cookie} if self.session_cookie else None
        async with httpx.AsyncClient(
            transport=self.transport, timeout=30.0, cookies=cookies
        ) as client:
            response = await client.get(self.catalogue_url)
            response.raise_for_status()
            body = response.json()

        if isinstance(body, list):
            return body
        for key in ("cameras", "items", "data"):
            if isinstance(body.get(key), list):
                return body[key]
        raise ValueError(f"Unrecognised catalogue shape: keys={list(body)}")

    def endpoints_for(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        endpoints: list[dict[str, Any]] = []
        for key, (protocol, reachability, requires_auth) in _PROTOCOL_KEYS.items():
            url = entry.get(key)
            if not url:
                continue
            endpoints.append(
                {
                    "protocol": protocol.value,
                    "url": url,
                    "codec": entry.get("codec"),
                    "resolution": entry.get("resolution"),
                    "reachability": reachability.value,
                    "requires_auth": requires_auth,
                    "credential_ref": "sentinel_cdn_password" if requires_auth else None,
                    "is_primary": protocol is StreamProtocol.HLS,
                }
            )
        return endpoints

    async def fetch(self, department_id: UUID) -> list[RawCameraRecord]:
        entries = await self._get_catalogue()
        records: list[RawCameraRecord] = []
        for entry in entries:
            camera_id = entry.get("id")
            payload = dict(entry)
            payload["_stream_endpoints"] = self.endpoints_for(entry)
            records.append(
                RawCameraRecord(
                    payload=payload,
                    department_id=department_id,
                    source_type=SourceType.ADAPTER,
                    source_ref=f"sentinel:{camera_id}",
                )
            )
        return records
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `pytest tests/adapters/test_sentinel_adapter.py -v`
Expected: 3 passed

- [ ] **Step 6: Run it against the real catalogue**

Create a `field_mappings` row for a `SENTINEL` department matching the real JSON keys, then:

```bash
python -c "
import asyncio, json
from app.adapters.sentinel_adapter import SentinelAdapter
from uuid import uuid4
a = SentinelAdapter('https://cctv.corp8.cloud/cameras.json', session_cookie='<your-cookie>')
print(json.dumps([r.payload for r in asyncio.run(a.fetch(uuid4()))][:2], indent=2))
"
```
Expected: two real camera objects. Confirm whether they carry coordinates — this answers
open question 1 in the spec.

- [ ] **Step 7: Commit**

```bash
git add app/adapters/sentinel_adapter.py tests/adapters/test_sentinel_adapter.py tests/fixtures
git commit -m "feat: Sentinel sandbox adapter onboarding live government cameras"
```

---

## Task 10: MVT tile endpoint

**Files:**
- Create: `app/services/tiles.py`, `app/api/v1/routers/tiles.py`
- Modify: `app/api/v1/router.py`
- Test: `tests/api/test_tiles.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_tiles.py`:

```python
import pytest

from app.models.camera import Camera


@pytest.mark.asyncio
async def test_tile_containing_a_camera_returns_protobuf_bytes(
    api_client, session, seeded_department
):
    session.add(
        Camera(
            camera_uid="GJ-AMC-000001",
            department_id=seeded_department,
            external_camera_id="A-1",
            location="SRID=4326;POINT(72.5714 23.0225)",
        )
    )
    await session.commit()

    # z12 tile covering Ahmedabad
    response = await api_client.get("/api/v1/tiles/cameras/12/2873/1778.mvt")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.mapbox-vector-tile"
    assert len(response.content) > 0


@pytest.mark.asyncio
async def test_empty_tile_returns_204(api_client, seeded_department):
    response = await api_client.get("/api/v1/tiles/cameras/12/1/1.mvt")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_zoom_out_of_range_is_rejected(api_client):
    assert (await api_client.get("/api/v1/tiles/cameras/25/1/1.mvt")).status_code == 422
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/api/test_tiles.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Create `app/services/tiles.py`**

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Below this zoom, individual markers are meaningless and slow — serve grid-aggregated
# counts instead. ST_SnapToGrid quantises in Web Mercator metres.
CLUSTER_ZOOM_THRESHOLD = 11

_POINT_SQL = text(
    """
    WITH bounds AS (
        SELECT ST_TileEnvelope(:z, :x, :y) AS merc,
               ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)::geography AS geog
    )
    SELECT ST_AsMVT(tile, 'cameras', 4096, 'geom') FROM (
        SELECT
            ST_AsMVTGeom(
                ST_Transform(c.location::geometry, 3857), bounds.merc, 4096, 64, true
            ) AS geom,
            c.id::text          AS id,
            c.camera_uid        AS camera_uid,
            c.current_status    AS status,
            c.camera_type       AS camera_type,
            c.ownership_class   AS ownership_class,
            c.department_id::text AS department_id
        FROM cameras c, bounds
        WHERE c.is_active
          AND c.lifecycle_state = 'active'
          AND c.location && bounds.geog
    ) AS tile
    """
)

_CLUSTER_SQL = text(
    """
    WITH bounds AS (
        SELECT ST_TileEnvelope(:z, :x, :y) AS merc,
               ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)::geography AS geog
    ),
    clustered AS (
        SELECT
            ST_SnapToGrid(ST_Transform(c.location::geometry, 3857), :cell, :cell) AS cell,
            count(*) AS camera_count,
            count(*) FILTER (WHERE c.current_status = 'offline') AS offline_count
        FROM cameras c, bounds
        WHERE c.is_active
          AND c.lifecycle_state = 'active'
          AND c.location && bounds.geog
        GROUP BY 1
    )
    SELECT ST_AsMVT(tile, 'camera_clusters', 4096, 'geom') FROM (
        SELECT
            ST_AsMVTGeom(clustered.cell, bounds.merc, 4096, 64, true) AS geom,
            clustered.camera_count,
            clustered.offline_count
        FROM clustered, bounds
    ) AS tile
    """
)


class TileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cameras(self, z: int, x: int, y: int) -> bytes:
        if z >= CLUSTER_ZOOM_THRESHOLD:
            result = await self.session.execute(_POINT_SQL, {"z": z, "x": x, "y": y})
        else:
            # Roughly one cluster per 40 screen pixels at this zoom.
            cell = 40 * (2 * 20037508.34) / (256 * 2**z)
            result = await self.session.execute(
                _CLUSTER_SQL, {"z": z, "x": x, "y": y, "cell": cell}
            )
        return bytes(result.scalar_one() or b"")
```

- [ ] **Step 4: Create `app/api/v1/routers/tiles.py`**

```python
from fastapi import APIRouter, Depends, Path, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.services.tiles import TileService

router = APIRouter(prefix="/tiles", tags=["tiles"])


@router.get(
    "/cameras/{z}/{x}/{y}.mvt",
    summary="Mapbox Vector Tile of cameras",
    description=(
        "Clustered counts below zoom 11, individual cameras at zoom 11 and above. "
        "Returns 204 when the tile contains no cameras."
    ),
    response_class=Response,
)
async def camera_tile(
    z: int = Path(ge=0, le=22),
    x: int = Path(ge=0),
    y: int = Path(ge=0),
    session: AsyncSession = Depends(get_session),
) -> Response:
    tile = await TileService(session).cameras(z, x, y)
    if not tile:
        return Response(status_code=204)
    return Response(
        content=tile,
        media_type="application/vnd.mapbox-vector-tile",
        headers={"Cache-Control": "public, max-age=60"},
    )
```

- [ ] **Step 5: Register the router**

In `app/api/v1/router.py`:

```python
from app.api.v1.routers import cameras, onboarding, tiles

api_router.include_router(tiles.router)
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `pytest tests/api/test_tiles.py -v`
Expected: 3 passed

- [ ] **Step 7: Commit**

```bash
git add app/services/tiles.py app/api/v1/routers/tiles.py tests/api/test_tiles.py app/api/v1/router.py
git commit -m "feat: PostGIS MVT tile endpoint with zoom-dependent clustering"
```

---

## Task 11: The map

**Files:**
- Create: `web/package.json`, `web/app/layout.tsx`, `web/app/map/page.tsx`, `web/components/CameraMap.tsx`
- Test: manual verification (Plan 2 adds Playwright coverage)

- [ ] **Step 1: Scaffold the frontend**

Run:
```bash
npx create-next-app@latest web --typescript --app --tailwind --eslint --no-src-dir --import-alias "@/*"
cd web && npm install maplibre-gl
```

- [ ] **Step 2: Create `web/components/CameraMap.tsx`**

```tsx
"use client";

import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const STATUS_COLOURS: maplibregl.ExpressionSpecification = [
  "match",
  ["get", "status"],
  "online", "#16a34a",
  "offline", "#dc2626",
  "maintenance", "#d97706",
  "#64748b",
];

export function CameraMap() {
  const container = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!container.current) return;

    const map = new maplibregl.Map({
      container: container.current,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
          },
        },
        layers: [{ id: "osm", type: "raster", source: "osm" }],
      },
      center: [72.5714, 23.0225],
      zoom: 10,
    });

    map.on("load", () => {
      map.addSource("cameras", {
        type: "vector",
        tiles: [`${API}/api/v1/tiles/cameras/{z}/{x}/{y}.mvt`],
        minzoom: 0,
        maxzoom: 22,
      });

      map.addLayer({
        id: "camera-clusters",
        type: "circle",
        source: "cameras",
        "source-layer": "camera_clusters",
        paint: {
          "circle-radius": [
            "interpolate", ["linear"], ["get", "camera_count"],
            1, 8, 100, 18, 5000, 34,
          ],
          "circle-color": "#0f2d5e",
          "circle-opacity": 0.85,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
        },
      });

      map.addLayer({
        id: "cluster-count",
        type: "symbol",
        source: "cameras",
        "source-layer": "camera_clusters",
        layout: { "text-field": ["get", "camera_count"], "text-size": 12 },
        paint: { "text-color": "#ffffff" },
      });

      map.addLayer({
        id: "camera-points",
        type: "circle",
        source: "cameras",
        "source-layer": "cameras",
        paint: {
          "circle-radius": 6,
          "circle-color": STATUS_COLOURS,
          "circle-stroke-width": 1.5,
          "circle-stroke-color": "#ffffff",
        },
      });

      map.on("click", "camera-points", (event) => {
        const feature = event.features?.[0];
        if (!feature) return;
        const { camera_uid, status, camera_type } = feature.properties as Record<string, string>;
        new maplibregl.Popup()
          .setLngLat(event.lngLat)
          .setHTML(
            `<strong>${camera_uid}</strong><br/>${camera_type} · ${status}`
          )
          .addTo(map);
      });

      map.on("mouseenter", "camera-points", () => {
        map.getCanvas().style.cursor = "pointer";
      });
      map.on("mouseleave", "camera-points", () => {
        map.getCanvas().style.cursor = "";
      });
    });

    return () => map.remove();
  }, []);

  return <div ref={container} className="h-screen w-full" />;
}
```

- [ ] **Step 3: Create `web/app/map/page.tsx`**

```tsx
import { CameraMap } from "@/components/CameraMap";

export default function MapPage() {
  return <CameraMap />;
}
```

- [ ] **Step 4: Enable CORS on the API**

In `app/main.py`, inside `create_app()` before the router registration:

```python
    from fastapi.middleware.cors import CORSMiddleware

    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
```

- [ ] **Step 5: Verify the full vertical slice end to end**

Run:
```bash
docker compose up -d db redis
alembic upgrade head
python -m seeds.departments                 # creates the five sandbox departments
uvicorn app.main:app --reload --port 8000 &
cd web && npm run dev
```

Then:
1. `POST /api/v1/onboarding/preview` with a CSV containing one bad row → confirm the row
   error is reported and nothing was written
2. `POST /api/v1/onboarding/import` with the corrected CSV → confirm `created: 1`
3. Open `http://localhost:3000/map` → the camera appears at its coordinates, coloured by
   status, and clicking it shows the popup
4. Zoom out below z11 → markers collapse into a numbered cluster

Expected: all four steps work. **This is the demo spine. Once it holds, everything else is
breadth.**

- [ ] **Step 6: Commit**

```bash
git add web app/main.py
git commit -m "feat: MapLibre map rendering cameras from PostGIS vector tiles"
```

---

## Self-review against the spec

**Covered by this plan:** spec §4 architecture (Task 1), §5 ingestion pipeline (Tasks 5–7),
§6 field mappings (Task 5), §7 data model core tables (Task 4), §8 API surface partial
(Tasks 3, 8, 10), §10 tile serving (Task 10), §14 frontend `/map` (Task 11).

**Deferred, with the plan that owns each:**

| Spec section | Deferred to |
|---|---|
| §7 `recorders`, `camera_health`, `admin_boundaries`, audit, webhook, coverage tables | Plans 3–6 |
| §8 spatial search, export, health, coverage routes | Plans 2–4 |
| §9 auth, RBAC, `Principal` resolution | Plan 5 |
| §10 PMTiles offline basemap, filter parameters on tiles | Plan 2 |
| §11 health monitoring | Plan 3 |
| §12 gap analysis | Plan 4 |
| §13 webhooks | Plan 6 |
| §15 80k seed generation | Plan 6 |

**Known gap accepted deliberately:** `stream_endpoints` rows are not yet persisted by the
ingestion pipeline — the Sentinel adapter collects them into `payload["_stream_endpoints"]`
and Task 8's `_persist` ignores that key. Plan 2 adds persistence and wires
`GET /cameras/{id}/streams` to real rows. The contract shape is already correct and stubbed,
so no consumer breaks.
