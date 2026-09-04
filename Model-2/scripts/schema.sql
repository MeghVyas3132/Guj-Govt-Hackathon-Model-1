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
    pts_ms           DOUBLE PRECISION,              -- position-in-segment (ms) from CAP_PROP_POS_MSEC; used to seek video player to the exact frame
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
    detection_id        UUID  NOT NULL REFERENCES detections(id),
    status              TEXT  NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'confirmed', 'dismissed')),
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
