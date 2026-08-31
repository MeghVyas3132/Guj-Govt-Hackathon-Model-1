# Model 1 — Centralised CCTV Registry & GIS Foundation

**Design specification**
Gujarat Police Innovation Challenge 2026 · Sentinel Gujarat
Date: 2026-08-31 · Status: approved for planning

---

## 1. Context

The Challenge asks for an integrated video management and analytics platform unifying CCTV
across **26 government departments** (~80,000 cameras) that today run as independent,
fragmented systems on different vendors, protocols, VMS platforms, AMC contracts and
storage policies.

The framework defines four reference models plus a hybrid option. **Model 1 is compulsory
for every submission** (FAQ #12) and must be paired with at least one other model. Our team
builds Model 1 only; Models 2–4 are being built in parallel by other developers on the same
team. Model 1 is therefore both a deliverable and an internal dependency, and its API
contract is on the critical path for everyone else.

Model 1 is explicitly **metadata and registry only** — no centralised streaming, recording,
or video analytics (FAQ #14).

### Official Model 1 requirements

| # | Requirement |
|---|---|
| 1 | Bulk import, manual entry, and API-based camera onboarding |
| 2 | Interactive GIS mapping by department, camera type, status, coverage |
| 3 | Camera health and maintenance monitoring |
| 4 | Gap-analysis reporting for uncovered zones |
| 5 | Role-based search, filtering, export, metadata audit trails |

### Official deliverables

- Working registry portal with GIS map
- Bulk and manual onboarding demonstration
- Sample camera-metadata dataset
- Registry API documentation
- Gap-analysis report sample

### Where Model 1 scores

- **Evaluation area 1 — Successful Test Case:** onboarding ~50 heterogeneous cameras and
  visualising them on a GIS map is Model 1's job. Model 1 is directly on the scored path.
- **Evaluation area 6 — Scalability / PoC readiness (~80,000 cameras).**
- **Bonus (FAQ #38)** names four Model 1 features explicitly: *operational dashboards,
  automated alerts, health monitoring, and integration-ready APIs*, plus *enhanced
  cybersecurity, auditability and RBAC*.

---

## 2. Non-goals

Out of scope for this repository, deliberately:

- Live video streaming, transcoding, recording, or playback
- Computer vision of any kind — ANPR, face recognition, object detection
- Cross-camera vehicle tracking and route reconstruction (Models 2–4)
- Kubernetes / microservice decomposition
- Production-hardened auth (key rotation, HSM, SSO federation) — designed and documented,
  not implemented
- Real rate-limiting infrastructure — a tier field and middleware hook exist; the enforcing
  gateway is documented as the intended approach

---

## 3. Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Map client | **MapLibre GL JS** | Native MVT + WebGL. Leaflet needs the semi-abandoned VectorGrid plugin. Suggested stacks are explicitly non-binding (FAQ #25). |
| Tile serving | **PostGIS `ST_AsMVT`** | Server-side tiles are the only approach that holds at 80k. Doubles as a scalability talking point. |
| Coverage geometry | **Directional wedges + circles** | Fixed/bullet/ANPR cameras get an azimuth-based sector; PTZ/dome get a full circle. Circles-only reads as naive to a police jury. |
| Grid | **`ST_HexagonGrid`** (PostGIS 3.1+) | Built in. Avoids the `h3-pg` extension and a more complex container image. |
| Integration | **REST + webhooks** | Contract boundary, not a shared database. Avoids schema coupling to parallel teams. |
| Identity | **Platform-wide IdP, RS256 + JWKS** | Models 2–4 validate tokens offline against `/.well-known/jwks.json`, so Model 1 is not a runtime dependency for their auth. |
| Repo | **Standalone** | Independent CI and deploy; merged into a submission repo near the deadline. |
| Seed data | **80k synthetic + ~30 live sandbox** | Synthetic proves the scalability criterion; live sandbox proves real onboarding. |
| Background work | **arq + Redis** | Async imports, health probes, coverage runs, webhook delivery. Lighter than Celery, native asyncio. |

---

## 4. Architecture

Single repository, `docker compose up` brings up the whole system:
`postgres` (PostGIS 3.4) · `redis` · `api` (FastAPI) · `worker` (arq) · `web` (Next.js).

```
app/
  api/v1/routers/    cameras onboarding tiles health coverage
                     search auth admin webhooks departments
  services/          ingestion normalization coverage health
                     tiles export audit webhook_dispatch
  repositories/      SQLAlchemy data access + RBAC query scoping
  adapters/          base csv generic_rest sentinel_sandbox
  models/            SQLAlchemy ORM
  schemas/           Pydantic request/response
  core/              config security deps logging
  workers/           arq task definitions
alembic/
web/                 Next.js App Router frontend
seeds/               synthetic generator + boundary loader
```

Routers stay thin: parse, authorise, delegate, serialise. All business logic lives in
services. All SQL lives in repositories. RBAC is applied as a query filter inside
repositories, never as scattered conditionals in routers.

---

## 5. The ingestion pipeline

This is the core of the system and the concrete expression of *"connect to anything,
anywhere, anytime."* Every onboarding path — CSV row, web form, REST POST, adapter pull —
constructs the same `RawCameraRecord` and calls one function.

```
RawCameraRecord(payload, department_id, source_type, source_ref)
        |
        v  FieldMappingResolver     per-department config: their vocabulary -> canonical
NormalizedCameraDraft
        |
        v  Validator                pydantic + spatial rules
ValidationResult(ok | row errors | warnings)
        |
        v  Deduper                  (department_id, external_camera_id) -> insert | update
        v  Persister                write + audit_log + webhook event
IngestOutcome(created | updated | skipped | failed)
```

Signature:

```python
IngestionService.ingest(
    records: list[RawCameraRecord],
    department: Department,
    mode: Literal["validate_only", "commit"],
    actor: Principal,
) -> IngestReport
```

`mode="validate_only"` is what gives the **validate -> preview -> import** wizard for free:
the preview is a `validate_only` run whose per-row results are persisted to `import_rows`
and rendered in the UI. Committing re-runs the same pipeline under the same
`field_mapping_version`, so preview and import cannot diverge.

### Validation rules

- Required: `external_camera_id`, `latitude`, `longitude`
- Coordinates parse as decimal degrees or DMS (per department config)
- Coordinates fall inside the Gujarat bounding box — outside is an **error**;
  inside Gujarat but outside any known district boundary is a **warning**
- `external_camera_id` unique within the file *and* resolvable against existing rows
- Enum values resolve through `value_maps`; unresolved values become `unknown` + a warning
- `azimuth_deg` in [0, 360), `fov_deg` in (0, 360], `range_m` > 0

### Idempotency

Dedupe key is `(department_id, external_camera_id)`. Re-running an identical import
produces zero changes and zero audit noise: the persister diffs the normalized draft
against the stored row and skips writes when nothing changed. Imports carry an optional
`Idempotency-Key` header; a repeated key returns the original `IngestReport`.

### Reconciliation

Adapter syncs run in `sync` or `append` mode. In `sync` mode, a camera present in the
registry but absent from the source catalogue is marked `decommissioned` with an audit
entry — never hard-deleted. This matters because the Sentinel guide warns that
"camera ids and the set of available cameras can change."

---

## 6. Field mappings — the "anything" mechanism

One versioned JSONB config per department. Onboarding a new department is a config row,
not a code change.

```json
{
  "column_map": {
    "cam_id": "external_camera_id",
    "lat": "latitude",
    "lng": "longitude",
    "cam_kind": "camera_type"
  },
  "value_maps": {
    "status":      { "ACTIVE": "online", "1": "online", "DOWN": "offline", "AMC": "maintenance" },
    "camera_type": { "PTZ-DOME": "ptz", "BULLET": "fixed", "ANPR-CAM": "anpr" }
  },
  "defaults": { "connectivity": "unknown", "ownership_class": "government" },
  "coordinate_format": "decimal_degrees",
  "passthrough_to_metadata": true
}
```

Two deliberate rules:

1. **Unmapped columns are preserved, not dropped.** With `passthrough_to_metadata`, any
   source field without a canonical home lands in `cameras.metadata` JSONB. No data loss
   during onboarding, and the field can be promoted to a real column later.
2. **Unmapped values warn, never fail.** An unknown status word normalizes to `unknown` and
   raises a row *warning*. A department inventing a new vocabulary term must never break
   their nightly sync.

Configs are versioned. Every `import_job` records the `field_mapping_version` it ran under,
so an import is reproducible.

---

## 7. Data model

14 tables.

**`departments`** — `id, code, name, dept_type, contact_name, contact_email, contact_phone,
jurisdiction_geom (nullable), is_active`
Seeded with the five sandbox departments (FAQ #39): **Health, Police, GSRTC, Panchayat,
Municipal Corporation**, plus others to demonstrate the 26-department story.

**`field_mappings`** — `id, department_id, version, config JSONB, is_active, created_at, created_by`

**`cameras`** — the core record.

| Group | Columns |
|---|---|
| Identity | `id (uuid)`, `camera_uid` (human-readable, e.g. `GJ-AMC-000123`), `department_id` (owner), `operator_department_id`, `external_camera_id`, `name` |
| Location | `location GEOGRAPHY(POINT,4326)`, `address`, `district_id`, `taluka_id`, `ward`, `site_type` |
| Optics | `camera_type`, `camera_technology` (analog\|ip), `azimuth_deg`, `fov_deg`, `range_m`, `height_m`, `resolution`, `has_night_vision` |
| Infrastructure | `recorder_id`, `connectivity`, `storage_type`, `retention_days`, `ownership_class`, `amc_vendor`, `amc_expiry_date`, `install_date` |
| State | `current_status`, `status_since`, `last_seen_at`, `is_active`, `lifecycle_state` |
| Extensibility | `metadata JSONB` |
| Provenance | `source_type`, `field_mapping_version`, `created_at/by`, `updated_at/by` |

`UNIQUE (department_id, external_camera_id)`. GIST index on `location`. B-tree on
`(current_status, status_since)` so the offline dashboard is an index scan.
GIN on `metadata`.

Enums: `camera_type` (fixed, ptz, dome, bullet, anpr, thermal, other) ·
`connectivity` (fiber, 4g, 5g, wifi, lan, unknown) ·
`status` (online, offline, unknown, maintenance) ·
`ownership_class` (government, private, ppp) ·
`site_type` (traffic_junction, godown, pds_shop, rto_checkpoint, office, hospital,
bus_depot, border_checkpost, public_space, other).

`site_type` and `ownership_class` come straight from the FAQs: departments use cameras very
differently (Home: traffic and law-and-order; Food & Civil Supplies: godowns and PDS shops;
RTO: offices, testing tracks, checkpoints — FAQ #6), and private cameras from societies and
malls are explicitly in scope (FAQ #7).

**`recorders`** — `id, department_id, external_id, kind (dvr|nvr|encoder), name,
location, ip_address, channel_count, metadata`
Analog cameras have no IP of their own; they hang off a DVR channel. Without this, the
"both analog and IP" requirement (FAQ #4) is inexpressible.

**`stream_endpoints`** — `id, camera_id, protocol (rtsp|hls|whep|onvif|snapshot), url,
codec, resolution, is_primary, reachability (public_cdn|direct_ip|lan_only), requires_auth,
credential_ref, verified_at, last_probe_status`

The bridge to Models 2–4. The Sentinel sandbox proves why `reachability` matters: HLS is
served over a password-gated CDN and works on any network, while RTSP and WHEP are served
on a bare public IP and need ports 8554/8889 open. A client on a restricted network asks
the registry which endpoint to use rather than hardcoding one and failing.

`credential_ref` names a row in a secrets table — never an inline password. The API omits
credentials unless the principal holds the `streams:credentials` scope.

**`camera_health`** — `id, camera_id, status, observed_at, source (probe|catalogue|api|manual|import),
latency_ms, detail JSONB`. Index `(camera_id, observed_at DESC)`. Monthly partitioning is
designed but not implemented; documented as the growth path.

**`admin_boundaries`** — `id, level (district|taluka|ward), name, code, parent_id,
geom GEOGRAPHY(MULTIPOLYGON,4326), population (nullable)`. GIST index.

**`users`** — `id, email, full_name, password_hash, department_id, role, is_active`
**`api_keys`** — `id, department_id, name, key_prefix, key_hash, scopes[], rate_limit_tier,
last_used_at, expires_at, revoked_at`
**`audit_logs`** — `id, actor_type (user|api_key|system), actor_id, action, entity_type,
entity_id, before JSONB, after JSONB, ip, user_agent, at`
**`import_jobs`** — `id, department_id, source_type, filename, field_mapping_version, status,
total_rows, valid_rows, warning_rows, error_rows, created_count, updated_count,
skipped_count, created_by, created_at, finished_at`
**`import_rows`** — `id, job_id, row_number, raw JSONB, normalized JSONB, status, errors JSONB`
**`webhook_subscriptions`** — `id, department_id, url, secret, event_types[], is_active`
**`webhook_deliveries`** — outbox: `id, subscription_id, event_type, payload JSONB, attempts,
status, next_retry_at, last_error`
**`coverage_runs`** / **`coverage_cells`** — cached gap analysis. Recomputing across 80k
cameras per page load is not viable.

---

## 8. API surface (`/api/v1`)

```
POST   /auth/login                       -> access + refresh JWT
POST   /auth/refresh
GET    /auth/me
GET    /.well-known/jwks.json            public keys for Models 2-4

GET    /cameras                          filter, paginate, sort
POST   /cameras                          single onboard (same pipeline)
GET    /cameras/{id}
PATCH  /cameras/{id}
DELETE /cameras/{id}                     soft delete
GET    /cameras/{id}/streams             <- Models 2-4 entry point
GET    /cameras/{id}/health
GET    /cameras/{id}/audit
GET    /cameras/search                   full-text + structured
GET    /cameras/nearby                   ?lat=&lon=&radius_m=  ST_DWithin
GET    /cameras/within                   ?district_id= | ?geojson=
GET    /cameras/export.csv

POST   /onboarding/imports               upload CSV/XLSX -> job (async)
GET    /onboarding/imports/{id}          status + row-level results
POST   /onboarding/imports/{id}/commit   preview -> import
POST   /onboarding/bulk                  JSON array, API onboarding
POST   /onboarding/adapters/{code}/sync  pull from a configured source

GET    /tiles/cameras/{z}/{x}/{y}.mvt
GET    /tiles/coverage/{run_id}/{z}/{x}/{y}.mvt
GET    /tiles/boundaries/{z}/{x}/{y}.mvt

POST   /health/observations              batch health push (async)
GET    /health/offline                   sorted by downtime desc
GET    /health/summary

POST   /coverage/runs                    launch analysis (async)
GET    /coverage/runs/{id}
GET    /coverage/runs/{id}/report.html|.pdf

GET    /departments  POST /departments
GET    /departments/{id}/field-mappings  PUT ...
GET    /admin/api-keys  POST /admin/api-keys  DELETE ...
GET    /admin/audit-logs
GET    /webhooks  POST /webhooks
```

Versioned from day one. OpenAPI is auto-generated; every schema carries a description and
an example so the generated docs are usable as the deliverable.

---

## 9. Auth and RBAC

**Users** authenticate with email/password and receive an RS256 access token (15 min) plus a
refresh token. Public keys are published at `/.well-known/jwks.json` so Models 2–4 validate
tokens **offline** — Model 1 being down does not break their login.

**Integrations** authenticate with an API key (`sk_…`, argon2-hashed, prefix stored for
display) scoped to one department with a `rate_limit_tier`.

Both resolve to a single `Principal(actor_type, actor_id, department_id, role, scopes)`
injected as a FastAPI dependency and passed into repositories, which apply it as a query
filter.

| Role | Read | Write | Export | Admin |
|---|---|---|---|---|
| `super_admin` | all | all | yes | yes |
| `dept_admin` | all departments | own department only | yes | own department |
| `analyst` | all departments | none | yes | none |
| `viewer` | all departments | none | no | none |

**Read is statewide, write is department-scoped.** An analyst in Rajkot can see Surat's
cameras but cannot edit them — correct for a state policing platform, where the whole point
is removing departmental blind spots.

Rate limiting: tier recorded on the key, middleware hook present, enforcement documented as
a gateway concern rather than implemented. Key rotation: documented, not built.

---

## 10. GIS and tile serving

`GET /api/v1/tiles/cameras/{z}/{x}/{y}.mvt` built with
`ST_TileEnvelope` -> `ST_AsMVTGeom` -> `ST_AsMVT`.

- **z < 11** — grid-aggregated cluster features carrying `count` and a status breakdown
- **z >= 11** — individual camera features with the attributes the client needs to style

Filters (department, type, status, ownership) are query parameters that hash into the Redis
cache key. Separate layers for coverage cells and administrative boundaries.

**Offline basemap.** The Grand Finale is on-site in a government facility. If OSM tiles are
unreachable the map renders grey — on our single most important screen. A self-hosted
PMTiles basemap of Gujarat, served from the same compose stack, removes that dependency.

Spatial search: `ST_DWithin` on `geography` for radius queries (index-assisted),
`ST_Intersects` against `admin_boundaries` for district and taluka queries.

---

## 11. Health monitoring

Health observations arrive from four sources: adapter catalogue sync, batch push API,
manual update, and an active prober.

The prober ladder, cheapest first:

1. Read declared status from the source catalogue (`cameras.json`)
2. Verify the HLS manifest is actually serving (HTTP GET, cheap, works through the CDN)
3. `ffprobe` the RTSP endpoint (expensive, sampled)

For 80k cameras a full active probe is infeasible in a demo; we probe the ~30 real sandbox
cameras plus a sampled subset of synthetic ones, and document the fan-out design (worker
pool partitioned by department, staggered schedules, adaptive intervals by tier).

`camera_health` is the append-only truth. `cameras.current_status` and `status_since` are
denormalized and updated transactionally only on state *change*, which makes
"offline longest" an index scan and gives an accurate downtime clock. A state change emits
a `camera.status_changed` webhook.

Because the Sentinel feeds are supervised and restart, health monitoring can be demonstrated
against real government infrastructure rather than fixtures.

---

## 12. Gap analysis methodology

Stated plainly, because the honest version is more persuasive than a fake-precise one.

1. **AOI** — a district or taluka polygon, or a user-drawn bbox.
2. **Tessellate** with `ST_HexagonGrid(edge_m, aoi)`, default edge 100 m, configurable.
   Hexagons because centre-to-centre distance is uniform in every direction, which square
   grids do not give.
3. **Footprint per camera**, from configurable per-type defaults:
   - PTZ / dome -> full circle, `ST_Buffer(location, range_m)`, default 250 m
   - fixed / bullet / ANPR -> **sector wedge** spanning `azimuth ± fov/2` clipped to
     `range_m`, default 100 m / 90 deg
   - azimuth missing -> full circle, flagged *"assumed omnidirectional"* in the report
4. **Per cell:** `area(ST_Intersection(cell, ST_Union(footprints))) / area(cell)`
5. **Classify:** covered `>= 60%` · partial `20–60%` · gap `< 20%` (configurable)
6. **Aggregate** to district coverage percentage, ranked list of low-coverage zones,
   and per-department contribution.

### Installed vs effective coverage

The run is computed **twice** — once over all cameras (*installed coverage*) and once over
online cameras only (*effective coverage*). The delta answers a question a police officer
actually asks: *"how much of this district went dark because cameras are down?"* It falls
out of having health data and registry data in one system, which is the argument for Model 1
existing at all.

### Stated limitations

2D only; no terrain or building occlusion; nominal range rather than optics- and
lighting-derived; assumes the recorded bearing is accurate; treats a camera as either
covering a cell area or not, with no probability weighting. These are written into the
generated report, not hidden.

Runs execute as background jobs and cache into `coverage_cells`.

---

## 13. Webhooks

Events: `camera.created`, `camera.updated`, `camera.decommissioned`, `camera.status_changed`,
`import.completed`, `coverage.completed`.

Transactional outbox: the event row is written in the same transaction as the change, and a
worker delivers it with exponential backoff and an HMAC-SHA256 signature header. Semantics
documented as at-least-once with no cross-camera ordering guarantee, so consumers must be
idempotent.

---

## 14. Frontend

Next.js App Router. Priority order — the first four are the demo.

1. `/map` — MapLibre, layer toggles (department / type / status / ownership), filter panel,
   radius-search tool, district select, coverage overlay, camera detail drawer showing
   stream endpoints, health sparkline and audit history
2. `/onboarding/import` — upload -> detected mapping -> validation report with row-level
   errors -> commit
3. `/` — dashboard: totals by department and status, coverage headline, offline-longest
   table, recent imports
4. `/coverage` — pick AOI and parameters, run, view heatmap, read report, export
5. `/cameras` — table, search, filter, CSV export
6. `/cameras/new` — manual form with map picker and an azimuth compass control
7. `/cameras/[id]` — detail, health history, audit trail
8. `/health` — offline dashboard sorted by downtime
9. `/admin` — departments, field-mapping editor, users, API keys, audit log

---

## 15. Seed data

- **~80,000 synthetic cameras** across real Gujarat district geometry, weighted to
  population so cities cluster and rural talukas are sparse. Deliberately includes: a
  visibly under-covered taluka for the gap-analysis demo, a cluster of offline cameras for
  the health demo, mixed analog/IP, a set of private (society/mall) cameras, and cameras
  with expiring AMC contracts.
- **Five departments matching the sandbox** — Health, Police, GSRTC, Panchayat, Municipal
  Corporation — each with a *different* CSV schema and status vocabulary, so the
  `field_mappings` demo is real rather than illustrative.
- **~30 live sandbox cameras** pulled from `cameras.json` through the Sentinel adapter
  during the demo itself.

---

## 16. Risks and accepted corners

| Risk | Mitigation |
|---|---|
| Other devs blocked on our API | Publish OpenAPI + stub server + JWKS + sample JWT on day 1, before the database exists |
| Venue network blocks tiles | Self-hosted PMTiles Gujarat basemap in the compose stack |
| Sandbox catalogue shape unknown | Adapter is config-driven; a geocoding stage is added only if `cameras.json` lacks coordinates |
| Coverage run too slow at 80k | Precompute per district as a background job; cache in `coverage_cells`; cap AOI size |
| MVT slow at 80k | GIST index, `ST_AsMVTGeom` clipping, Redis tile cache, pre-aggregated low-zoom table |

**Accepted corners, to be stated openly in the HLD rather than hidden:**
rate limiting is a tier field plus a middleware hook, not an enforcing gateway; API key
rotation is designed, not built; `camera_health` partitioning is designed, not built;
active probing is sampled rather than exhaustive; there is no terrain occlusion in coverage.

---

## 17. Open questions

1. Does `cameras.json` carry lat/lon per camera, or only a place name? Determines whether
   the Sentinel adapter needs an offline Gujarat gazetteer geocoding stage.
2. Does the catalogue expose a department attribute per camera, or must the five departments
   be assigned manually across cam01–cam30?
