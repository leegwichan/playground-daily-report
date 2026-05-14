-- SQLite DDL — data/state.db 초기화에 사용
-- 모든 시각은 ISO 8601 (TEXT) 로 저장. 타임존은 UTC.

-- ─────────────────────────────────────────────────────────
-- events: collectors 가 적재. 같은 (source, id) 는 UPSERT.
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    id            TEXT NOT NULL,
    source        TEXT NOT NULL,        -- SourceType enum 값
    occurred_at   TEXT NOT NULL,
    collected_at  TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL,
    body          TEXT,
    url           TEXT,
    tags          TEXT NOT NULL DEFAULT '[]',   -- JSON array
    metadata      TEXT NOT NULL DEFAULT '{}',   -- JSON object
    PRIMARY KEY (source, id)
);

CREATE INDEX IF NOT EXISTS idx_events_occurred  ON events(occurred_at);
CREATE INDEX IF NOT EXISTS idx_events_source    ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_collected ON events(collected_at);


-- ─────────────────────────────────────────────────────────
-- reports: publishers 가 발송 후 적재. 재발송 차단에 사용.
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reports (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind                  TEXT NOT NULL,      -- daily | weekly | monthly | deep_dive
    period_start          TEXT NOT NULL,
    period_end            TEXT NOT NULL,
    title                 TEXT NOT NULL,
    payload_json          TEXT NOT NULL,      -- 전체 Report 직렬화
    discord_message_id    TEXT,
    discord_channel_label TEXT,
    published_at          TEXT NOT NULL,
    UNIQUE (kind, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_reports_period ON reports(kind, period_start);


-- ─────────────────────────────────────────────────────────
-- cs_concepts_covered: CS 보강에서 다룬 개념 추적
-- 같은 개념을 너무 자주 반복하지 않기 위함
-- tier 칼럼: 'primary' (사용자 실 스택) / 'interest' (학습 곁가지) — monthly 진도도 audit 의 source-of-truth
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cs_concepts_covered (
    concept           TEXT PRIMARY KEY,
    last_covered_at   TEXT NOT NULL,
    times_covered     INTEGER NOT NULL DEFAULT 1,
    last_report_id    INTEGER,
    tier              TEXT CHECK(tier IN ('primary','interest')) DEFAULT NULL,
    FOREIGN KEY (last_report_id) REFERENCES reports(id)
);


-- ─────────────────────────────────────────────────────────
-- collector_runs: 각 collector 의 마지막 성공 시각
-- 증분 수집(since=last_run)에 사용
-- ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS collector_runs (
    collector_name    TEXT PRIMARY KEY,       -- 'notion' | 'git' | ...
    last_success_at   TEXT NOT NULL,
    last_event_count  INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT
);
