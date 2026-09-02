# Notes for Model 2 — playing the Sentinel feeds

Everything below was measured against the live sandbox
(`cctv.corp8.cloud`), not inferred. Model 2 is the viewing and analytics layer,
so it will hit all of this; Model 1 already had to solve it to render a preview,
and this is what we learned.

**The headline:** the organisers' own Live Grid does not play its feeds. That is
not a broken backend — every request it makes returns 200. It is a client-side
load problem, and it is easy to reproduce and easy to avoid.

---

## 1. The gateway's five constraints

| Constraint | What it means for you |
|---|---|
| **No `Access-Control-Allow-Origin`** | A browser on any other origin cannot fetch the manifest. Not a setting you can talk them into — the request never leaves. |
| **Cookie is `HttpOnly; Secure; SameSite=Lax`** | JavaScript cannot read the session, and it is not sent cross-site. So you cannot authenticate from the browser either. |
| **`403 "browser required"`** on media | Any `User-Agent` without a `Mozilla/` prefix is refused — *on stream endpoints only*, while `/cameras.json` keeps working. It presents as a broken stream, not a rejected client. |
| **216 KB manifests, 7,200 entries** | A 12-hour archive. Cheap once; expensive thirty times. |
| **Throttles under concurrency** | One request ~1.4 s. Thirty at once: median 5.4 s, tail 17.5 s. |

Two more facts worth knowing before you design anything:

- Every stream is **AES-128 encrypted**, key at `/enc.key` — **one key shared by
  all cameras**, so fetch it once.
- Every stream is `#EXT-X-PLAYLIST-TYPE:VOD` — a **recorded 12-hour loop, not a
  live feed**. Do not build "live" language or wall-clock alerting on top of it
  without saying so.

---

## 2. Why their grid fails, measured

Their page does `.forEach(makeTile)` — it builds a player for **all 30 cameras
at once**. No `IntersectionObserver`, no staggering, no gating.

```
A) one manifest  (one player)     216 KB      1.4 s
B) all 30 at once (their grid)    5.3 MB      median 5.4 s, slowest 17.5 s
```

And that is *only* the manifests. Each player then wants the key and a first
segment of **268 KB to 2.7 MB**. Browsers cap ~6 connections per host, so 30
players queue behind 6 sockets.

**The actual killer:** `hls.js` defaults `manifestLoadingTimeOut` to **10
seconds**. Their tail manifest arrives at 17.5 s. Those players time out and
render nothing — which looks like a dead camera and is really a queued request.

---

## 3. What to do instead

### 3a. Load on demand, never all at once

The single highest-value change. A wall of tiles must not each open a stream.

```js
// Only start a player when its tile is actually on screen.
const io = new IntersectionObserver((entries) => {
  for (const e of entries) {
    if (e.isIntersecting) start(e.target);
    else stop(e.target);          // and tear down when it scrolls away
  }
}, { rootMargin: "200px" });
```

If you must show many at once, cap concurrent players (4–6 is realistic on this
gateway) and queue the rest.

### 3b. Raise the timeouts — the defaults are wrong for this gateway

```js
new Hls({
  manifestLoadPolicy: {
    default: {
      maxTimeToFirstByteMs: 20_000,   // hls.js default is 10_000
      maxLoadTimeMs: 30_000,
      timeoutRetry: { maxNumRetry: 1, retryDelayMs: 1000, maxRetryDelayMs: 2000 },
      errorRetry:   { maxNumRetry: 0, retryDelayMs: 0, maxRetryDelayMs: 0 },
    },
  },
  maxBufferLength: 12,     // a 12-hour archive will happily buffer forever
  maxMaxBufferLength: 30,
});
```

Retry **timeouts** but not **statuses**: a 404 or 403 is a settled fact, and
re-asking spends the one resource that is actually scarce here.

### 3c. You need a server-side relay — there is no way around it

Constraints 1 and 2 together mean the browser cannot fetch *or* authenticate the
stream. Something server-side must hold the credential and re-serve same-origin.

Model 1 already does this and you can just use it (§4). If you build your own,
the parts that are easy to get wrong:

- **Rewrite every URL in the manifest** — segments, variant playlists, **and the
  `#EXT-X-KEY` URI**. The key URI is absolute on this gateway; miss it and
  playback fails as a decrypt error that looks like a codec problem.
- **Send a `Mozilla/`-prefixed User-Agent.** We use a self-identifying one, not
  an impersonation, and verified it is accepted:
  `Mozilla/5.0 (compatible; SentinelRegistry/1.0; Gujarat Police Innovation Challenge)`
- **Do not follow redirects.** A 302 is the login page; following it hands the
  player HTML and presents as a corrupt stream.
- **Fix the content type.** The gateway serves `.ts` as
  `text/vnd.trolltech.linguist` — it has guessed they are Qt Linguist files. A
  player that trusts `Content-Type` refuses them. Decide from the extension.
- **Constrain what you will proxy.** If the target URL comes from the browser,
  confine it to the camera's own scheme+host+port, or you have built an SSRF
  gateway into your own network.

### 3d. For analytics, fetch a range, not a segment

If you are decoding frames server-side rather than playing in a browser: segment
sizes vary tenfold, and a decoder needs only the beginning to read codec,
resolution and frame rate. The gateway honours `Range`:

```
whole segment            200, 131 KB, timed out at 60 s
Range: bytes=0-393215    206, 393 KB, 7.1 s
```

Also: do **not** hand `ffprobe` the playlist URL. It then does its own
networking — the whole 216 KB playlist, then the key, then a segment — three
serial round-trips you cannot bound. Measured at **~29 s for one camera,
unloaded**. Fetch the bytes yourself and pipe them in; ffprobe on local bytes
takes milliseconds.

