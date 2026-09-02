# Deploying Model 1

What to provision, what to set, and what to hand the Model 2 team.

All the sizing below comes from measuring the running system, not from a rule of
thumb.

---

## 1. What it actually uses

Measured with the registry running, 30 cameras onboarded, worker probing:

| Process | Resident memory |
|---|---|
| FastAPI (uvicorn) | **55 MB** |
| arq worker | **29 MB** |
| Next.js server | **29 MB** |
| PostgreSQL 16 + PostGIS | **355 MB** |
| Redis | **9 MB** |
| **Total** | **≈ 480 MB** |

Disk:

| | |
|---|---|
| Database, 30 cameras + 3 coverage runs | 166 MB |
| — of which `coverage_cells` | 105 MB |
| Python virtualenv | 179 MB |
| Next.js production build | ~490 MB (dev build; a standalone build is far smaller) |

**The application is small. Sizing is driven by three other things**, in order:

1. **Coverage analysis** — the heaviest query. A 250 m run over one district is
   ~48,000 hexagons and ~38 MB of cells, and completes in about 6 seconds. It is
   bounded at 250,000 cells, so it cannot run away.
2. **The stream preview proxy** — real video passes through it. Each concurrent
   viewer is roughly **2 Mbps sustained**. Ten viewers is ~20 Mbps of egress.
3. **Enrichment** — `ffprobe` decoding one segment per camera. CPU-light,
   network-bound, and bounded at 4 concurrent.

---

## 2. What to provision

### For the hackathon and for Model 2 to integrate against

> **2 vCPU · 4 GB RAM · 40 GB SSD**

Comfortable, with room for coverage runs and a handful of preview viewers.
Any of: DigitalOcean `s-2vcpu-4gb`, AWS `t3.medium`, Azure `B2s`, GCP `e2-medium`.

**Minimum that works: 2 vCPU / 2 GB.** Below 2 GB, Postgres and a coverage run
will contend and the run is what suffers.

**Do not use a 1 vCPU box.** Coverage, `ffprobe` and the API all want CPU at the
same moment, and the symptom is timeouts that look like bugs.

### If you keep the 80,000-camera synthetic fleet

> **4 vCPU · 8 GB RAM · 80 GB SSD**

The extra RAM is for Postgres, not the app — give it `shared_buffers = 2GB` so
tile and coverage queries stay in memory.

### If many people will watch previews at once

Add bandwidth, not CPU. The proxy is a relay: it does not transcode. Ten
concurrent viewers ≈ 20 Mbps egress. If that becomes the point of the product,
it belongs in Model 2 with a real media server — this relay was built so an
operator can confirm one camera works, and was never load-tested for a wall of
them.

---

## 3. What must be set

These were hardcoded until now and would each have failed **only after
deploying**. All are read from the environment with a `SENTINEL_` prefix.

```bash
# Where the database and queue live
SENTINEL_DATABASE_URL=postgresql+asyncpg://sentinel:<pw>@db.internal:5432/sentinel
SENTINEL_REDIS_URL=redis://cache.internal:6379

# REQUIRED. Browser origins allowed to call this API -- the deployed portal, and
# any other model's browser client. Leave it at the default and the deployed
# frontend is refused by CORS with an error that looks like the API being down.
SENTINEL_CORS_ORIGINS=https://registry.example.gov.in,https://model2.example.gov.in

# REQUIRED on ephemeral storage. The RS256 signing key is generated on first use
# if absent, so a container without a persistent volume mints a new one on every
# restart -- invalidating every issued token and breaking the offline JWKS
# verification Models 2-4 depend on. Either supply the PEM:
SENTINEL_JWT_PRIVATE_KEY_PEM="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
# ...or point at a mounted volume:
SENTINEL_JWT_PRIVATE_KEY_PATH=/var/lib/sentinel/jwt_private.pem
```

Generate the key once and keep it:

```bash
openssl genpkey -algorithm RSA -pkcs8 -pkeyopt rsa_keygen_bits:2048 \
  -out jwt_private.pem && chmod 600 jwt_private.pem
```

