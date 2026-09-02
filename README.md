# Sentinel CCTV Registry — Model 1

**Centralised CCTV Registry & GIS Foundation** for the Gujarat Police Innovation
Challenge 2026.

Metadata and asset visibility only: no video streaming, recording, or analytics.
Model 1 is the mandatory foundation that Models 2–4 query, and this repository
implements it so that it can actually be depended on — a camera registered here
carries everything another system needs to reach it.

---

## Quick start

```bash
docker compose up -d db redis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head

python -m seeds.vocabulary        # controlled vocabularies
python -m seeds.boundaries        # 33 Gujarat districts
python -m seeds.departments       # 6 departments, each with a different schema
python -m seeds.place_aliases     # place-name → district lookups
python -m seeds.users             # 4 demo accounts, one per role
python -m seeds.connectors        # the Sentinel sandbox, as a config row
python -m seeds.connectors    # then sync it: the 30 sandbox cameras, live

# Optional — 80,000 synthetic cameras inside real district polygons, for
# demonstrating map and coverage performance at state scale.
# python -m seeds.synthetic 80000

uvicorn app.main:app --port 8000
arq app.workers.tasks.WorkerSettings   # probes camera health every 5 minutes
cd web && npm install && npm run dev
```

| | |
|---|---|
| Portal | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Public keys | http://localhost:8000/.well-known/jwks.json |

Demo accounts, password `Sentinel@2026`: `root@`, `mun.admin@`, `analyst@`,
`viewer@` — all `gujarat.gov.in`. Sign in as each to see the same pages behave
differently.

---

## What it does

| Capability | Where |
|---|---|
| Bulk CSV import, validate → preview → import | `/onboarding` |
| Manual camera entry | `/cameras/new` |
| API onboarding for departmental systems | `POST /api/v1/onboarding/bulk` |
| Vendor onboarding by configuration | `/connectors` |
| GIS map with layer toggles and filters | `/map` |
| Radius and district spatial search | `GET /api/v1/cameras/nearby` |
| Health monitoring ranked by downtime | `/health` |
| Coverage gap analysis and report | `/coverage` |
| Coverage as a map overlay | `/map` |
| Ageing infrastructure and AMC expiry | `/ageing` |
| Metadata derived from the stream itself | `POST /api/v1/cameras/{id}/enrich` |
| Signed outbound alerts on camera state | `/webhooks` |
| Live camera preview, relayed and authenticated | `/cameras/{id}` |
| CSV export and per-camera change history | `/cameras` |
| Vocabulary, aliases, keys, audit trail | `/admin` |

**622 tests · 49 API paths · 17 tables · 10 migrations · 13 pages.**

### Documentation

| | |
|---|---|
| [Deploying it](DEPLOYMENT.md) | VM sizing, required settings, what to hand Model 2 |
| [What every screen does](UI-GUIDE.md) | The portal, page by page |
| [Architecture & workflow](ARCHITECTURE.md) | How it fits together, and where to optimise |
| [Notes for Model 2](MODEL-2-STREAMING.md) | Playing the sandbox feeds, and what we already provide |
| [High-Level Design](docs/HLD.md) | Architecture, data flow, integration methodology |
| [Onboarding a department](docs/api/onboarding-guide.md) | The four ways data gets in |
| [Where metadata comes from](docs/api/metadata.md) | Deriving what a source omits |
| [Gap analysis](docs/api/reports.md) | Coverage and ageing infrastructure |
| [Event subscriptions](docs/api/webhooks.md) | Signed alerts, and how to verify them |
| [OpenAPI spec](docs/api/openapi.json) | Generated, and drift-tested against the code |
| [Sample dataset](docs/sample-camera-dataset.csv) | The 30 sandbox cameras as onboarded, 24 columns, 10 districts |
| [Sample gap-analysis report](docs/sample-gap-analysis-report.html) | Generated from real data |

---

## The four ideas this is built on

### 1. One pipeline, whatever the source

CSV upload, the manual form, the REST endpoint and a vendor sync all construct the
same `RawCameraRecord` and call one function:

```
RawCameraRecord → FieldMappingResolver → [geocoding] → VocabularyService
                → CameraValidator → Deduper → Persist + audit
```

Consequences that are tested rather than asserted:

- **Idempotent** on `(department_id, external_camera_id)`. Re-running a nightly
  sync produces `skipped`, writes nothing, and adds no audit noise.
- **One bad row fails alone.** Its batch-mates still commit, and the row reports
  which field failed and why.
- `validate_only` writes nothing but reports what *would* happen, which is what
  makes the preview wizard possible without a second code path.

### 2. Configuration, not code

Onboarding a department that nobody anticipated should not require a deploy. So:

| What | Where it lives |
|---|---|
| A vendor's catalogue URL, auth scheme, JSON shape, stream protocols | `source_connectors` row |
| A department's column names and vocabulary | `field_mappings` row |
| Camera types, statuses, connectivity, site types | `vocabulary_terms` rows |
| Coverage geometry per camera type | columns on the `camera_type` term |
| Place names the geocoder resolves | `place_aliases` rows |
| Secrets | `credentials`, referenced by name, env override |