---

## 4. What Model 1 already gives you

Point at the registry instead of the gateway and most of the above disappears.

### Authenticate once, verify offline

```python
import httpx, jwt

jwks = httpx.get("http://<registry>/.well-known/jwks.json").json()
key = jwt.PyJWK.from_dict(jwks["keys"][0]).key
claims = jwt.decode(token, key, algorithms=["RS256"], audience="sentinel-platform")
```

RS256, so your service verifies our tokens without calling us — your login does
not fail because we are restarting. For service-to-service use an API key with
its own scopes (`X-API-Key`) rather than a user token.

Tokens last 15 minutes; `POST /api/v1/auth/refresh` renews. De-duplicate
concurrent refreshes behind one in-flight promise, or a burst of 401s rotates the
token out from under itself.

### Find cameras

```
# Note the exact names: `statuses` is plural and repeatable, and district is a
# UUID rather than a name. FastAPI ignores unknown query parameters silently, so
# `?district=Rajkot&status=online` returns the whole unfiltered set and looks
# like the filter did nothing.
GET /api/v1/cameras?statuses=online&limit=500
GET /api/v1/cameras?district_id=<uuid>&camera_types=ptz&camera_types=anpr
GET /api/v1/cameras?q=rajkot                    # free text over uid, name, address
GET /api/v1/cameras/{camera_id}/streams

# District ids come from:
GET /api/v1/boundaries?q=Rajkot          # case-insensitive substring
```

Use `q` rather than fetching the whole list and taking an index. Gujarat spells
several districts with a space (`Sabar Kantha`, `Panch Mahals`) and one without
an "e" (`Ahmadabad`), so name guessing fails quietly — we attributed a coverage
run to the wrong district exactly this way.

Repeatable filters (`statuses`, `camera_types`, `department_ids`,
`ownership_classes`) are OR-ed within a parameter and AND-ed across them. The map
tiles take the same parameters through the same dependency, so a tile and a list
can never disagree about what matches.

`streams` gives every way to reach a camera with a **reachability** label:
`public_cdn` works anywhere, `direct_ip` needs gateway ports open on your
network. Pick by that label, not by protocol preference.

Each endpoint also carries `codec` and `resolution` where we could measure them
— useful for sizing a decode pipeline before you open anything.

### Play without solving CORS yourself

```
GET /api/v1/cameras/{camera_id}/preview.m3u8
```

Returns the camera's manifest with every URL — segments, variant playlists and
the AES key — rewritten to come back through the registry, same-origin, under
your own bearer token. Attach it in `xhrSetup`:

```js
new Hls({ xhrSetup: (xhr) => xhr.setRequestHeader("Authorization", `Bearer ${token}`) })
  .loadSource(`${REGISTRY}/api/v1/cameras/${id}/preview.m3u8`);
```

**Caveat, stated plainly:** this is a *preview* relay, not a streaming tier. It
does not transcode, record, or fan out, and it was never load-tested for many
concurrent viewers. If Model 2 needs sustained multi-viewer playback, build a
proper media path — but you can develop and demo against this one today.

### Be told when a camera changes state

```
POST /api/v1/webhooks   { "events": ["camera.offline"], "secret_ref": "..." }
```

Signed HMAC-SHA256 with the timestamp inside the signed material. Fires on
transition only, never per observation. **No automatic retry** — treat it as a
low-latency hint and reconcile against
`GET /api/v1/cameras?status=offline` on a schedule.

---

## 5. When we merge

Things worth agreeing on early, because they are cheap now and expensive later:

- **`camera_uid` is the join key** (`GJ-POL-000123`). It is ours and stable. Do
  not key on the department's own id — those get renumbered.
- **Coordinates carry a precision marker.** Most cameras are placed from their
  *name* to a district representative point and carry
  `metadata.geocode_precision: "district"`. Do not plot those as if surveyed, and
  do not compute distances between two of them.
- **Vocabulary is data, not enums.** Camera types, statuses and connectivity are
  rows in `vocabulary_terms`. If you hardcode an enum, the first department that
  sends something new breaks your service and not ours.
- **We hold the registry; you hold the analytics.** Model 1 deliberately does not
  record or restream. If Model 2 needs a persistent media store, that is yours —
  tell us what metadata you want written back and we will add the fields.
- **Ask for a write path if you need one.** If analytics should update a camera
  (a better position, a detected resolution), we can expose it rather than have
  you write to our tables.

---

## 6. Quick reference — what the sandbox actually does

Corrections to the integrator's guide, all verified:

| The guide says | Reality |
|---|---|
| `cameras.json` returns location, codec, live status, stream properties, all three URLs | It returns `{"id","name"}`. Nothing else. |
| `curl http://<host>/api/ingest` | 404. That endpoint does not exist. |
| — | Login is a **form POST** (`password=…`), not JSON |
| — | Stream path is `/{id}/index.m3u8`, **not** `/hls/{id}/` |
| — | Feeds are recorded 12-hour loops, not live |

```bash
SESSION=$(curl -s -i -X POST https://cctv.corp8.cloud/auth/login \
  --data-urlencode "password=$SANDBOX_PASSWORD" \
  | sed -n 's/.*sentinel=\([^;]*\).*/\1/p' | tr -d '\r')

curl -s -b "sentinel=$SESSION" -H "User-Agent: Mozilla/5.0" \
  https://cctv.corp8.cloud/cam01/index.m3u8
```

Drop the `User-Agent` and you get `403 browser required`. That one cost us an
afternoon.
