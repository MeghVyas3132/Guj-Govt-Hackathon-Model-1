-- Setu — Temporary database schema for the ML worker
-- ─────────────────────────────────────────────────────────────────────────────
-- This schema is owned by the ML engineer for standalone development.
-- When backend creates Alembic migrations, they MUST match these column names
-- and types exactly. Flag any drift immediately.
--
-- Run against a fresh pgvector/pgvector:pg16 Postgres:
--   psql $DATABASE_URL -f schema.sql
-- ─────────────────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- ─── cameras ─────────────────────────────────────────────────────────────────
-- Populated from the Sentinel catalogue (cctv.corp8.cloud/cameras.json).
-- In production this is seeded from Model 1's registry via its API.
CREATE TABLE IF NOT EXISTS cameras (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id TEXT        UNIQUE NOT NULL,   -- "cam04" — matches Sentinel id
    name        TEXT,                          -- "04 Paldi Circle"
    lat         DOUBLE PRECISION,
    lng         DOUBLE PRECISION,
    ingest_url  TEXT,                          -- primary stream URL written at seed time
    status      TEXT        NOT NULL DEFAULT 'online',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── detections ──────────────────────────────────────────────────────────────
-- Highest-volume table. Every processed frame box produces one row.
CREATE TABLE IF NOT EXISTS detections (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id        UUID        NOT NULL REFERENCES cameras(id),
    track_id         INTEGER,                       -- ByteTrack per-camera-session ID
    class            TEXT        NOT NULL,          -- person | car | motorcycle | bus | truck
    bbox             JSONB       NOT NULL,          -- {x1, y1, x2, y2} pixel coords
    confidence       REAL        NOT NULL,          -- YOLO detection confidence
    plate_text       TEXT,                          -- NULL when unreadable — intentional
    plate_confidence REAL,
    embedding        vector(512),                   -- open_clip ViT-B/32, L2-normalised
    thumbnail_key    TEXT,                          -- MinIO object key — backend generates URLs
    ts               TIMESTAMPTZ NOT NULL,          -- wall-clock UTC at detection time
    pts_ms           DOUBLE PRECISION,              -- position within the segment; resets to 0 each segment
    archive_ms       DOUBLE PRECISION,              -- position in the 12h archive - the orderable timeline, and what a player seeks to
    dominant_color   TEXT,                          -- HSV-derived colour bucket: red | white | black | silver | blue | yellow | green | orange | unknown
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- HNSW index for fast cosine similarity search (cross-camera tracking + NL search).
-- HNSW over IVFFlat: no tuning needed, better recall at demo scale.
CREATE INDEX IF NOT EXISTS ix_detections_embedding
    ON detections USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS ix_detections_camera_ts
    ON detections (camera_id, ts DESC);

-- Partial index: plate lookups only scan rows where a plate was actually read.
CREATE INDEX IF NOT EXISTS ix_detections_plate_text
    ON detections (plate_text) WHERE plate_text IS NOT NULL;

-- ─── vehicle_tracks ──────────────────────────────────────────────────────────
-- One row per vehicle sighting, not per frame.
--
-- This is the table to search. `detections` holds per-frame boxes for drawing a
-- trajectory; everything expensive and everything trustworthy lives here,
-- because it is derived from a whole track rather than a single frame:
--
--   * embedding  - taken from the sharpest crop of the track, not an arbitrary one
--   * plate_text - voted across every read of the track, which is the only thing
--                  that recovers a plate from footage at this bitrate
--   * class, colour - majority across the track, so one bad frame cannot relabel
--                     an autorickshaw as a truck
--
-- Searching here also means "find the red hatchback" returns one row per vehicle
-- instead of forty near-identical rows of the same car.
CREATE TABLE IF NOT EXISTS vehicle_tracks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id       UUID NOT NULL REFERENCES cameras(id),
    track_id        INTEGER NOT NULL,          -- unique only within one camera session
    class           TEXT NOT NULL,
    dominant_color  TEXT,
    first_ms        DOUBLE PRECISION NOT NULL, -- archive offset of first sighting
    last_ms         DOUBLE PRECISION NOT NULL,
    n_observations  INTEGER NOT NULL,
    embedding       vector(512),

    plate_text      TEXT,
    plate_status    TEXT CHECK (plate_status IN ('exact', 'corrected', 'unvalidated')),
    plate_agreement REAL,                      -- how much the reads agreed, 0-1
    plate_votes     INTEGER,                   -- how many reads were fused

    thumbnail_key   TEXT,
    trajectory      JSONB,                     -- [[archive_ms, cx, cy], ...]
    ts              TIMESTAMPTZ NOT NULL,      -- wall-clock at ingest, telemetry only
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (camera_id, track_id, first_ms)
);

-- Vehicle search runs over tracks, so the HNSW index belongs here.
CREATE INDEX IF NOT EXISTS ix_tracks_embedding
    ON vehicle_tracks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS ix_tracks_camera_time
    ON vehicle_tracks (camera_id, first_ms DESC);

-- Only plates good enough to act on. A `corrected` or `unvalidated` plate must
-- never satisfy a lookup for a specific registration.
CREATE INDEX IF NOT EXISTS ix_tracks_plate_exact
    ON vehicle_tracks (plate_text) WHERE plate_status = 'exact';

-- ─── watchlist_entries ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS watchlist_entries (
    id                  UUID  PRIMARY KEY DEFAULT gen_random_uuid(),
    type                TEXT  NOT NULL CHECK (type IN ('plate', 'person_embedding')),
    value               TEXT,                          -- plate string, when type = 'plate'
    reference_embedding vector(512),                   -- when type = 'person_embedding'
    reason              TEXT  NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── alerts ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS alerts (
    id                  UUID  PRIMARY KEY DEFAULT gen_random_uuid(),
    watchlist_entry_id  UUID  NOT NULL REFERENCES watchlist_entries(id),
    -- An alert now normally comes from a finished track rather than a single
    -- frame, so exactly one of these is set. Alerting per frame meant one
    -- vehicle raised the same alert on every frame it was visible.
    detection_id        UUID  REFERENCES detections(id),
    vehicle_track_id    UUID  REFERENCES vehicle_tracks(id),
    status              TEXT  NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'confirmed', 'dismissed')),
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CHECK (detection_id IS NOT NULL OR vehicle_track_id IS NOT NULL)
);

-- Postgres NOTIFY on alert insert — backend WebSocket listener wakes on this.
CREATE OR REPLACE FUNCTION _notify_setu_alert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_notify('setu_alerts', NEW.id::text);
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tr_alert_notify ON alerts;
CREATE TRIGGER tr_alert_notify
    AFTER INSERT ON alerts
    FOR EACH ROW
    EXECUTE FUNCTION _notify_setu_alert();
