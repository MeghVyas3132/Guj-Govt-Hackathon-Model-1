# Gap analysis

The problem statement asks for gap analysis over "uncovered zones **and ageing
infrastructure**". Those are two different questions and they need two different
reports: coverage is a map question, ageing is a calendar question.

---

## Uncovered zones

A coverage run tessellates a district into hexagons and asks, per cell, what
fraction any camera can see.

```bash
curl -X POST localhost:8000/api/v1/coverage/runs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"district": "Bhavnagar", "hex_edge_m": 250}'
```

```json
{
  "id": "…",
  "hex_edge_m": 250,
  "total_cells": 46231,
  "camera_count": 1204,
  "online_camera_count": 764,
  "installed_coverage_pct": 4.28,
  "effective_coverage_pct": 2.72,
  "district_located_camera_count": 0
}
```

Two figures, and the gap between them is the point:

- **Installed** — what the fleet would cover if every camera worked.
- **Effective** — what it covers right now, excluding offline cameras.

The difference is coverage lost to outages rather than to absent hardware. For
Bhavnagar above, 1.56 percentage points of a 4.28% footprint — roughly a third of
all coverage — is not missing equipment but broken equipment. Those two problems
have very different budgets.

Each camera's footprint comes from its own `range_m`, `fov_deg` and `azimuth_deg`
where recorded. Where a bearing is not recorded the camera is treated as
omnidirectional, which is the conservative reading — and the defaults per camera
type come from `vocabulary_terms`, so a department that knows its PTZ units reach
400m changes a row rather than the code.

`district_located_camera_count` is the honesty check. Cameras positioned from a
place name sit at one representative point for the whole district, so the totals
hold but the spatial distribution does not. The report says so on its face rather
than implying a precision it does not have.

### As a map layer

```
GET /api/v1/coverage/runs/{run_id}/tiles/{z}/{x}/{y}.mvt
```

Vector tiles of the run's grid. Each cell carries `classification`,
`installed_fraction`, `effective_fraction` and `camera_count`, so a client can
shade by either measure without a second request. Selectable as an overlay on
`/map`. A completed run is immutable, so the tiles cache for a day.

### As a document

```
GET /api/v1/coverage/runs/{run_id}/report.html
```

A standalone HTML report — no external assets, prints correctly, and states its
own parameters and caveats. `docs/sample-gap-analysis-report.html` is one
generated from real data.

---

## Ageing infrastructure

The other half. What is about to stop working, rather than what is not there.

```bash
curl "localhost:8000/api/v1/lifecycle/ageing?service_life_years=5&amc_horizon_days=90" \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "generated_for": "2026-09-02",
  "thresholds": {
    "service_life_years": 5, "amc_horizon_days": 90, "min_retention_days": 30
  },
  "totals": {
    "cameras": 80049, "needs_attention": 14203,
    "past_service_life": 11840, "amc_expired": 3106,
    "amc_expiring_soon": 1522, "retention_below_policy": 2277,
    "unknown_install_date": 6611
  },
  "bands": [{"label": "Under 1 year", "count": 8123, "share": 10.15}, "…"],
  "departments": ["…"]
}
```

Available in the portal at `/ageing`, and as CSV for a procurement spreadsheet:

```
GET /api/v1/lifecycle/ageing.csv
```

### Reading it correctly

**`needs_attention` is not the sum of the categories.** A camera can be past its
service life *and* out of AMC *and* under-retaining. That is one replacement, not
three. The figure is a distinct count computed in SQL, precisely so the number
someone takes to a budget meeting is not inflated by double counting.

**Every threshold is a parameter, and the response echoes what it used.**
Replacement cycles differ by department and by procurement round, so the registry
does not encode one office's policy as its opinion. A printed report has to say
what "ageing" meant when it was produced.

**`unknown_install_date` is reported separately, never as "not old".** Those are
the cameras nobody can plan around, and the size of that number is itself a
finding about what the source systems are sending.

**Absent is not non-compliant.** A camera with no recorded `retention_days` is
not counted as a retention breach. Counting nulls as breaches would make the
report a measure of data entry rather than of infrastructure.

### The bands

Age bands partition every camera that has an `install_date` — none in two bands,
none in none — so they reconcile against the dated population. Cameras without a
date appear in no band, which is why the band counts and `totals.cameras` differ
by exactly `unknown_install_date`.

Shares are of the whole fleet, not of the dated subset, so a fleet where half the
records lack dates does not report misleadingly confident percentages.
