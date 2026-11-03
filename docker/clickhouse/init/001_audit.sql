-- Executed by the clickhouse container on first start (compose profile "clickhouse").
-- Same DDL as app.adapters.clickhouse.sink.DDL (also: make ch-migrate).
CREATE DATABASE IF NOT EXISTS kb;
CREATE TABLE IF NOT EXISTS kb.audit_runs
(
    created_at   DateTime64(3, 'UTC'),
    request_id   UUID,
    action       LowCardinality(String),
    status       LowCardinality(String),
    error        Nullable(String),
    duration_ms  UInt32,
    input        String,
    output       String
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(created_at)
ORDER BY (action, created_at)
TTL toDateTime(created_at) + INTERVAL 365 DAY;
