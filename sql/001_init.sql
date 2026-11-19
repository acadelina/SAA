-- Shifting Hulls storage schema.
-- Safe to run more than once: every object uses IF NOT EXISTS.

-- Every delivered Redis entry is kept here, keyed on the stream and the Redis
-- entry id. The payload hash is a SHA-256 of the full raw payload, before any
-- truncation, so distinct deliveries never collapse into one audit row.
CREATE TABLE IF NOT EXISTS raw_messages (
    stream TEXT NOT NULL,
    entry_id TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL,
    PRIMARY KEY (stream, entry_id)
);

CREATE TABLE IF NOT EXISTS clean_ais (
    message_id TEXT PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    mmsi TEXT NOT NULL,
    imo TEXT,
    name TEXT NOT NULL,
    flag TEXT NOT NULL,
    call_sign TEXT NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lon DOUBLE PRECISION NOT NULL,
    sog DOUBLE PRECISION NOT NULL,
    cog DOUBLE PRECISION NOT NULL,
    heading INTEGER NOT NULL,
    nav_status INTEGER NOT NULL,
    draft DOUBLE PRECISION,
    ship_type INTEGER NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clean_compliance (
    message_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    imo TEXT,
    name TEXT,
    mmsi TEXT,
    event_time TIMESTAMPTZ NOT NULL,
    publish_time TIMESTAMPTZ NOT NULL,
    note TEXT,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clean_results (
    message_id TEXT PRIMARY KEY,
    day INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    imo TEXT NOT NULL,
    mmsi TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reported_at TIMESTAMPTZ NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (day, plan_id, imo, mmsi)
);

-- message_id includes the rejection reason, so one payload can be quarantined
-- under different rules without the first row hiding the second.
-- payload is text, not jsonb: quarantine must keep values such as NaN that
-- standard JSON and jsonb refuse, and nothing may be silently dropped.
CREATE TABLE IF NOT EXISTS quarantine (
    stream TEXT NOT NULL,
    message_id TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (stream, message_id)
);

CREATE INDEX IF NOT EXISTS raw_messages_received_at_idx ON raw_messages (received_at);
CREATE INDEX IF NOT EXISTS clean_ais_mmsi_ts_idx ON clean_ais (mmsi, ts);
CREATE INDEX IF NOT EXISTS clean_ais_imo_idx ON clean_ais (imo);
CREATE INDEX IF NOT EXISTS clean_compliance_imo_idx ON clean_compliance (imo);
CREATE INDEX IF NOT EXISTS clean_compliance_publish_time_idx ON clean_compliance (publish_time);
CREATE INDEX IF NOT EXISTS clean_results_day_idx ON clean_results (day);
