-- =====================================================================
-- Real SQLite project contract.
--
-- This file is DOCUMENTATION + a runnable bootstrap. It is NOT an invented
-- Executing it on a fresh file gives the same shape as data/app.db.
-- =====================================================================
--
-- TIME FORMATS (deliberately NOT uniform -- this is the actual contract):
--   raw_posts.collected_at        ISO 8601 text, UTC  e.g. 2026-11-15T08:17:14.729852+00:00
--   clean_posts.created_at        ISO 8601 text, UTC  e.g. 2026-11-15T08:14:40+00:00
--   clean_posts.inserted_at       ISO 8601 text, UTC
--   sentiment_trends.time_bucket  ISO 8601 text, hour-truncated, UTC
--   sentiment_results.analyzed_at ISO 8601 text, UTC
--   trending_keywords.updated_at  REAL Unix epoch seconds (time.time())
--
-- Because ISO strings are fixed-width and always +00:00, lexicographic
-- comparison in SQL equals chronological comparison.
--
-- DISPLAY ONLY: the dashboard renders all times in Hong Kong Time (HKT, UTC+8)
-- via config.DISPLAY_TZ / dashboard.to_hkt() / db.iso(). Nothing in this file
-- changes: stored values stay UTC, and every SQL window filter stays +00:00.
--
-- WRITE OWNERSHIP / LOCKING:
--   SQLite has ONE writer at a time.
--   run_pipeline.py runs pipeline stages sequentially to avoid locking.
--   The dashboard opens mode=ro and never writes to this file.
-- =====================================================================

-- ---------------------------- Raw ingestion ------------------------------
CREATE TABLE IF NOT EXISTS raw_posts (
    post_id      TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    text         TEXT,
    source       TEXT NOT NULL,          -- fixed: 'hackernews'
    created_at   TEXT NOT NULL,          -- ISO 8601 UTC, from HN item.time
    collected_at TEXT NOT NULL,          -- ISO 8601 UTC, time of crawl
    url          TEXT NOT NULL
);

-- ---------------------------- Cleaned data -------------------------------
CREATE TABLE IF NOT EXISTS clean_posts (
    post_id      TEXT PRIMARY KEY REFERENCES raw_posts(post_id),
    title_clean  TEXT NOT NULL,          -- HTML stripped, URL removed, whitespace normalised
    text_clean   TEXT,
    tokens       TEXT,                   -- JSON array string, lowercase, stop-words removed
    created_at   TEXT NOT NULL,          -- ISO 8601 UTC (copied from raw_posts)
    inserted_at  TEXT NOT NULL           -- ISO 8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_clean_created ON clean_posts(created_at);

-- ---------------------------- Trend snapshot -----------------------------
-- IMPORTANT: this table is a *current snapshot*, not history.
-- trend.py DELETEs every row then bulk-inserts Top-20 every 60 seconds.
-- burst_score = freq(last 1h) / (freq(prior 23h)/23 + 1)
--   i.e. the baseline is converted to an hourly RATE before comparison, so a
--   word seen once for the first time no longer automatically scores 1.0 and
--   outranks genuinely repeated words. BURST_FORMULA=original restores
--   the raw `freq(1h)/(freq(24h)+1)`.
--   Candidates also need freq(last 1h) >= MIN_RECENT_COUNT (default 2).
--
-- Persist the raw counts too. The dashboard uses them to
-- show an honest sample-size note. trend.py ALTERs an existing 3-column table
-- automatically, so old databases keep working.
CREATE TABLE IF NOT EXISTS trending_keywords (
    keyword         TEXT,
    burst_score     REAL,
    updated_at      REAL,               -- Unix seconds, same for all rows in a cycle
    recent_count    INTEGER,            -- freq in the last 1 h
    baseline_count  INTEGER             -- freq in the prior 24 h (excluding last 1 h)
);

-- ---------------------------- Sentiment analysis -------------------------
CREATE TABLE IF NOT EXISTS sentiment_results (
    post_id         TEXT NOT NULL,
    entity          TEXT NOT NULL,
    positive_score  REAL NOT NULL,
    negative_score  REAL NOT NULL,
    neutral_score   REAL NOT NULL,
    compound_score  REAL NOT NULL,
    sentiment_label TEXT NOT NULL CHECK (sentiment_label IN
                                        ('positive', 'negative', 'neutral')),
    analyzed_at     TEXT NOT NULL,
    PRIMARY KEY (post_id, entity)
);

CREATE TABLE IF NOT EXISTS sentiment_trends (
    entity          TEXT NOT NULL,
    time_bucket     TEXT NOT NULL,      -- ISO 8601 UTC, hour truncated
    post_count      INTEGER NOT NULL,
    avg_compound    REAL NOT NULL,
    positive_count  INTEGER NOT NULL,
    negative_count  INTEGER NOT NULL,
    neutral_count   INTEGER NOT NULL,
    sentiment_shift TEXT NOT NULL CHECK (sentiment_shift IN
                                        ('positive', 'negative', 'stable')),
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (entity, time_bucket)
);
CREATE INDEX IF NOT EXISTS idx_sentiment_trends_time
    ON sentiment_trends(time_bucket);

-- =====================================================================
-- Historical trend snapshots are stored in a separate file
-- (data/history.db), created by db.init_history().
-- =====================================================================
-- CREATE TABLE IF NOT EXISTS trend_snapshots (
--     source_updated_at REAL NOT NULL,
--     keyword           TEXT NOT NULL,
--     burst_score       REAL NOT NULL,
--     rank_no           INTEGER,
--     captured_at       REAL NOT NULL,
--     PRIMARY KEY (source_updated_at, keyword));
-- CREATE TABLE IF NOT EXISTS kpi_snapshots (...);
-- CREATE TABLE IF NOT EXISTS run_log (...);
-- =====================================================================
