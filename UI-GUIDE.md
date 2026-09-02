# What every screen does

A walkthrough of the portal, page by page, in the order you would actually use
them. If you only read one section, read **Two ideas that explain most of the
UI** — nearly everything that looks unfamiliar follows from those two.

Portal: http://localhost:3000 · API docs: http://localhost:8000/docs

---

## Two ideas that explain most of the UI

### 1. Nothing about a camera is hardcoded

Camera types, statuses, connectivity, ownership, site types — none of these are
lists in the code. They are **rows in a database table** (`vocabulary_terms`)
that you edit from **Admin → Vocabulary**.

This is why the UI has an Admin page full of things that look like settings but
are really *the schema itself*. Gujarat has 26 departments and nobody knows what
they will send. If "camera type" were an enum in the code, the first department
to send `"thermal-ANPR-hybrid"` would need a developer and a deployment.

The consequence you will see: when the registry meets a value it does not
recognise, it **keeps it** rather than dropping it. The camera detail page will
say *"Values this registry did not recognise were kept"* and show you the
original. You add one vocabulary row and it classifies correctly — nothing was
lost in between.

### 2. The source usually sends almost nothing

The organisers' own sandbox returns two fields per camera: an id and a name. No
location, no codec, no status. That is normal, not a failing of theirs.

So the registry **derives** the rest — from the stream, from a health probe, from
the camera's name. Several screens show a *precision* or a *provenance* marker
because of this. When you see **"Position is district-level, not surveyed"**,
that is the system refusing to present a guess as a measurement.

---

## Who sees what

Four roles. The navigation bar hides pages you cannot use, and the API enforces
the same rules independently — a typed URL still fails.

| Role | Can do | Sees in nav |
|---|---|---|
| **viewer** | read only | Map, Cameras, Health, Ageing |
| **analyst** | read, CSV export, run coverage | + Coverage |
| **dept_admin** | all the above, plus write — **but only to their own department** | + Onboarding |
| **super_admin** | everything, all departments | + Connectors, Alerts, Admin |

Demo accounts, all `@gujarat.gov.in`, password `Sentinel@2026`:
`root@` (super_admin), `mun.admin@` (dept_admin), `analyst@`, `viewer@`.

Sign in as each to watch the same pages change. That difference *is* the
"department-wise role-based access control" the problem statement asks for.

---

## The pages

### `/login`

Email and password. The token is held in browser storage and attached to every
request, including the map's tile requests.

### `/map` — the GIS view

The main screen. 80,000 cameras render because the map is **not** sending you
80,000 markers: PostGIS generates binary vector tiles, and below zoom 11 you see
clustered counts rather than individual points. Zoom past 11 and clusters become
cameras.

**Dot colour is status:** green online, red offline, amber maintenance, grey
unknown.

**The sidebar** is the index. A map of clustered dots is a picture, not a way to
find one camera among thousands — so the filters and a list of every match sit
beside the canvas. Click a row and the map flies to that camera and opens its
drawer. The list pages 50 at a time, because this is built for 80,000 cameras
and no panel should try to render them all.

The filters go to *the same endpoint the Cameras table uses*, so the map, the
list and the table can never disagree about what matches. The map also frames
itself on whatever the filter matches, rather than opening on a fixed viewport
that may contain nothing.

**The coverage overlay (bottom-left)** is off until you pick a run. Once you run
a coverage analysis on `/coverage`, it appears here as a selectable layer:
hexagons shaded by how much of each cell any camera can see. The legend is the
colour ramp itself.

**Click a camera** and a drawer opens with its identity, status, and the stream
URLs an analytics system would use.

### `/cameras` — the list

The same data as a sortable, filterable table. Two things worth knowing:

- **Export CSV** produces the current filter's results, not everything. It needs
  the `cameras:export` scope, which a viewer does not have — bulk extraction is
  its own permission, separate from reading.
- Clicking a row opens the detail page.

### `/cameras/[id]` — one camera

The deepest screen, and the one with the most unfamiliar parts:

- **Live preview** — plays the actual feed. See *Can we watch the cameras?*
  below, because there is something important about how this works.
- **Derived from the stream** — codec, resolution, frame rate, encryption, and
  whether the feed is live or a recorded loop. This panel is empty until you
  press **Read metadata from stream**, which goes and measures it.
