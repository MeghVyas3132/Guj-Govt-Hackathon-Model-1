# Model 1 GIS Depth — Implementation Plan (Plan 2 of 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the bare map from Plan 1 into the searchable, filterable GIS console that answers "how many cameras near this point," "which cameras in this district," and "show me only AMC's offline PTZ cameras" — and make it work with no internet.

**Architecture:** Spatial predicates run in PostGIS against the `GEOGRAPHY` column so distances are true metres, not degrees. Filters are expressed once as a `CameraFilter` object and reused by the list endpoint, the CSV export, and the tile query, so the map and the table can never disagree about what matches.

**Tech Stack:** PostGIS `ST_DWithin` / `ST_Intersects` / `ST_Distance`, GeoJSON district boundaries, MapLibre GL JS, PMTiles.

**Prerequisite:** Plan 1 complete.

---

## File structure additions

```
app/
  models/admin_boundary.py
  schemas/filters.py                 CameraFilter — one filter object, three consumers
  repositories/boundary.py
  services/export.py
  api/v1/routers/search.py
seeds/
  boundaries.py                      loads Gujarat district GeoJSON
data/
  gujarat_districts.geojson
  gujarat_basemap.pmtiles
web/
  components/FilterPanel.tsx
  components/CameraDrawer.tsx
  components/RadiusTool.tsx
  lib/filters.ts
```

---

## Task 1: Persist stream endpoints

Plan 1 left `payload["_stream_endpoints"]` collected but ignored. This closes that gap so
`GET /cameras/{id}/streams` returns real rows and Models 2–4 can stop using the stub.

**Files:**
- Modify: `app/services/ingestion.py`
- Modify: `app/api/v1/routers/cameras.py`
- Test: `tests/services/test_stream_persistence.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_stream_persistence.py`:

```python
import pytest
from sqlalchemy import select

from app.core.enums import SourceType
from app.models.stream_endpoint import StreamEndpoint
from app.schemas.ingestion import RawCameraRecord
from app.services.ingestion import IngestionService


def sentinel_record(dept_id):
    return RawCameraRecord(
        payload={
            "external_camera_id": "cam04",
            "latitude": 23.0225,
            "longitude": 72.5714,
            "_stream_endpoints": [
                {
                    "protocol": "hls",
                    "url": "https://cctv.corp8.cloud/cam04/index.m3u8",
                    "codec": "h264",
                    "resolution": "1920x1080",
                    "reachability": "public_cdn",
                    "requires_auth": True,
                    "credential_ref": "sentinel_cdn_password",
                    "is_primary": True,
                },
                {
                    "protocol": "rtsp",
                    "url": "rtsp://103.250.160.189:8554/stream/cam04",
                    "codec": "h264",
                    "resolution": "1920x1080",
                    "reachability": "direct_ip",
                    "requires_auth": False,
                    "credential_ref": None,
                    "is_primary": False,
                },
            ],
        },
        department_id=dept_id,
        source_type=SourceType.ADAPTER,
    )


@pytest.mark.asyncio
async def test_endpoints_are_persisted_on_create(session, seeded_department_obj):
    service = IngestionService(session)
    await service.ingest(
        [sentinel_record(seeded_department_obj.id)], seeded_department_obj, mode="commit"
    )

    endpoints = (await session.execute(select(StreamEndpoint))).scalars().all()
    assert {e.protocol for e in endpoints} == {"hls", "rtsp"}
    assert next(e for e in endpoints if e.protocol == "hls").reachability == "public_cdn"


@pytest.mark.asyncio
async def test_resyncing_replaces_endpoints_rather_than_duplicating(
    session, seeded_department_obj
):
    service = IngestionService(session)
    for _ in range(3):
        await service.ingest(
            [sentinel_record(seeded_department_obj.id)],
            seeded_department_obj,
            mode="commit",
        )

    endpoints = (await session.execute(select(StreamEndpoint))).scalars().all()
    assert len(endpoints) == 2


@pytest.mark.asyncio
async def test_endpoints_are_not_written_during_validate_only(
    session, seeded_department_obj
):
    service = IngestionService(session)
    await service.ingest(
        [sentinel_record(seeded_department_obj.id)],
        seeded_department_obj,
        mode="validate_only",
    )
    assert (await session.execute(select(StreamEndpoint))).first() is None
```

Add this fixture to `tests/conftest.py`:

```python
@pytest.fixture
async def seeded_department_obj(session):
    from app.models.department import Department
    from app.models.field_mapping import FieldMapping

    dept = Department(code="SEN", name="Sentinel Sandbox")
    session.add(dept)
    await session.flush()
    session.add(FieldMapping(department_id=dept.id, version=1, config={}))
    await session.commit()
    return dept
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/services/test_stream_persistence.py -v`
Expected: FAIL — no `StreamEndpoint` rows written

- [ ] **Step 3: Add endpoint syncing to `app/services/ingestion.py`**

Add this import at the top:

```python
from sqlalchemy import delete

from app.models.stream_endpoint import StreamEndpoint
```

Add this method to `IngestionService`:

```python
    async def _sync_endpoints(
        self, camera: Camera, endpoints: list[dict[str, Any]]
    ) -> None:
        """Replace rather than merge. The catalogue is authoritative, so a URL that
        disappeared upstream must disappear here too."""
        await self.session.execute(
            delete(StreamEndpoint).where(StreamEndpoint.camera_id == camera.id)
        )
        for endpoint in endpoints:
            self.session.add(
                StreamEndpoint(
                    camera_id=camera.id,
                    protocol=endpoint["protocol"],
                    url=endpoint["url"],
                    codec=endpoint.get("codec"),
                    resolution=endpoint.get("resolution"),
                    is_primary=endpoint.get("is_primary", False),
                    reachability=endpoint.get("reachability", "direct_ip"),
                    requires_auth=endpoint.get("requires_auth", False),
                    credential_ref=endpoint.get("credential_ref"),
                )
            )
```

In `_persist`, capture the endpoints before the writable-field loop and call the sync just
before each `return`. At the top of `_persist` add:

```python
        endpoints = record.payload.get("_stream_endpoints") or []
```

Then immediately before `return "created"`:

```python
        if endpoints is not None:
            await self._sync_endpoints(camera, endpoints)
            await self.session.flush()
```

`is not None`, not a truthiness check. `if endpoints:` conflates "the payload has no
`_stream_endpoints` key at all" (CSV, manual and REST onboarding — leave existing endpoints
alone) with "the catalogue returned an empty list" (every URL vanished upstream — clear
them). The truthy version can never clear endpoints, so a camera that loses all its streams
would keep serving dead URLs forever.

Rather than repeating this before three separate `return` statements, restructure `_persist`
to a single exit point and sync once after the outcome is decided. The sync must run on
`skipped` as well as `created`/`updated`: a camera's core fields can be identical while its
stream URLs have moved.

And immediately before `return "updated"` and `return "skipped"`, add the same three lines.
Endpoint syncing must run even on a `skipped` outcome, because a camera's core fields can be
unchanged while its stream URLs have moved.

- [ ] **Step 4: Exclude the private key from metadata passthrough**

In `app/services/normalization.py`, in the `resolve` loop, add this guard as the first
statement inside the `for source_key, value in raw.items():` block:

```python
            if source_key.startswith("_"):
                continue
```

This keeps `_stream_endpoints` out of `cameras.metadata`.

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `pytest tests/services/test_stream_persistence.py -v`
Expected: 3 passed

- [ ] **Step 6: Wire the real streams route**

