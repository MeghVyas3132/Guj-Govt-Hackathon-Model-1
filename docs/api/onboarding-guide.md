# Onboarding a department

Four routes in, all through the same pipeline. Whichever you use, a camera gets
identical validation, vocabulary resolution and dedupe — the source changes
nothing about the rules.

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"root@gujarat.gov.in","password":"Sentinel@2026"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
AUTH="Authorization: Bearer $TOKEN"
```

---

## 1. Register the department

```bash
curl -X POST localhost:8000/api/v1/departments -H "$AUTH" \
  -H 'content-type: application/json' \
  -d '{"code":"RTO","name":"Regional Transport Office"}'
```

## 2. Describe their data shape

A `field_mappings` config translates their vocabulary into ours. Only
`column_map` is required.

```bash
curl -X PUT localhost:8000/api/v1/departments/$DEPT/field-mappings \
  -H "$AUTH" -H 'content-type: application/json' -d '{
  "config": {
    "column_map": {
      "veh_cam_id": "external_camera_id",
      "y": "latitude",
      "x": "longitude",
      "kind": "camera_type",
      "state": "status"
    },
    "value_maps": {
      "camera_type": { "NUMBERPLATE": "anpr", "PTZ-DOME": "ptz" },
      "status": { "RUNNING": "online", "HALTED": "offline" }
    },
    "defaults": { "site_type": "rto_checkpoint", "connectivity": "lan" },
    "coordinate_format": "decimal_degrees",
    "passthrough_to_metadata": true
  }
}'
```

**`value_maps` keys are canonical field names, not source column names.**
`column_map` runs first, so by the time values are translated `kind` is already
`camera_type`. This is the most common configuration mistake.

Three guarantees worth knowing:

- **Unmapped columns are kept.** With `passthrough_to_metadata` they land in
  `cameras.metadata` and can be promoted to real columns later. Nothing is
  dropped during onboarding.
- **Unmapped values warn, never fail.** A status word this registry has never
  seen normalises to the dimension's fallback, the original is preserved in
  `metadata.unmapped_status`, and the row carries a warning. A department
  inventing vocabulary cannot break its own nightly sync.
- **Mappings are versioned.** Updating creates version N+1 and the old version
  survives, so a past import remains reproducible.

If their coordinates are degrees-minutes-seconds, set
`"coordinate_format": "dms"`. Both `23 01 21.0 N` and `23°01'21.0" N` parse.

If they supply a place name and no coordinates at all, set
`"geocode_from": "name"`. The name is matched against the district boundaries and
the camera is placed at that district's representative point, stamped
`geocode_precision: district` so it is never mistaken for a surveyed position.
Add unknown place names at `POST /api/v1/boundaries/{id}/aliases`.

---

## 3a. Send a file

Validate first — nothing is written:

```bash
curl -X POST "localhost:8000/api/v1/onboarding/preview?department_id=$DEPT" \
  -H "$AUTH" -F "file=@cameras.csv"
```

```json
{ "total": 2, "created": 1, "failed": 1,
  "rows": [
    { "row_number": 2, "external_camera_id": "RTO-1", "outcome": "created" },
    { "row_number": 3, "outcome": "failed",
      "errors": [{ "code": "outside_gujarat", "field": "location",
                   "message": "Point (28.6139, 77.209) falls outside the Gujarat bounding box." }] }
  ] }
```

Then commit the same file:

```bash
curl -X POST "localhost:8000/api/v1/onboarding/import?department_id=$DEPT" \
  -H "$AUTH" -F "file=@cameras.csv"
```

Error codes you will see: `missing_required_field`, `invalid_coordinate`,
`outside_gujarat`, `invalid_integer`, `invalid_boolean`, `invalid_date`.

## 3b. Post JSON

For a departmental system pushing directly:

```bash
curl -X POST "localhost:8000/api/v1/onboarding/bulk?department_id=$DEPT" \
  -H "$AUTH" -H 'content-type: application/json' \
  -d '[{"department_id":"'$DEPT'","external_camera_id":"RTO-1",
        "latitude":23.02,"longitude":72.57}]'
```

## 3c. Enter one by hand

`POST /api/v1/cameras`, or the form at `/cameras/new`.

## 3d. Let the registry pull from them

If they have a catalogue endpoint, describe it once and the registry fetches it —
**no code, no deploy**:

```bash
curl -X POST localhost:8000/api/v1/connectors/credentials -H "$AUTH" \
  -H 'content-type: application/json' \
  -d '{"name":"rto_key","value":"their-api-key"}'

curl -X POST localhost:8000/api/v1/connectors -H "$AUTH" \
  -H 'content-type: application/json' -d '{
  "code": "rto",
  "name": "RTO checkpoint catalogue",
  "department_id": "'$DEPT'",
  "config": {
    "catalogue_url": "https://rto.example/api/v2/devices",
    "auth": { "type": "header", "name": "X-API-Key", "credential_ref": "rto_key" },
    "root_path": "payload.devices",
    "id_keys": ["veh_cam_id"],
    "endpoint_rules": [
      { "protocol": "rtsp", "url_key": "rtsp_url",
        "url_template": "rtsp://rto.example:554/{id}",
        "reachability": "direct_ip", "is_primary": true }
    ]
  }
}'

curl -X POST localhost:8000/api/v1/connectors/rto/sync -H "$AUTH"
```

- `auth.type` — `none`, `cookie`, `header`, `bearer` or `basic`. Secrets are
  referenced by name and never inlined; config is readable by anyone with admin
  scope.
- `root_path` — dotted path to the camera array, or `null` for a bare array.
  Common wrappers (`cameras`, `items`, `data`, `results`) are found automatically.
- `endpoint_rules` — one per protocol. `url_key` reads the URL from the entry and
  always wins where present, because the source is authoritative. `url_template`
  builds it when the catalogue omits URLs, as the Sentinel sandbox does.
- The config is validated on save, so a malformed rule is rejected at write time
  rather than discovered mid-sync at 3am.

An unreachable catalogue returns **502**, not 500 — on demo day that tells you
immediately whether the problem is the credential or the code.

---

## Idempotency

Records dedupe on `(department_id, external_camera_id)`. Re-sending identical
data returns `skipped`, writes nothing and records no audit entry. Safe to run on
a cron.

Verified against the live government sandbox: 30 catalogue entries, 29 onboarded,
second sync created nothing.

---

## Adding a term the registry does not know

If a department runs a camera type nobody anticipated, the value is preserved on
import and can be classified afterwards without a deploy:

```bash
curl -X POST localhost:8000/api/v1/vocabulary/camera_type -H "$AUTH" \
  -H 'content-type: application/json' \
  -d '{"code":"fisheye-360","label":"Fisheye 360",
       "coverage_range_m":400,"is_omnidirectional":true}'
```

Re-import and the cameras reclassify. For `camera_type`, the coverage fields feed
the gap analysis directly, so the new type is modelled with its own geometry
immediately.

---

## Authentication

Humans log in at `POST /api/v1/auth/login`. Integrations use an API key:

```bash
curl -X POST localhost:8000/api/v1/admin/api-keys -H "$AUTH" \
  -H 'content-type: application/json' \
  -d '{"department_id":"'$DEPT'","name":"nightly sync",
       "scopes":["cameras:read","cameras:write"]}'
```

The key is returned once and never again — only its hash is stored. Send it as
`X-API-Key`. An API key's scopes are its own rather than its role's, so a
read-only integration cannot write.