- **Details** — identity, position, bearing, retention. *"not recorded — treated
  as omnidirectional"* under Bearing means nobody told us which way it points,
  so coverage assumes it sees in all directions (the conservative reading).
- **Stream endpoints** — every way to reach this camera, with a *reachability*
  label. `public_cdn` works from anywhere; `direct_ip` needs gateway ports open
  on your network. Models 2–4 pick by this label.
- **Health history** — every observation, so you can see *when* it went down.
- **Change history** — who changed what, and when. Every write is recorded in
  the same database transaction as the change itself, so the trail cannot claim
  something that was rolled back.

### `/cameras/new` — manual entry

One camera, by hand. Required: an external id and a position. Everything else is
optional and will be normalised through the same vocabulary the bulk paths use.

If you type a camera type nobody has configured, it is accepted and flagged
rather than rejected.

### `/onboarding` — bulk import

The vendor-onboarding screen, and a three-step wizard:

1. **Choose a department and a file.** CSV. Encoding and delimiter are detected,
   so a file exported from Excel on a Windows machine works — including
   semicolon-delimited and non-UTF-8 files, which are the common cases.
2. **Preview.** Nothing is written. You get a row-by-row report: what would be
   created, updated, skipped, or failed, and *why* for each failure. This runs
   the entire real pipeline — it is not a separate simplified check.
3. **Import.** Commits exactly what the preview showed.

**Re-importing the same file is safe.** Cameras are identified by
`(department, external id)`, so a second run reports everything as *skipped* and
writes nothing. This is what makes a nightly sync possible.

### `/connectors` — pulling from a vendor

The most conceptually unusual page, and the heart of the architecture.

A connector is **a database row that describes someone else's API**: its URL, how
to authenticate (cookie, header, bearer, basic, or none), where the camera list
sits inside the JSON response, which field is the id, and how to build stream
URLs. There is no vendor-specific code anywhere in this project.

Press **Sync** and the registry fetches their catalogue and onboards it through
the same pipeline as a CSV upload. The Sentinel sandbox is seeded as one of these
rows — it gets no special treatment.

Onboarding department 27 is adding a row here, not shipping a release.

### `/health` — what is broken

Cameras ranked by how long they have been down. A background worker probes the
streams every five minutes and records what came back.

One subtlety: if a probe gets *redirected*, that is recorded as **unknown**, not
offline. A redirect means our session expired — a fact about our credentials, not
about the camera. Recording it as offline would paint the whole fleet red the day
someone changes a password.

### `/coverage` — where nothing is watching

Pick a district, press run. The district is tiled into hexagons and each cell is
asked what fraction of it any camera can see.

You get **two numbers, and the gap between them is the point**:

- **Installed coverage** — what the fleet would cover if everything worked.
- **Effective coverage** — what it covers right now, excluding offline cameras.

For Bhavnagar: 4.28% installed, 2.72% effective. About a third of all coverage is
lost to *broken* equipment rather than *missing* equipment. Those are different
budget lines, and no other view separates them.

**Open full report** produces a standalone HTML document you can print or attach
to a paper. **View on map** takes the run to `/map` as an overlay.

If some cameras were positioned from a place name, a red notice appears saying so
— the totals hold, but the spatial distribution does not.

### `/ageing` — what is about to break

The other half of gap analysis. Cameras past their service life, out of their
maintenance contract or nearing expiry, and retaining less footage than policy.

**The three thresholds at the top are editable**, because replacement cycles
differ by department and procurement round. The report echoes whichever values
produced it, so a printed copy says what "ageing" meant when it was made.

**"Needs attention" is deliberately not the sum of the other figures.** A camera
can be past its service life *and* out of AMC *and* under-retaining — that is one
replacement, not three. Adding the columns would inflate the number you take to a
budget meeting.

**"No date"** is reported separately and never counted as "not old". Those are
the cameras nobody can plan around, and how many there are is itself a finding
about what the source systems send.

**Export CSV** gives the per-department table for a procurement spreadsheet.

### `/webhooks` — alerts (labelled "Alerts" in the nav)

Where the registry pushes a notification when a camera changes state, so nothing
has to poll it.

- **Events**: pick specific ones, or select none to receive everything — including
  event types added later.
- **Signing secret**: names a row in `credentials`, never the secret itself. A
  subscription without one is delivered unsigned and labelled as such.