In `app/api/v1/routers/cameras.py`, delete `_STUB_STREAMS` and replace `get_camera_streams`:

```python
@router.get(
    "/{camera_id}/streams",
    response_model=list[StreamEndpointRead],
    summary="Stream endpoints for this camera",
    description=(
        "Entry point for Models 2-4. Prefer the endpoint whose reachability matches "
        "your network: public_cdn works anywhere, direct_ip needs gateway ports open."
    ),
)
async def get_camera_streams(
    camera_id: UUID, session: AsyncSession = Depends(get_session)
) -> list[StreamEndpointRead]:
    from sqlalchemy import select

    from app.models.stream_endpoint import StreamEndpoint

    rows = (
        (
            await session.execute(
                select(StreamEndpoint)
                .where(StreamEndpoint.camera_id == camera_id)
                .order_by(StreamEndpoint.is_primary.desc())
            )
        )
        .scalars()
        .all()
    )
    return [StreamEndpointRead.model_validate(row) for row in rows]
```

- [ ] **Step 6b: Seed the contract test's reference camera**

`tests/api/test_contract.py` asserts three specific protocols for camera
`00000000-…-0001`. That passed against `_STUB_STREAMS`; the moment the route reads the
database it returns `200 []` and fails. Do **not** edit the contract file — that file
passing unchanged is the signal the published shape did not drift. Instead add
`tests/api/conftest.py` with an autouse fixture that seeds the reference camera for tests
whose closure includes the bare `client` fixture (check `request.fixturenames`, so tests
asserting an empty registry are untouched). Seed it through
`SentinelAdapter.endpoints_for()` into `IngestionService._sync_endpoints()` — production
mapping, production writer — so the values under test are the real ones rather than a
second hand-maintained copy.

Every later task that turns a stubbed route into a database-backed one hits this same wall.

- [ ] **Step 7: Run the full suite**

Run: `pytest -v`
Expected: all pass, including the Plan 1 contract test — the shape is unchanged.

- [ ] **Step 8: Commit**

```bash
git add app/services/ingestion.py app/services/normalization.py app/api/v1/routers/cameras.py tests
git commit -m "feat: persist stream endpoints and serve them from the real table"
```

---

## Task 2: Administrative boundaries

**Files:**
- Create: `app/models/admin_boundary.py`, `seeds/boundaries.py`, `data/gujarat_districts.geojson`
- Test: `tests/repositories/test_boundaries.py`

- [ ] **Step 1: Download Gujarat district boundaries**

Run:
```bash
mkdir -p data
# State-scoped file: 7.9 MB and already Gujarat-only. (The India-wide
# INDIA/india_district.geojson path in older docs now 404s; the repo was
# reorganised and the equivalent INDIA_DISTRICTS.geojson is 77 MB.)
curl -sL "https://raw.githubusercontent.com/datta07/INDIAN-SHAPEFILES/master/STATES/GUJARAT/GUJARAT_DISTRICTS.geojson" \
  -o data/gujarat_districts.geojson
python3 - <<'PY'
import json
src = json.load(open("data/gujarat_districts.geojson"))
props = src["features"][0]["properties"]
print("property keys:", sorted(props))
assert all(f["properties"]["stname"] == "GUJARAT" for f in src["features"])
print(f'{len(src["features"])} districts')
PY
```
Expected: **33 districts**, with keys `dtname` (name), `stname` (= `GUJARAT`) and
`dtcode11` (2011 Census code).

- [ ] **Step 2: Create `app/models/admin_boundary.py`**

```python
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AdminBoundary(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "admin_boundaries"
    __table_args__ = (Index("ix_admin_boundaries_geom", "geom", postgresql_using="gist"),)

    level: Mapped[str] = mapped_column(String(16))  # district | taluka | ward
    name: Mapped[str] = mapped_column(String(200), index=True)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("admin_boundaries.id"), nullable=True
    )
    population: Mapped[int | None] = mapped_column(Integer, nullable=True)
    geom: Mapped[Any] = mapped_column(
        Geography(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False)
    )
```

- [ ] **Step 3: Generate and run the migration**

Run:
```bash
alembic revision --autogenerate -m "admin boundaries"
alembic upgrade head
```

- [ ] **Step 4: Create `seeds/boundaries.py`**

```python
import asyncio
import json
from pathlib import Path

from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, shape
from sqlalchemy import delete

from app.core.db import SessionLocal
from app.models.admin_boundary import AdminBoundary

GEOJSON = Path("data/gujarat_districts.geojson")
NAME_KEYS = ("district", "DISTRICT", "dtname", "NAME_2")


def _name(properties: dict) -> str:
    for key in NAME_KEYS:
        if properties.get(key):
            return str(properties[key]).title()
    raise KeyError(f"No district name found in {list(properties)}")


async def main() -> None:
    collection = json.loads(GEOJSON.read_text())
    async with SessionLocal() as session:
        # Re-runnable: appending unconditionally would leave two copies of every
        # district, and Plan 4 would then count every cell's coverage twice.
        await session.execute(
            delete(AdminBoundary).where(AdminBoundary.level == "district")
        )
        for feature in collection["features"]:
            geometry = shape(feature["geometry"])
            if geometry.geom_type == "Polygon":
                geometry = MultiPolygon([geometry])
            session.add(
                AdminBoundary(
                    level="district",
                    name=_name(feature["properties"]),
                    # Census names use 2011 spellings ("Ahmadabad", "Kachchh"), so the
                    # census code is the stable join key, not the display name.
                    code=feature["properties"].get("dtcode11"),
                    geom=from_shape(geometry, srid=4326),
                )
            )
        await session.commit()
    print(f"Loaded {len(collection['features'])} districts")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Write the failing test**

Create `tests/repositories/test_boundaries.py`:

```python
import pytest
from geoalchemy2.shape import from_shape
from shapely.geometry import MultiPolygon, Polygon
from sqlalchemy import func, select

from app.models.admin_boundary import AdminBoundary
from app.models.camera import Camera


@pytest.fixture
async def ahmedabad(session):
    box = MultiPolygon([Polygon([(72.4, 22.9), (72.7, 22.9), (72.7, 23.2), (72.4, 23.2)])])
    boundary = AdminBoundary(level="district", name="Ahmedabad", geom=from_shape(box, srid=4326))
    session.add(boundary)
    await session.commit()
    return boundary


@pytest.mark.asyncio
async def test_point_inside_the_district_is_matched(session, ahmedabad, seeded_department):
    session.add(
        Camera(
            camera_uid="GJ-AMC-000001",
            department_id=seeded_department,
            external_camera_id="A-1",
            location="SRID=4326;POINT(72.5714 23.0225)",
        )
    )
    await session.commit()

    stmt = (
        select(func.count())
        .select_from(Camera)
        .join(AdminBoundary, func.ST_Intersects(Camera.location, AdminBoundary.geom))
        .where(AdminBoundary.id == ahmedabad.id)
    )
    assert (await session.execute(stmt)).scalar_one() == 1


@pytest.mark.asyncio
async def test_point_outside_the_district_is_not_matched(
    session, ahmedabad, seeded_department
):
    session.add(
        Camera(
            camera_uid="GJ-AMC-000002",
            department_id=seeded_department,
            external_camera_id="A-2",
            location="SRID=4326;POINT(70.8 22.3)",  # Rajkot
        )
    )
    await session.commit()

    stmt = (
        select(func.count())
        .select_from(Camera)
        .join(AdminBoundary, func.ST_Intersects(Camera.location, AdminBoundary.geom))
        .where(AdminBoundary.id == ahmedabad.id)
    )
    assert (await session.execute(stmt)).scalar_one() == 0
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/repositories/test_boundaries.py -v`
Expected: 2 passed

- [ ] **Step 7: Load the real data and verify**

Run:
```bash
python -m seeds.boundaries
docker compose exec db psql -U sentinel -d sentinel -c \
  "SELECT count(*), min(name), max(name) FROM admin_boundaries;"