There is no vendor name anywhere in the application. `RestCatalogueAdapter` does
not know that "sentinel" exists, nor that HLS or RTSP exist — it does what the
connector row says. Auth is generic: cookie, header, bearer, basic or none.

### 3. Ask the thing itself

A source catalogue rarely carries what a registry needs — the organisers' own
sandbox returns an id and a name, and nothing else. So the registry derives the
rest rather than recording nulls: codec, resolution and frame rate by reading the
camera's own manifest and decoding one segment; position by resolving a place
name to a district and marking the result as district-level rather than
pretending it is surveyed.

Verified against the live sandbox: 29 of 30 cameras place to a district from
their name, and enrichment reports genuinely different hardware per camera —
1920×1080 at 30fps for one, 1280×960 at 25fps for another. See
[docs/api/metadata.md](docs/api/metadata.md).

### 4. Never silently lose what you were told

A camera type this registry has never seen is **recorded, not discarded**. It
normalises to the dimension's fallback so it stays queryable, the original text is
kept in `metadata.unmapped_camera_type`, and the row carries a warning. An
operator adds one row and re-imports; nothing was lost in between.

An unconfigured dimension accepts anything. A registry with no vocabulary loaded
must not null every controlled field it is handed — permissive when unconfigured,
strict once configured.

---

## For Models 2–4

```python
import httpx, jwt

# Verify our tokens offline: your login must not fail because we are restarting.
jwks = httpx.get("http://<registry>/.well-known/jwks.json").json()
key = jwt.PyJWK.from_dict(jwks["keys"][0]).key
claims = jwt.decode(token, key, algorithms=["RS256"], audience="sentinel-platform")

# How to reach a camera.
streams = httpx.get(
    f"http://<registry>/api/v1/cameras/{camera_id}/streams",
    headers={"X-API-Key": your_key},
).json()
```

Pick the endpoint whose `reachability` matches your network — `public_cdn` works
anywhere, `direct_ip` needs gateway ports open, `lan_only` does not leave the
site. Do not keep your own camera list; ask the registry.

---

## Access model

| Role | Read | Write | Export | Admin |
|---|---|---|---|---|
| `super_admin` | all | all | ✓ | ✓ |
| `dept_admin` | all departments | own department only | ✓ | — |
| `analyst` | all departments | — | ✓ | — |
| `viewer` | all departments | — | — | — |

**Read is statewide, write is departmental.** The platform exists to remove
departmental blind spots, so scoping reads would defeat its purpose. Holding
`cameras:write` is not sufficient to write into another department.

Roles are read from the user row rather than trusted from the token, so revoking
or demoting someone takes effect on their next request instead of when their
token happens to expire.

---

## Known limitations

Stated here rather than discovered later.

**Coverage analysis** is two-dimensional with no terrain or building occlusion, so
real coverage is *lower* than reported. Ranges are nominal per camera type, not
derived from optics or lighting. Cameras with no recorded bearing are treated as
omnidirectional, which *overstates* their contribution — the report counts them
and says so.

**District-level positions.** Sources that supply a place name and no coordinates
are resolved to the district's representative point and stamped
`geocode_precision: district`. Those cameras are real and their count is reliable,
but their coverage appears concentrated where no camera physically stands. The
report raises this prominently. Importing surveyed coordinates updates them in
place, because ingestion dedupes rather than duplicates.

**Coverage run size** is capped at 250,000 cells. A request over budget is refused
with the edge length that would work rather than left to appear hung.

**Health probing** covers 200 cameras per five-minute pass, ordered by staleness.
At 80,000 cameras that is a 33-hour rotation. Full coverage needs the partitioned
worker pool described in the HLD.

**Rate limiting** records a tier per API key and exposes a middleware hook, but no
limiter enforces it; enforcement belongs at an API gateway. **API key rotation** is
designed, not built — keys are created and revoked, not rolled. **Token storage**
is `localStorage`, appropriate for a cross-origin demonstration portal; behind a
single domain it should move to an httpOnly cookie, which is a change to one file.

**`camera_health` partitioning** by month is designed, not built.

**One sandbox camera (cam20, "Mohanpura") has no location** — the name matches no
Gujarat district and the geocoder declines rather than guessing. A camera visibly
missing is better than one confidently in the wrong district.

---

## Testing

```bash
pytest                                    # 296 tests
ruff check .
cd web && npx tsc --noEmit && npx eslint . && npm run build
```

Tests use testcontainers, so a real PostGIS instance is started per session —
spatial behaviour is verified against PostGIS rather than mocked.

---

## Documentation

- [Onboarding a department](docs/api/onboarding-guide.md)
- [OpenAPI specification](docs/api/openapi.json) — regenerate with
  `python -m scripts.export_openapi`; a test fails if it drifts
- [Design specification](docs/superpowers/specs/2026-08-31-cctv-registry-gis-design.md)
- [Sample gap-analysis report](docs/sample-gap-analysis-report.html)
