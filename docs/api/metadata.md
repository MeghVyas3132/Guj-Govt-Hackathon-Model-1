# Where camera metadata comes from

A source catalogue almost never carries what a registry needs. The Sentinel
sandbox is the worked example, and it is not an unusual one: it returns two
fields per camera.

```bash
curl -s https://cctv.corp8.cloud/cameras.json | head -3
```

```json
[
{"id":"cam01","name":"01 Chiman bhai Bridge"}
,{"id":"cam02","name":"02 Janpath"}
```

No coordinates, no codec, no resolution, no department, no status, no stream
URLs. The integrator's guide for that sandbox states that the catalogue returns
"location, codec, live status, stream properties, and all three URLs". It does
not. Planning around what a source *says* it provides is how a registry ends up
full of nulls.

So the registry derives what the source omits, from four places. Each tier is
independent: a camera that fails one still gets the others.

| Field | Derived from | Precision recorded as |
|---|---|---|
| `external_camera_id`, `name` | the catalogue | source |
| stream URLs | connector `endpoint_rules` templates | config |
| `codec`, `resolution`, `frame_rate` | the stream itself | measured |
| encryption, archive depth, live vs recorded | the HLS manifest | measured |
| `current_status`, `last_seen_at` | the health probe | measured |
| `location` | place name → district centroid | `district` |
| `location` (refined) | an operator, or a later import | `surveyed` |

---

## Tier 1 — the catalogue

Whatever the source actually sends. Read through the connector's
`field_mappings`, so a department that calls it `cam_no` and one that calls it
`device_id` both land in `external_camera_id` without either of them changing
their export.

Nothing is discarded. A field the registry has no column for is kept in
`metadata`, and a value it cannot classify is kept under
`metadata.unmapped_<dimension>` rather than being dropped.

---

## Tier 2 — the stream

This is the tier that answers "we have identifiers and nothing else".

```bash
curl -X POST localhost:8000/api/v1/cameras/$CAMERA_ID/enrich \
  -H "Authorization: Bearer $TOKEN"
```

```json
{
  "checked": 1,
  "updated": 1,
  "failed": 0,
  "results": [{
    "camera_id": "…",
    "external_camera_id": "cam01",
    "updated": true,
    "metadata": {
      "codec": "h264",
      "resolution": "1920x1080",
      "frame_rate": 30.0,
      "manifest": {
        "version": 6,
        "target_duration": 8.0,
        "playlist_type": "VOD",
        "segment_count": 7200,
        "total_duration_s": 43197.413,
        "encryption": "AES-128",
        "key_uri": "/enc.key",
        "is_live": false
      }
    }
  }]
}
```

Two tiers inside this one:

**The manifest.** Costs a few KB and no extra dependency. A master playlist
states resolution and codecs outright. A media playlist gives segmenting,
encryption, archive depth, and whether the feed is live or a recorded loop.

**One segment.** When the manifest is a media playlist — which is what the
Sentinel gateway serves — codec and resolution require decoding actual media.
This shells out to `ffprobe` and reads the first frame's parameters.

`ffprobe` is optional. Without `ffmpeg` installed the manifest tier still runs
and the media tier is skipped; nothing fails, and `codec` simply stays as
whatever the catalogue supplied.

In bulk:

```bash
curl -X POST "localhost:8000/api/v1/cameras/enrich?department_id=$DEPT&limit=100" \
  -H "Authorization: Bearer $TOKEN"
```

The same filter parameters as `GET /api/v1/cameras`, so what you see in the list
is what you enrich. `limit` is capped because each camera costs one manifest
fetch and one segment decode against the source.

**Enrichment never destroys.** A probe that fails leaves the previous values in
place. A gateway down for maintenance must not blank the codec that a successful
probe established last week, so only a measured value overwrites anything.

**Treat it as a background job, not an interactive one.** Measured against the
live sandbox, enrichment runs at roughly 16 seconds per camera: `ffprobe` has to
parse the playlist — 7,200 entries for a 12-hour archive — then fetch the
decryption key and one encrypted segment before it can report a frame. Four run
concurrently; more starves each one until they all time out. A department of
1,000 cameras is a scheduled overnight run, not a button someone waits on.

**A value that cannot be measured stays absent.** Where `ffprobe` reports a frame
rate of `0/0` the field is omitted rather than defaulted, because a fabricated 25
is worse than an honest gap — one of the 30 sandbox cameras does exactly this.

---

## Tier 3 — the health probe

Runs on a schedule and records `current_status`, `last_seen_at` and latency. It
follows no redirects: a 3xx means the session expired, which is a fact about the
registry's credentials rather than about the camera. Recording it as `offline`
would paint an entire fleet red the day a password changes.

---

## Tier 4 — position

Most sources send a place name, not a coordinate. The registry resolves the name
against `place_aliases` to a district and uses a representative point inside that
district's polygon.

This is recorded honestly. Every such camera carries:

```json
{
  "geocode_precision": "district",
  "geocode_matched_on": "17 Rajkot Bus Port CCTV",
  "geocode_district": "Rajkot"
}
```

The map, the camera detail page and every coverage run say so. A coverage
percentage computed over district-level points is arithmetically correct and
spatially meaningless, and the report states that rather than implying a
precision it does not have.

Supplying real coordinates later updates the row in place — matched on
`(department_id, external_camera_id)` — and clears the precision marker. No
re-onboarding, no duplicate.

---

## What this means for the sandbox

Against the live sandbox, 29 of 30 cameras resolve to a district from their name
alone, across 9 districts. Enrichment then establishes real per-camera technical
metadata:

| | cam01 | cam03 | cam06 | cam29 |
|---|---|---|---|---|
| codec | h264 | h264 | h264 | h264 |
| resolution | 1920×1080 | 1280×720 | 1920×1080 | 1280×960 |
| frame rate | 30 | 30 | 25 | 25 |
| archive | 12h | 12h | 24h | 8h |
| encryption | AES-128 | AES-128 | AES-128 | AES-128 |
| feed | recorded loop | recorded loop | recorded loop | recorded loop |

Two things worth knowing before building Models 2–4 on it: every stream is
AES-128 encrypted against `/enc.key`, and every one is
`EXT-X-PLAYLIST-TYPE:VOD` — a recorded loop, not a live feed.

### Reaching the sandbox

The login is a **form POST**, not the JSON the integrator's guide shows, and the
stream path is `/{id}/index.m3u8`, not `/hls/{id}/`:

```bash
SESSION=$(curl -s -i -X POST https://cctv.corp8.cloud/auth/login \
  --data-urlencode "password=$SANDBOX_PASSWORD" \
  | sed -n 's/.*sentinel=\([^;]*\).*/\1/p' | tr -d '\r')

curl -s -b "sentinel=$SESSION" https://cctv.corp8.cloud/cameras.json
curl -s -b "sentinel=$SESSION" https://cctv.corp8.cloud/cam01/index.m3u8
```

Both facts are encoded in `seeds/connectors.py` as connector configuration, not
in code. A gateway that changes either is a row edit.