Secrets for source gateways are **not** environment-only — they live in the
`credentials` table by reference, and any of them can be overridden by an
environment variable named after the ref in upper case
(`sentinel_session` → `SENTINEL_SESSION`). That is how you rotate a vendor
password without a database write.

---

## 4. What to install

```
Python 3.14          (asyncpg needs cp314 wheels; 3.13 also works)
Node 20+             (build only, if you serve the portal from the same box)
PostgreSQL 16 + PostGIS 3.4
Redis 7
ffmpeg               (ffprobe only -- optional; without it, stream enrichment
                      falls back to the manifest tier and nothing fails)
```

Postgres and Redis are already described in `docker-compose.yml`; for a real
deployment prefer a managed Postgres with the PostGIS extension enabled.

---

## 5. Bring it up

```bash
alembic upgrade head

python -m seeds.vocabulary       # controlled vocabularies
python -m seeds.boundaries       # 33 Gujarat districts
python -m seeds.departments      # 6 departments, each a different schema
python -m seeds.place_aliases    # place name -> district
python -m seeds.users            # demo accounts -- change these
python -m seeds.connectors       # the Sentinel sandbox, as a config row

uvicorn app.main:app --host 0.0.0.0 --port 8000
arq app.workers.tasks.WorkerSettings          # health probing, every 5 minutes
```

**The worker is not optional.** Without it every camera reads `unknown`, which is
indistinguishable from a fleet that is genuinely unreachable. Run it as its own
service alongside the API.

The portal:

```bash
cd web
NEXT_PUBLIC_API_URL=https://registry.example.gov.in npm run build
npm run start        # or serve the build behind the same reverse proxy
```

`NEXT_PUBLIC_API_URL` is baked in at build time, so it must be set **before**
`npm run build`, not at runtime.

---

## 6. Put it behind TLS

Terminate HTTPS at a reverse proxy. Two things depend on it:

- Browsers restrict what a page served over HTTP may do, and the deployed portal
  will be a different origin from the API.
- Session cookies and bearer tokens should not cross a network in clear.

```nginx
server {
    listen 443 ssl http2;
    server_name registry.example.gov.in;

    location /api/     { proxy_pass http://127.0.0.1:8000; }
    location /docs     { proxy_pass http://127.0.0.1:8000; }
    location /openapi.json { proxy_pass http://127.0.0.1:8000; }
    location /.well-known/ { proxy_pass http://127.0.0.1:8000; }
    location /         { proxy_pass http://127.0.0.1:3000; }

    # The preview proxy streams media. Buffering it here reintroduces exactly
    # the problem streaming was added to solve: the player abandons a fragment
    # whose first byte has not arrived within 10 seconds.
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

That `proxy_buffering off` line matters more than it looks. Buffering the
response makes nginx hold each segment whole before forwarding it, which pushes
time-to-first-byte back up to the full download time and the preview goes black.

---

## 7. What to hand the Model 2 team

Once it is up, they need four things:

| | |
|---|---|
| **Base URL** | `https://registry.example.gov.in` |
| **An API key** | Admin → API keys. Scoped, and shown once at creation. |
| **The spec** | `/docs` (interactive) and `/openapi.json` |
| **The notes** | [`MODEL-2-STREAMING.md`](MODEL-2-STREAMING.md) — everything measured about the gateway, and what this registry already solves |

They verify our tokens offline against `/.well-known/jwks.json`, which is why the
signing key must be stable across restarts.

Add their origin to `SENTINEL_CORS_ORIGINS` if their client calls this API from a
browser. A server-side client does not need it.

---

## 8. Before you call it live

```bash
curl -s https://registry.example.gov.in/healthz                    # {"status":"ok"}
curl -s https://registry.example.gov.in/.well-known/jwks.json      # one key
curl -s https://registry.example.gov.in/openapi.json | head -c 80  # the spec
```

Then, in a browser, sign in and check the map draws cameras and the health page
is not all `unknown`. Those two are what break first, and neither shows up in a
`curl` check — which is exactly how they were missed before.
