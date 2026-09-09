-- UNS_SILVER/postgres/init.sql
-- UNS Silver schema — versioned semantic catalog + normalized readings/events.
-- See docs/superpowers/specs/2026-09-05-uns-silver-design.md, Section 2.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS signal_catalog (
    id              BIGSERIAL PRIMARY KEY,
    topic           TEXT NOT NULL,
    signal_key      TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    unit            TEXT,
    data_type       TEXT,
    range_min       NUMERIC,
    range_max       NUMERIC,
    thresholds      JSONB,
    description     TEXT,
    source_version  TEXT,
    effective_since TIMESTAMPTZ NOT NULL,
    effective_until TIMESTAMPTZ,
    UNIQUE (topic, signal_key, effective_since)
);
CREATE INDEX IF NOT EXISTS idx_signal_catalog_active ON signal_catalog (topic, signal_key) WHERE effective_until IS NULL;

CREATE TABLE IF NOT EXISTS silver_readings (
    time          TIMESTAMPTZ NOT NULL,
    topic         TEXT NOT NULL,
    signal_key    TEXT NOT NULL,
    signal_type   TEXT NOT NULL,
    value_numeric NUMERIC,
    value_text    TEXT,
    quality       TEXT NOT NULL DEFAULT 'good'
);
SELECT create_hypertable('silver_readings', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_silver_readings_lookup ON silver_readings (topic, signal_key, time DESC);

CREATE TABLE IF NOT EXISTS silver_events (
    time        TIMESTAMPTZ NOT NULL,
    topic       TEXT NOT NULL,
    event_key   TEXT NOT NULL,
    payload     JSONB NOT NULL,
    signal_type TEXT NOT NULL DEFAULT 'unknown'
);
SELECT create_hypertable('silver_events', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_silver_events_lookup ON silver_events (topic, event_key, time DESC);

CREATE TABLE IF NOT EXISTS silver_latest_value (
    topic         TEXT NOT NULL,
    signal_key    TEXT NOT NULL,
    signal_type   TEXT NOT NULL,
    time          TIMESTAMPTZ NOT NULL,
    value_numeric NUMERIC,
    value_text    TEXT,
    PRIMARY KEY (topic, signal_key)
);

CREATE TABLE IF NOT EXISTS silver_ingest_state (
    id                INT PRIMARY KEY DEFAULT 1,
    last_processed_id BIGINT NOT NULL DEFAULT 0
);
-- Idempotent migration: the bronze scan pairs `id > last_processed_id` with a
-- `time >=` predicate so TimescaleDB can exclude old mqtt_messages chunks.
-- '0001-01-01' (matching db.py's own datetime.min fallback), not '-infinity':
-- psycopg's binary timestamptz loader raises DataError trying to load the
-- literal -infinity value into a Python datetime, which has no such value.
ALTER TABLE silver_ingest_state ADD COLUMN IF NOT EXISTS last_processed_time TIMESTAMPTZ NOT NULL DEFAULT '0001-01-01 00:00:00+00';
INSERT INTO silver_ingest_state (id, last_processed_id) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;

CREATE MATERIALIZED VIEW IF NOT EXISTS silver_readings_1m
WITH (timescaledb.continuous) AS
SELECT topic, signal_key, signal_type,
       time_bucket('1 minute', time) AS bucket,
       avg(value_numeric) AS avg_value,
       min(value_numeric) AS min_value,
       max(value_numeric) AS max_value,
       count(*)           AS sample_count
FROM silver_readings
GROUP BY topic, signal_key, signal_type, bucket
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS silver_readings_1h
WITH (timescaledb.continuous) AS
SELECT topic, signal_key, signal_type,
       time_bucket('1 hour', time) AS bucket,
       avg(value_numeric) AS avg_value,
       min(value_numeric) AS min_value,
       max(value_numeric) AS max_value,
       count(*)           AS sample_count
FROM silver_readings
GROUP BY topic, signal_key, signal_type, bucket
WITH NO DATA;