```
Expected: ~33 rows

- [ ] **Step 8: Commit**

```bash
git add app/models/admin_boundary.py seeds/boundaries.py data/gujarat_districts.geojson alembic tests
git commit -m "feat: Gujarat district boundaries with spatial containment queries"
```

---

## Task 3: One filter object, three consumers

The list endpoint, the CSV export and the tile query must never disagree about what matches.
This task defines the filter once.

**Files:**
- Create: `app/schemas/filters.py`, `app/repositories/boundary.py`
- Modify: `app/repositories/camera.py`
- Test: `tests/repositories/test_camera_filters.py`

- [ ] **Step 1: Create `app/schemas/filters.py`**

```python
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.core.enums import CameraStatus, CameraType, OwnershipClass


class CameraFilter(BaseModel):
    """Shared by the list endpoint, CSV export and MVT tiles so the map and the table
    can never show different result sets for the same query."""

    q: str | None = Field(default=None, description="Free text over uid, name, address.")
    department_ids: list[UUID] = Field(default_factory=list)
    camera_types: list[CameraType] = Field(default_factory=list)
    statuses: list[CameraStatus] = Field(default_factory=list)
    ownership_classes: list[OwnershipClass] = Field(default_factory=list)
    district_id: UUID | None = None
    near_lat: float | None = Field(default=None, ge=-90, le=90)
    near_lon: float | None = Field(default=None, ge=-180, le=180)
    radius_m: float | None = Field(default=None, gt=0, le=200_000)

    @model_validator(mode="after")
    def radius_search_needs_all_three(self) -> "CameraFilter":
        provided = [self.near_lat, self.near_lon, self.radius_m]
        if any(v is not None for v in provided) and any(v is None for v in provided):
            raise ValueError("near_lat, near_lon and radius_m must be supplied together.")
        return self

    @property
    def has_radius(self) -> bool:
        return self.radius_m is not None
```

- [ ] **Step 2: Write the failing test**

Create `tests/repositories/test_camera_filters.py`:

```python
import pytest

from app.core.enums import CameraStatus, CameraType
from app.models.camera import Camera
from app.repositories.camera import CameraRepository
from app.schemas.filters import CameraFilter