- **Send test** delivers a synthetic payload so you can verify your endpoint
  before a real outage does it for you.
- **Delivery log** is on the same screen, because the only reason to open this
  page twice is to find out why an alert did not arrive. Every attempt is stored
  with its status code.

Alerts fire **only on a transition**, never per observation — otherwise a camera
that stays down would alert every five minutes and train you to ignore it.

A subscription that fails 20 times running is switched off automatically, so one
decommissioned endpoint does not slow every future alert. Resume clears it.

### `/admin` — four tabs

- **Vocabulary** — the controlled lists described at the top. Add a term here and
  previously-unrecognised values classify on the next import. One term per
  dimension can be the *fallback*, which is what unknown values normalise to.
  Camera-type terms also carry the coverage geometry (range, field of view), so
  a department that knows its PTZ units reach 400m edits a row here and coverage
  recalculates.
- **Place aliases** — name-to-district lookups the geocoder uses when a source
  sends a place name instead of coordinates. Each row records *how* the mapping
  was established, so a verified lookup stays distinct from a guess.
- **API keys** — for machines. A key carries its own scope set, independent of
  any user. This is how Models 2–4 authenticate. The key is shown **once**, at
  creation.
- **Audit** — every write to the registry, by whom, with before-and-after values.

---

## Can we watch the cameras?

**Yes — and there is one thing worth understanding about it.**

The camera detail page has a **Live preview** player. Press play and you see the
actual feed.

It cannot work the obvious way. The gateway sends no `Access-Control-Allow-Origin`
header, and its session cookie is `HttpOnly; Secure; SameSite=Lax`. That means a
browser on our origin **cannot fetch the stream and could not authenticate it
even if it could**. This is a hard browser rule, not a configuration we forgot.

So the registry relays it: the server holds the credential, fetches the manifest,
rewrites every URL inside it — including the AES-128 decryption key — to point
back at us, and re-serves everything same-origin with your normal login. The
player never talks to the gateway directly.

Three deliberate limits:

- **Preview only.** Video does not otherwise pass through the registry. It does
  not record, transcode, or restream. Model 1's job is metadata and asset
  visibility; continuous viewing is Model 2's.
- **It only relays that camera's own host.** The `target` parameter comes from
  the browser, so a crafted value could otherwise point at internal
  infrastructure. Anything off the camera's own stream host is refused.
- **It loads on demand.** A grid of cards each opening a video stream would pull
  megabytes nobody asked for, so playback starts when you press play.

Two facts the preview reveals about the sandbox: every stream is **AES-128
encrypted**, and every one is a **recorded 12-hour loop**, not a live feed.

---

## Things that look like errors and are not

| What you see | What it means |
|---|---|
| *"Position is district-level, not surveyed"* | Located from a place name. Real coordinates update it in place — no re-import. |
| *"Values this registry did not recognise were kept"* | An unknown vocabulary value was preserved, not dropped. Add a term in Admin. |
| *"not recorded — treated as omnidirectional"* | No bearing recorded, so coverage assumes it sees all directions. |
| Status **unknown** rather than offline | A probe was redirected — our session expired, which says nothing about the camera. |
| Re-import reports everything **skipped** | Correct. Idempotency working; nothing changed, so nothing was written. |
| Coverage of ~4% | Also correct. 80,000 cameras across 196,000 km² is genuinely sparse. That is the finding. |
| Enrichment takes ~16s per camera | Expected. It decodes real encrypted media. A fleet-scale run is an overnight job. |

---

## A five-minute tour

1. Sign in as `viewer@gujarat.gov.in` — note how few pages appear.
2. Sign in as `root@gujarat.gov.in` — everything appears.
3. `/map` — zoom from state level into Ahmedabad and watch clusters become
   cameras. Filter to `status = offline`.
4. `/connectors` — press **Sync** on the Sentinel connector. It onboards the
   organisers' 30 cameras. Press it again: nothing is created.
5. `/cameras` — search `cam01`, open it, press **Play preview**, then
   **Read metadata from stream**.
6. `/coverage` — run Bhavnagar at 250m. Compare installed against effective.
7. `/map` — turn on the coverage overlay you just produced.
8. `/ageing` — change the service life from 5 years to 8 and watch the figures
   move.