@pytest.fixture
async def cameras(session, seeded_department):
    rows = [
        ("GJ-AMC-000001", "A-1", 72.5714, 23.0225, CameraType.FIXED, CameraStatus.ONLINE),
        ("GJ-AMC-000002", "A-2", 72.5800, 23.0300, CameraType.PTZ, CameraStatus.OFFLINE),
        ("GJ-AMC-000003", "A-3", 70.8000, 22.3000, CameraType.FIXED, CameraStatus.ONLINE),
    ]
    for uid, ext, lon, lat, kind, status in rows:
        session.add(
            Camera(
                camera_uid=uid,
                department_id=seeded_department,
                external_camera_id=ext,
                location=f"SRID=4326;POINT({lon} {lat})",
                camera_type=kind,
                current_status=status,
                name=f"Junction {ext}",
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_status_filter(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(CameraFilter(statuses=[CameraStatus.OFFLINE]))
    assert [c.camera_uid for c in found] == ["GJ-AMC-000002"]


@pytest.mark.asyncio
async def test_type_filter(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(CameraFilter(camera_types=[CameraType.FIXED]))
    assert len(found) == 2


@pytest.mark.asyncio
async def test_radius_search_uses_real_metres(session, cameras):
    repo = CameraRepository(session)
    # A-1 and A-2 are ~1 km apart; A-3 is in Rajkot, ~200 km away.
    found = await repo.list(
        CameraFilter(near_lat=23.0225, near_lon=72.5714, radius_m=2000)
    )
    assert {c.camera_uid for c in found} == {"GJ-AMC-000001", "GJ-AMC-000002"}


@pytest.mark.asyncio
async def test_tight_radius_excludes_the_neighbour(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(
        CameraFilter(near_lat=23.0225, near_lon=72.5714, radius_m=100)
    )
    assert {c.camera_uid for c in found} == {"GJ-AMC-000001"}


@pytest.mark.asyncio
async def test_free_text_matches_name_case_insensitively(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(CameraFilter(q="junction a-3"))
    assert [c.camera_uid for c in found] == ["GJ-AMC-000003"]


@pytest.mark.asyncio
async def test_combined_filters_intersect(session, cameras):
    repo = CameraRepository(session)
    found = await repo.list(
        CameraFilter(
            camera_types=[CameraType.FIXED],
            near_lat=23.0225,
            near_lon=72.5714,
            radius_m=2000,
        )
    )
    assert [c.camera_uid for c in found] == ["GJ-AMC-000001"]
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `pytest tests/repositories/test_camera_filters.py -v`
Expected: FAIL — `CameraRepository` has no attribute `list`

- [ ] **Step 4: Add filtering to `app/repositories/camera.py`**

Add these imports:

```python
from sqlalchemy import Select, and_, func, or_

from app.models.admin_boundary import AdminBoundary
from app.schemas.filters import CameraFilter
```

Add these methods to `CameraRepository`:

```python
    def _apply(self, stmt: Select, filters: CameraFilter) -> Select:
        stmt = stmt.where(Camera.is_active, Camera.lifecycle_state == "active")

        if filters.department_ids:
            stmt = stmt.where(Camera.department_id.in_(filters.department_ids))
        if filters.camera_types:
            stmt = stmt.where(Camera.camera_type.in_([t.value for t in filters.camera_types]))
        if filters.statuses:
            stmt = stmt.where(Camera.current_status.in_([s.value for s in filters.statuses]))
        if filters.ownership_classes:
            stmt = stmt.where(
                Camera.ownership_class.in_([o.value for o in filters.ownership_classes])
            )
        if filters.q:
            pattern = f"%{filters.q.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Camera.camera_uid).like(pattern),
                    func.lower(Camera.name).like(pattern),
                    func.lower(Camera.address).like(pattern),
                    func.lower(Camera.external_camera_id).like(pattern),
                )
            )
        if filters.district_id:
            stmt = stmt.where(
                Camera.location.ST_Intersects(
                    select(AdminBoundary.geom)
                    .where(AdminBoundary.id == filters.district_id)
                    .scalar_subquery()
                )
            )
        if filters.has_radius:
            origin = func.ST_SetSRID(
                func.ST_MakePoint(filters.near_lon, filters.near_lat), 4326
            ).cast(Camera.location.type)
            stmt = stmt.where(func.ST_DWithin(Camera.location, origin, filters.radius_m))
        return stmt

    async def list(
        self, filters: CameraFilter, limit: int = 50, offset: int = 0
    ) -> list[Camera]:
        stmt = self._apply(select(Camera), filters)
        if filters.has_radius:
            origin = func.ST_SetSRID(
                func.ST_MakePoint(filters.near_lon, filters.near_lat), 4326
            ).cast(Camera.location.type)
            stmt = stmt.order_by(func.ST_Distance(Camera.location, origin))
        else:
            stmt = stmt.order_by(Camera.camera_uid)
        result = await self.session.execute(stmt.limit(limit).offset(offset))
        return list(result.scalars().all())

    async def count(self, filters: CameraFilter) -> int:
        stmt = self._apply(select(func.count()).select_from(Camera), filters)
        return (await self.session.execute(stmt)).scalar_one()
```

Radius results are ordered by true distance — which is exactly the question an investigator
asks at an incident location: *"what are the nearest cameras, closest first?"*

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `pytest tests/repositories/test_camera_filters.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add app/schemas/filters.py app/repositories/camera.py tests/repositories/test_camera_filters.py
git commit -m "feat: shared camera filter with PostGIS radius and district search"
```

---

## Task 4: Search endpoints and filtered tiles

**Files:**
- Modify: `app/api/v1/routers/cameras.py`, `app/api/v1/routers/tiles.py`, `app/services/tiles.py`
- Test: `tests/api/test_search.py`

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_search.py`:

```python
import pytest

from app.models.camera import Camera


@pytest.fixture
async def two_cameras(session, seeded_department):
    session.add_all(
        [
            Camera(
                camera_uid="GJ-AMC-000001", department_id=seeded_department,
                external_camera_id="A-1", location="SRID=4326;POINT(72.5714 23.0225)",
                current_status="online", camera_type="fixed",
            ),
            Camera(
                camera_uid="GJ-AMC-000002", department_id=seeded_department,
                external_camera_id="A-2", location="SRID=4326;POINT(70.8 22.3)",
                current_status="offline", camera_type="ptz",
            ),
        ]
    )
    await session.commit()


@pytest.mark.asyncio
async def test_nearby_returns_closest_first_with_distance(api_client, two_cameras):
    response = await api_client.get(
        "/api/v1/cameras/nearby?lat=23.0225&lon=72.5714&radius_m=5000"
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["camera_uid"] == "GJ-AMC-000001"
    assert items[0]["distance_m"] < 1


@pytest.mark.asyncio
async def test_status_filter_narrows_the_list(api_client, two_cameras):
    response = await api_client.get("/api/v1/cameras?statuses=offline")
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_radius_search_rejects_partial_parameters(api_client):
    response = await api_client.get("/api/v1/cameras/nearby?lat=23.0&radius_m=1000")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_tiles_honour_the_same_status_filter(api_client, two_cameras):
    matching = await api_client.get(
        "/api/v1/tiles/cameras/12/2873/1778.mvt?statuses=online"
    )
    excluded = await api_client.get(
        "/api/v1/tiles/cameras/12/2873/1778.mvt?statuses=offline"
    )
    assert matching.status_code == 200
    assert excluded.status_code == 204
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/api/test_search.py -v`
Expected: FAIL — but with **422, not 404**: `/cameras/{camera_id}` catches `nearby` and fails
to parse it as a UUID. That is the same hazard as the route-ordering rule below, and it means
`test_radius_search_rejects_partial_parameters` passes *before the endpoint exists* — it is not
a route-ordering guard. Add explicit tests asserting `/cameras/nearby` and `/cameras/export.csv`
return 200 rather than 422.

- [ ] **Step 3: Add a filter dependency and the nearby route**

In `app/api/v1/routers/cameras.py`, add:

```python
from app.core.enums import CameraStatus, CameraType, OwnershipClass
from app.repositories.camera import CameraRepository
from app.schemas.filters import CameraFilter


def camera_filter(
    q: str | None = Query(None),
    department_ids: list[UUID] = Query(default_factory=list),
    camera_types: list[CameraType] = Query(default_factory=list),
    statuses: list[CameraStatus] = Query(default_factory=list),
    ownership_classes: list[OwnershipClass] = Query(default_factory=list),
    district_id: UUID | None = Query(None),
) -> CameraFilter:
    return CameraFilter(
        q=q,
        department_ids=department_ids,
        camera_types=camera_types,
        statuses=statuses,
        ownership_classes=ownership_classes,
        district_id=district_id,
    )
```

Replace `list_cameras` with:

```python
def _to_read(row) -> CameraRead:
    from geoalchemy2.shape import to_shape

    point = to_shape(row.location)
    return CameraRead.model_validate(
        {
            **row.__dict__,
            "latitude": point.y,
            "longitude": point.x,
            "metadata": row.metadata_,
            "stream_endpoints": [],
        }
    )


@router.get("", response_model=Page[CameraRead], summary="List and filter cameras")
async def list_cameras(
    filters: CameraFilter = Depends(camera_filter),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> Page[CameraRead]:
    repo = CameraRepository(session)
    rows = await repo.list(filters, limit=limit, offset=offset)
    total = await repo.count(filters)
    return Page(items=[_to_read(r) for r in rows], total=total, limit=limit, offset=offset)
```

Add the nearby route. It is declared **before** `/{camera_id}` so FastAPI does not try to
parse "nearby" as a UUID. Put the query in `CameraRepository.list_nearby()` reusing `_origin`
and `_apply` rather than writing `ST_DWithin`/`ST_Distance` inline in the router — the
geography cast is the single line that keeps the radius in metres instead of degrees, and it
should exist in exactly one place:

```python
class CameraNearby(CameraRead):
    distance_m: float


@router.get(
    "/nearby",
    response_model=Page[CameraNearby],
    summary="Cameras within a radius, nearest first",
    description=(
        "The incident-response query: given an FIR location, which cameras could have "
        "seen it, closest first."
    ),
)
async def cameras_nearby(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_m: float = Query(..., gt=0, le=200_000),
    limit: int = Query(50, le=500),
    session: AsyncSession = Depends(get_session),
) -> Page[CameraNearby]:
    from sqlalchemy import func, select

    from app.models.camera import Camera

    origin = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326).cast(Camera.location.type)
    distance = func.ST_Distance(Camera.location, origin).label("distance_m")
    stmt = (
        select(Camera, distance)
        .where(
            Camera.is_active,
            Camera.lifecycle_state == "active",
            func.ST_DWithin(Camera.location, origin, radius_m),
        )
        .order_by(distance)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    items = [
        CameraNearby(**_to_read(camera).model_dump(), distance_m=round(dist, 2))
        for camera, dist in rows
    ]
    return Page(items=items, total=len(items), limit=limit, offset=0)
```

Note: `CameraNearby(**_to_read(camera).model_dump(), ...)` would pass `distance_m` twice if
`_to_read` ever gains that field. It will not — `CameraRead` has no `distance_m`.

Move the `@router.get("/nearby")` block above `@router.get("/{camera_id}")` in the file.

- [ ] **Step 4: Add filters to the tile query**

In `app/services/tiles.py`, change both SQL statements to accept a filter fragment. Replace
the module with this structure — the `WHERE` clause is now assembled:

```python
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.filters import CameraFilter

CLUSTER_ZOOM_THRESHOLD = 11


def _predicates(filters: CameraFilter) -> tuple[str, dict[str, Any]]:
    """Build the tile query's extra WHERE clauses from the shared filter.

    The two halves are kept strictly apart: the *clause text* is a fixed literal
    chosen by a branch on the filter, and every *value* leaves via a bound parameter.
    Nothing derived from the request is ever formatted into SQL -- not the values, not
    the column names, not the operators. The values are additionally constrained to
    enum members and UUIDs by `camera_filter` before they arrive here.

    Every field `camera_filter` can produce is handled here, and each clause mirrors
    its counterpart in `CameraRepository._apply` exactly -- `lower(col) LIKE lower(q)`
    rather than the merely-similar `ILIKE`, the same four searched columns, the same
    boundary subquery. A field reaching the tile endpoint and being dropped here is
    not a smaller result set, it is a map showing markers the table has filtered out.

    The radius fields are the one exception, and they are unreachable rather than
    ignored: `camera_filter` does not expose them, so only /cameras/nearby ever sets
    a radius and no tile request can carry one.
    """
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if filters.department_ids:
        clauses.append("c.department_id = ANY(:department_ids)")
        params["department_ids"] = [str(d) for d in filters.department_ids]
    if filters.camera_types:
        clauses.append("c.camera_type = ANY(:camera_types)")
        params["camera_types"] = [t.value for t in filters.camera_types]
    if filters.statuses:
        clauses.append("c.current_status = ANY(:statuses)")
        params["statuses"] = [s.value for s in filters.statuses]
    if filters.ownership_classes:
        clauses.append("c.ownership_class = ANY(:ownership_classes)")
        params["ownership_classes"] = [o.value for o in filters.ownership_classes]
    if filters.q:
        clauses.append(
            "(lower(c.camera_uid) LIKE :q"
            " OR lower(c.name) LIKE :q"
            " OR lower(c.address) LIKE :q"
            " OR lower(c.external_camera_id) LIKE :q)"
        )
        params["q"] = f"%{filters.q.lower()}%"
    if filters.district_id:
        clauses.append(
            "ST_Intersects("
            "c.location, (SELECT b.geom FROM admin_boundaries b WHERE b.id = :district_id)"
            ")"
        )
        params["district_id"] = str(filters.district_id)
    return ("".join(f"\n          AND {clause}" for clause in clauses), params)


_POINT_TEMPLATE = """
    WITH bounds AS (
        SELECT ST_TileEnvelope(:z, :x, :y) AS merc,
               ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)::geography AS geog
    )
    SELECT ST_AsMVT(tile, 'cameras', 4096, 'geom') FROM (
        SELECT
            ST_AsMVTGeom(
                ST_Transform(c.location::geometry, 3857), bounds.merc, 4096, 64, true
            ) AS geom,
            c.id::text AS id, c.camera_uid, c.current_status AS status,
            c.camera_type, c.ownership_class, c.department_id::text AS department_id
        FROM cameras c, bounds
        WHERE c.is_active AND c.lifecycle_state = 'active'
          AND c.location && bounds.geog{predicates}
    ) AS tile
"""

_CLUSTER_TEMPLATE = """
    WITH bounds AS (
        SELECT ST_TileEnvelope(:z, :x, :y) AS merc,
               ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)::geography AS geog
    ),
    clustered AS (
        SELECT ST_SnapToGrid(ST_Transform(c.location::geometry, 3857), :cell, :cell) AS cell,
               count(*) AS camera_count,
               count(*) FILTER (WHERE c.current_status = 'offline') AS offline_count
        FROM cameras c, bounds
        WHERE c.is_active AND c.lifecycle_state = 'active'
          AND c.location && bounds.geog{predicates}
        GROUP BY 1
    )
    SELECT ST_AsMVT(tile, 'camera_clusters', 4096, 'geom') FROM (
        SELECT ST_AsMVTGeom(clustered.cell, bounds.merc, 4096, 64, true) AS geom,
               clustered.camera_count, clustered.offline_count
        FROM clustered, bounds
    ) AS tile
"""


class TileService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def cameras(self, z: int, x: int, y: int, filters: CameraFilter) -> bytes:
        predicates, params = _predicates(filters)
        params |= {"z": z, "x": x, "y": y}
        if z >= CLUSTER_ZOOM_THRESHOLD:
            sql = _POINT_TEMPLATE.format(predicates=predicates)
        else:
            params["cell"] = 40 * (2 * 20037508.34) / (256 * 2**z)
            sql = _CLUSTER_TEMPLATE.format(predicates=predicates)
        result = await self.session.execute(text(sql), params)
        return bytes(result.scalar_one() or b"")
```

The filter values are bound parameters; only the fixed clause strings are interpolated, so
this is not SQL injection. This is the only string-built SQL in the codebase — keep the two
halves strictly apart, and never append an f-string clause here.

**Every field `camera_filter` can produce must be handled.** A field that reaches the tile
endpoint and is silently dropped does not give a smaller result set — it gives a map showing
markers the table has already filtered out, which is the exact divergence this shared-filter
design exists to prevent. Each clause must mirror its counterpart in
`CameraRepository._apply` exactly.

- [ ] **Step 5: Pass the filter through the tile router**

In `app/api/v1/routers/tiles.py`, add `from app.api.v1.routers.cameras import camera_filter`
and `from app.schemas.filters import CameraFilter`, then add the parameter:

```python
    filters: CameraFilter = Depends(camera_filter),
```

and change the call to `await TileService(session).cameras(z, x, y, filters)`.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `pytest tests/api/test_search.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add app/api/v1/routers app/services/tiles.py tests/api/test_search.py
git commit -m "feat: spatial search endpoints and filter-aware vector tiles"
```

---

## Task 5: CSV export

**Files:**
- Create: `app/services/export.py`
- Modify: `app/api/v1/routers/cameras.py`
- Test: `tests/services/test_export.py`

- [ ] **Step 1: Write the failing test**

Create `tests/services/test_export.py`:

```python
import csv
import io

import pytest

from app.models.camera import Camera


@pytest.mark.asyncio
async def test_export_returns_csv_matching_the_filter(
    api_client, session, seeded_department
):
    session.add_all(
        [
            Camera(
                camera_uid="GJ-AMC-000001", department_id=seeded_department,
                external_camera_id="A-1", location="SRID=4326;POINT(72.5714 23.0225)",
                current_status="online", name="Nehru Bridge",
            ),
            Camera(
                camera_uid="GJ-AMC-000002", department_id=seeded_department,
                external_camera_id="A-2", location="SRID=4326;POINT(72.58 23.03)",
                current_status="offline", name="Ashram Road",
            ),
        ]
    )
    await session.commit()

    response = await api_client.get("/api/v1/cameras/export.csv?statuses=offline")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 1
    assert rows[0]["camera_uid"] == "GJ-AMC-000002"
    assert rows[0]["latitude"] == "23.03"


@pytest.mark.asyncio
async def test_export_of_an_empty_result_still_has_a_header_row(
    api_client, seeded_department
):
    response = await api_client.get("/api/v1/cameras/export.csv")
    assert response.status_code == 200
    assert response.text.splitlines()[0].startswith("camera_uid,")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/services/test_export.py -v`
Expected: FAIL — 404

- [ ] **Step 3: Create `app/services/export.py`**

```python
import csv
import io
from collections.abc import Iterable

from geoalchemy2.shape import to_shape

from app.models.camera import Camera

COLUMNS = [
    "camera_uid", "external_camera_id", "name", "latitude", "longitude", "address",
    "camera_type", "camera_technology", "current_status", "status_since",
    "connectivity", "ownership_class", "site_type", "resolution", "retention_days",
    "amc_vendor", "amc_expiry_date", "install_date",
]


def cameras_to_csv(cameras: Iterable[Camera]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for camera in cameras:
        point = to_shape(camera.location)
        row = {column: getattr(camera, column, None) for column in COLUMNS}
        row["latitude"] = point.y
        row["longitude"] = point.x
        writer.writerow(row)
    return buffer.getvalue()
```

- [ ] **Step 4: Add the export route**

In `app/api/v1/routers/cameras.py`, declared before `/{camera_id}`:

```python
@router.get("/export.csv", summary="Export the filtered result set as CSV")
async def export_csv(
    filters: CameraFilter = Depends(camera_filter),
    session: AsyncSession = Depends(get_session),
) -> Response:
    from app.services.export import cameras_to_csv

    rows = await CameraRepository(session).list(filters, limit=100_000, offset=0)
    return Response(
        content=cameras_to_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="cameras.csv"'},
    )
```

Add `from fastapi import Response` to the imports.

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `pytest tests/services/test_export.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add app/services/export.py app/api/v1/routers/cameras.py tests/services/test_export.py
git commit -m "feat: filtered CSV export"
```

---

## Task 6: Filter panel, layer toggles and detail drawer

**Files:**
- Create: `web/lib/filters.ts`, `web/components/FilterPanel.tsx`, `web/components/CameraDrawer.tsx`
- Modify: `web/components/CameraMap.tsx`

- [ ] **Step 1: Create `web/lib/filters.ts`**

```ts
export type Filters = {
  statuses: string[];
  cameraTypes: string[];
  departmentIds: string[];
  q: string;
};

export const EMPTY_FILTERS: Filters = {
  statuses: [],
  cameraTypes: [],
  departmentIds: [],
  q: "",
};

export function toQueryString(filters: Filters): string {
  const params = new URLSearchParams();
  filters.statuses.forEach((s) => params.append("statuses", s));
  filters.cameraTypes.forEach((t) => params.append("camera_types", t));
  filters.departmentIds.forEach((d) => params.append("department_ids", d));
  if (filters.q) params.set("q", filters.q);
  return params.toString();
}
```

- [ ] **Step 2: Create `web/components/FilterPanel.tsx`**

```tsx
"use client";

import { Filters } from "@/lib/filters";

const STATUSES = ["online", "offline", "unknown", "maintenance"];
const TYPES = ["fixed", "ptz", "dome", "bullet", "anpr", "thermal"];

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function FilterPanel({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (next: Filters) => void;
}) {
  return (
    <div className="absolute left-4 top-4 z-10 w-72 rounded-lg bg-white/95 p-4 shadow-lg backdrop-blur">
      <input
        className="mb-3 w-full rounded border px-2 py-1 text-sm"
        placeholder="Search uid, name, address…"
        value={filters.q}
        onChange={(e) => onChange({ ...filters, q: e.target.value })}
      />

      <p className="mb-1 text-xs font-semibold uppercase text-slate-500">Status</p>
      <div className="mb-3 flex flex-wrap gap-1">
        {STATUSES.map((status) => (
          <button
            key={status}
            onClick={() => onChange({ ...filters, statuses: toggle(filters.statuses, status) })}
            className={`rounded px-2 py-1 text-xs ${
              filters.statuses.includes(status)
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-700"
            }`}
          >
            {status}
          </button>
        ))}
      </div>

      <p className="mb-1 text-xs font-semibold uppercase text-slate-500">Camera type</p>
      <div className="flex flex-wrap gap-1">
        {TYPES.map((type) => (
          <button
            key={type}
            onClick={() =>
              onChange({ ...filters, cameraTypes: toggle(filters.cameraTypes, type) })
            }
            className={`rounded px-2 py-1 text-xs ${
              filters.cameraTypes.includes(type)
                ? "bg-slate-800 text-white"
                : "bg-slate-100 text-slate-700"
            }`}
          >
            {type}
          </button>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `web/components/CameraDrawer.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type StreamEndpoint = {
  protocol: string;
  url: string;
  reachability: string;
  requires_auth: boolean;
};

export function CameraDrawer({
  camera,
  onClose,
}: {
  // Pass the clicked tile feature's properties, not just the id: a heading reading
  // "GJ-SEN-000002 · PTZ · OFFLINE" is legible in a demo, a raw UUID is not.
  camera: { id: string; camera_uid: string; status: string; camera_type: string } | null;
  onClose: () => void;
}) {
  const cameraId = camera?.id ?? null;
  const [streams, setStreams] = useState<StreamEndpoint[]>([]);

  useEffect(() => {
    if (!cameraId) return;
    // Do not reset state at the top of the effect: react-hooks/set-state-in-effect
    // rejects it, and tagging the response with its request id is a stronger guard
    // against a slow reply for a previously-selected camera landing last.
    let active = true;
    fetch(`${API}/api/v1/cameras/${cameraId}/streams`)
      .then((r) => r.json())
      .then((data) => active && setStreams(data))
      .catch(() => active && setStreams([]));
    return () => {
      active = false;
    };
  }, [cameraId]);

  if (!cameraId) return null;

  return (
    <aside className="absolute right-0 top-0 z-20 h-full w-96 overflow-y-auto bg-white p-5 shadow-xl">
      <button onClick={onClose} className="mb-4 text-sm text-slate-500 hover:text-slate-900">
        ← Close
      </button>
      <h2 className="mb-1 font-mono text-lg font-semibold">{camera.camera_uid}</h2>
      <p className="mb-4 text-xs uppercase text-slate-500">
        {camera.camera_type} · {camera.status}
      </p>

      <h3 className="mb-2 text-xs font-semibold uppercase text-slate-500">
        Stream endpoints
      </h3>
      {streams.length === 0 ? (
        <p className="text-sm text-slate-400">No endpoints registered.</p>
      ) : (
        <ul className="space-y-2">
          {streams.map((s) => (
            <li key={s.url} className="rounded border p-2 text-xs">
              <div className="flex items-center justify-between">
                <span className="font-semibold uppercase">{s.protocol}</span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5">{s.reachability}</span>
              </div>
              <code className="mt-1 block break-all text-slate-600">{s.url}</code>
              {s.requires_auth && (
                <span className="mt-1 block text-amber-600">requires credentials</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
```

- [ ] **Step 4: Wire filters and the drawer into `web/components/CameraMap.tsx`**

Replace the component body so the tile URL rebuilds when filters change:

```tsx
"use client";

import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";

import { CameraDrawer } from "@/components/CameraDrawer";
import { FilterPanel } from "@/components/FilterPanel";
import { EMPTY_FILTERS, Filters, toQueryString } from "@/lib/filters";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function CameraMap() {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current || map.current) return;
    // ... identical map construction and addLayer calls from Plan 1 Task 11 ...
    // In the "camera-points" click handler, replace the popup with:
    //   setSelected(feature.properties.id as string);
  }, []);

  useEffect(() => {
    const instance = map.current;
    if (!instance?.isStyleLoaded()) return;
    const query = toQueryString(filters);
    const source = instance.getSource("cameras") as maplibregl.VectorTileSource | undefined;
    source?.setTiles([
      `${API}/api/v1/tiles/cameras/{z}/{x}/{y}.mvt${query ? `?${query}` : ""}`,
    ]);
  }, [filters]);

  return (
    <div className="relative h-screen w-full">
      <div ref={container} className="h-full w-full" />
      <FilterPanel filters={filters} onChange={setFilters} />
      <CameraDrawer cameraId={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
```

Keep the `map.current = new maplibregl.Map({...})` assignment and every `addSource` /
`addLayer` call exactly as written in Plan 1 Task 11, Step 2.

- [ ] **Step 5: Verify manually**

Run the stack, open `http://localhost:3000/map`, then:
1. Click "offline" — only red markers remain and the count drops
2. Type a camera uid in the search box — the map narrows
3. Click a marker — the drawer opens showing its three Sentinel endpoints with reachability
4. Clear filters — everything returns

- [ ] **Step 6: Commit**

```bash
git add web
git commit -m "feat: map filter panel, layer toggles and camera detail drawer"
```

---

## Task 7: Offline basemap

Insurance for the on-site Grand Finale. If OSM is unreachable, the map still renders.

**Files:**
- Create: `data/gujarat_basemap.pmtiles`
- Modify: `web/components/CameraMap.tsx`, `docker-compose.yml`

- [ ] **Step 1: Build a Gujarat PMTiles extract**

Run:
```bash
brew install protomaps/tap/pmtiles   # or download from github.com/protomaps/go-pmtiles
pmtiles extract https://build.protomaps.com/20260801.pmtiles data/gujarat_basemap.pmtiles \
  --bbox=68.0,20.0,74.6,24.8
ls -lh data/gujarat_basemap.pmtiles
```
Expected: a file of roughly 50–200 MB. If the dated build URL 404s, list available builds at
`https://build.protomaps.com/` and use the most recent.

- [ ] **Step 2: Serve it from the compose stack**

Add to `docker-compose.yml`:

```yaml
  basemap:
    image: protomaps/go-pmtiles:latest
    command: serve /data --cors="*"
    volumes: ["./data:/data"]
    ports: ["8080:8080"]
```

- [ ] **Step 3: Install the PMTiles protocol in the client**

Also bundle glyphs alongside the PMTiles basemap and point the style's `glyphs` key at
them, or every symbol layer silently renders nothing when the venue network is down.

Run: `cd web && npm install pmtiles`

- [ ] **Step 4: Switch the basemap source with a runtime fallback**

In `web/components/CameraMap.tsx`, above the component:

```tsx
import { Protocol } from "pmtiles";

const BASEMAP = process.env.NEXT_PUBLIC_BASEMAP ?? "osm";
```

Inside the map-construction effect, before `new maplibregl.Map(...)`:

```tsx
    const protocol = new Protocol();
    maplibregl.addProtocol("pmtiles", protocol.tile);
```

and choose the style:

```tsx
    const style: maplibregl.StyleSpecification =
      BASEMAP === "pmtiles"
        ? {
            version: 8,
            sources: {
              base: {
                type: "vector",
                url: "pmtiles://http://localhost:8080/gujarat_basemap",
                attribution: "© OpenStreetMap contributors, Protomaps",
              },
            },
            layers: [
              { id: "bg", type: "background", paint: { "background-color": "#f1f5f9" } },
              {
                id: "roads",
                type: "line",
                source: "base",
                "source-layer": "roads",
                paint: { "line-color": "#cbd5e1", "line-width": 1 },
              },
              {
                id: "boundaries",
                type: "line",
                source: "base",
                "source-layer": "boundaries",
                paint: { "line-color": "#94a3b8", "line-width": 0.8 },
              },
            ],
          }
        : {
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
          };
```

Pass `style` to the `Map` constructor in place of the inline object.

- [ ] **Step 5: Verify offline operation**

Run:
```bash
docker compose up -d basemap
NEXT_PUBLIC_BASEMAP=pmtiles npm run dev
```
Then disable your network adapter and reload `http://localhost:3000/map`.
Expected: roads and boundaries still render, and camera markers still load from the local API.

**Rehearse the demo in this mode.** A grey map on stage is unrecoverable.

- [ ] **Step 6: Commit**

```bash
echo "data/*.pmtiles filter=lfs diff=lfs merge=lfs -text" >> .gitattributes
git add .gitattributes docker-compose.yml web
git commit -m "feat: self-hosted PMTiles basemap for offline demo operation"
```

Do not commit the `.pmtiles` binary unless Git LFS is configured. Document the build command
in the README instead.

---

## Task 8: Departments CRUD and the adapter sync route

Two endpoints that the README, the onboarding guide and the demo script all depend on but
which no earlier task creates: registering a department, and triggering an adapter pull over
HTTP rather than from a Python shell.

**Files:**
- Create: `app/api/v1/routers/departments.py`
- Modify: `app/api/v1/routers/onboarding.py`, `app/api/v1/router.py`
- Test: `tests/api/test_departments_api.py`, `tests/api/test_adapter_sync.py`

- [ ] **Step 1: Write the failing departments test**

Create `tests/api/test_departments_api.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_create_and_list_a_department(api_client):
    created = await api_client.post(
        "/api/v1/departments",
        json={"code": "RTO", "name": "Regional Transport Office"},
    )
    assert created.status_code == 201
    assert created.json()["code"] == "RTO"

    listed = await api_client.get("/api/v1/departments")
    assert any(d["code"] == "RTO" for d in listed.json())


@pytest.mark.asyncio
async def test_duplicate_code_is_rejected(api_client):
    payload = {"code": "RTO", "name": "Regional Transport Office"}
    await api_client.post("/api/v1/departments", json=payload)
    duplicate = await api_client.post("/api/v1/departments", json=payload)
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_field_mapping_can_be_set_and_read_back(api_client):
    dept = (
        await api_client.post(
            "/api/v1/departments", json={"code": "RTO", "name": "RTO"}
        )
    ).json()

    config = {
        "column_map": {"veh_cam_id": "external_camera_id", "y": "latitude", "x": "longitude"},
        "value_maps": {"status": {"RUNNING": "online", "HALTED": "offline"}},
    }
    put = await api_client.put(
        f"/api/v1/departments/{dept['id']}/field-mappings", json={"config": config}
    )
    assert put.status_code == 200
    assert put.json()["version"] == 1

    fetched = await api_client.get(f"/api/v1/departments/{dept['id']}/field-mappings")
    assert fetched.json()["config"]["value_maps"]["status"]["RUNNING"] == "online"


@pytest.mark.asyncio
async def test_updating_a_mapping_creates_a_new_version(api_client):
    dept = (
        await api_client.post("/api/v1/departments", json={"code": "RTO", "name": "RTO"})
    ).json()

    for _ in range(2):
        await api_client.put(
            f"/api/v1/departments/{dept['id']}/field-mappings",
            json={"config": {"column_map": {"a": "external_camera_id"}}},
        )

    latest = await api_client.get(f"/api/v1/departments/{dept['id']}/field-mappings")
    assert latest.json()["version"] == 2
```

Versioning on update rather than overwrite is what makes an import reproducible — the
`import_job` records which mapping version it ran under.

- [ ] **Step 2: Run it to make sure it fails**

Run: `pytest tests/api/test_departments_api.py -v`
Expected: FAIL — 404 on `/api/v1/departments`

- [ ] **Step 3: Create `app/api/v1/routers/departments.py`**

```python
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.department import Department
from app.models.field_mapping import FieldMapping

router = APIRouter(prefix="/departments", tags=["departments"])


class DepartmentCreate(BaseModel):
    code: str = Field(max_length=16, examples=["RTO"])
    name: str = Field(max_length=200)
    dept_type: str = "government"
    contact_email: str | None = None


class DepartmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    dept_type: str
    is_active: bool


class FieldMappingWrite(BaseModel):
    config: dict[str, Any]


class FieldMappingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    department_id: UUID
    version: int
    config: dict[str, Any]
    is_active: bool


@router.post("", response_model=DepartmentRead, status_code=201)
async def create_department(
    payload: DepartmentCreate, session: AsyncSession = Depends(get_session)
) -> DepartmentRead:
    existing = (
        await session.execute(select(Department).where(Department.code == payload.code))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Department code {payload.code} already exists."
        )

    department = Department(**payload.model_dump())
    session.add(department)
    await session.commit()
    return DepartmentRead.model_validate(department)


@router.get("", response_model=list[DepartmentRead])
async def list_departments(
    session: AsyncSession = Depends(get_session),
) -> list[DepartmentRead]:
    rows = (
        (await session.execute(select(Department).order_by(Department.code)))
        .scalars()
        .all()
    )
    return [DepartmentRead.model_validate(r) for r in rows]


@router.get("/{department_id}/field-mappings", response_model=FieldMappingRead)
async def get_field_mapping(
    department_id: UUID, session: AsyncSession = Depends(get_session)
) -> FieldMappingRead:
    mapping = (
        await session.execute(
            select(FieldMapping)
            .where(FieldMapping.department_id == department_id)
            .order_by(FieldMapping.version.desc())
        )
    ).scalars().first()
    if mapping is None:
        raise HTTPException(status_code=404, detail="No field mapping configured.")
    return FieldMappingRead.model_validate(mapping)


@router.put("/{department_id}/field-mappings", response_model=FieldMappingRead)
async def put_field_mapping(
    department_id: UUID,
    payload: FieldMappingWrite,
    session: AsyncSession = Depends(get_session),
) -> FieldMappingRead:
    if await session.get(Department, department_id) is None:
        raise HTTPException(status_code=404, detail="Department not found")

    # New version rather than overwrite, so a past import can be reproduced exactly.
    current_max = (
        await session.execute(
            select(func.coalesce(func.max(FieldMapping.version), 0)).where(
                FieldMapping.department_id == department_id
            )
        )
    ).scalar_one()

    mapping = FieldMapping(
        department_id=department_id, version=current_max + 1, config=payload.config
    )
    session.add(mapping)
    await session.commit()
    return FieldMappingRead.model_validate(mapping)
```

- [ ] **Step 4: Write the failing adapter-sync test**

Create `tests/api/test_adapter_sync.py`:

```python
import httpx
import pytest
from sqlalchemy import select

from app.models.camera import Camera

CATALOGUE = [
    {
        "id": "cam04", "name": "Nehru Bridge", "lat": 23.0225, "lon": 72.5714,
        "codec": "h264", "resolution": "1920x1080", "live": True,
        "hls": "https://cctv.corp8.cloud/cam04/index.m3u8",
        "rtsp": "rtsp://103.250.160.189:8554/stream/cam04",
        "whep": "http://103.250.160.189:8889/stream/cam04/whep",
    }
]


@pytest.fixture(autouse=True)
def mock_catalogue(monkeypatch):
    import app.api.v1.routers.onboarding as onboarding

    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=CATALOGUE))
    monkeypatch.setattr(onboarding, "_ADAPTER_TRANSPORT", transport)


@pytest.fixture
async def sentinel_department(session):
    from app.models.department import Department
    from app.models.field_mapping import FieldMapping

    dept = Department(code="SEN", name="Sentinel Sandbox")
    session.add(dept)
    await session.flush()
    session.add(
        FieldMapping(
            department_id=dept.id,
            version=1,
            config={
                "column_map": {
                    "id": "external_camera_id", "lat": "latitude",
                    "lon": "longitude", "name": "name",
                }
            },
        )
    )
    await session.commit()
    return dept


@pytest.mark.asyncio
async def test_sync_onboards_catalogue_cameras(api_client, session, sentinel_department):
    response = await api_client.post(
        f"/api/v1/onboarding/adapters/sentinel/sync?department_id={sentinel_department.id}"
    )
    assert response.status_code == 200
    assert response.json()["created"] == 1

    camera = (await session.execute(select(Camera))).scalar_one()
    assert camera.external_camera_id == "cam04"


@pytest.mark.asyncio
async def test_second_sync_is_idempotent(api_client, sentinel_department):
    url = f"/api/v1/onboarding/adapters/sentinel/sync?department_id={sentinel_department.id}"
    await api_client.post(url)
    second = (await api_client.post(url)).json()
    assert second["created"] == 0
    assert second["skipped"] == 1


@pytest.mark.asyncio
async def test_unknown_adapter_code_is_404(api_client, sentinel_department):
    response = await api_client.post(
        f"/api/v1/onboarding/adapters/nosuch/sync?department_id={sentinel_department.id}"
    )
    assert response.status_code == 404
```

- [ ] **Step 5: Run it to make sure it fails**

Run: `pytest tests/api/test_adapter_sync.py -v`
Expected: FAIL — 404 on the sync route

- [ ] **Step 6: Add the sync route to `app/api/v1/routers/onboarding.py`**

Add these imports and module-level values:

```python
import os

import httpx

from app.adapters.sentinel_adapter import SentinelAdapter

# Overridden in tests with an httpx.MockTransport.
_ADAPTER_TRANSPORT: httpx.BaseTransport | None = None

SENTINEL_CATALOGUE_URL = os.environ.get(
    "SENTINEL_CATALOGUE_URL", "https://cctv.corp8.cloud/cameras.json"
)
```

Then the route:

```python
@router.post(
    "/adapters/{adapter_code}/sync",
    response_model=IngestReport,
    summary="Pull a source catalogue and onboard it",
    description=(
        "Reads the source's catalogue and runs every entry through the same validation "
        "and normalization as a CSV upload. Idempotent: re-running produces no changes "
        "when nothing upstream has changed."
    ),
)
async def sync_adapter(
    adapter_code: str,
    department_id: UUID = Query(...),
    session: AsyncSession = Depends(get_session),
) -> IngestReport:
    if adapter_code != "sentinel":
        raise HTTPException(status_code=404, detail=f"Unknown adapter {adapter_code!r}")

    department = await _department(session, department_id)
    adapter = SentinelAdapter(
        catalogue_url=SENTINEL_CATALOGUE_URL,
        session_cookie=os.environ.get("SENTINEL_SESSION_COOKIE"),
        transport=_ADAPTER_TRANSPORT,
    )

    try:
        records = await adapter.fetch(department_id)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach the source catalogue: {exc}"
        ) from exc

    return await IngestionService(session).ingest(records, department, mode="commit")
```

A 502 rather than a 500 when the upstream catalogue is unreachable matters on demo day:
it tells you instantly whether the problem is your session cookie or your code.

- [ ] **Step 7: Register the departments router**

In `app/api/v1/router.py`, add `departments` to the existing import list and register it.
Only add the modules that exist — `boundaries`, `coverage` and `health` arrive in Plans 3–4:

```python
api_router.include_router(departments.router)
```

- [ ] **Step 8: Run the tests and make sure they pass**

Run: `pytest tests/api/test_departments_api.py tests/api/test_adapter_sync.py -v`
Expected: 7 passed

- [ ] **Step 9: Run it against the real sandbox**

Run:
```bash
export SENTINEL_SESSION_COOKIE="<copy from your browser session>"
curl -s -X POST "localhost:8000/api/v1/onboarding/adapters/sentinel/sync?department_id=$SEN" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```
Expected: around 30 created on the first run, then 30 skipped on the second.
**This is the demo's strongest single moment — rehearse it.**

- [ ] **Step 10: Commit**

```bash
git add app/api/v1/routers/departments.py app/api/v1/routers/onboarding.py app/api/v1/router.py tests/api
git commit -m "feat: department registration, versioned field mappings and adapter sync route"
```

---

## Self-review against the spec

**Covered:** §7 `admin_boundaries` and `stream_endpoints` persistence, §8 search/nearby/
within/export routes, §10 filtered tiles and PMTiles offline basemap, §14 pages 1 and 5
partially.

**Deferred:** health (Plan 3), coverage (Plan 4), auth and the `Principal` argument still
stubbed (Plan 5), webhooks and seeding (Plan 6).

**Known gap accepted deliberately:** `CameraRead.stream_endpoints` is returned empty by the
list endpoint — endpoints are fetched per camera via `/cameras/{id}/streams` to avoid an
N+1 join on a 500-row page. The detail endpoint populates it in Plan 5 when the drawer needs
a single-request payload.
