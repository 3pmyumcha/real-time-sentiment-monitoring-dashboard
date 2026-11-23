# -*- coding: utf-8 -*-
"""Real-time trend detection module (Member 3).

Data source: the clean_posts table in data/app.db (written by Member 2's pipeline).
Output target: the trending_keywords table (read by Member 5's dashboard).

Core logic:
1. Use the newest created_at in clean_posts as "now", so the detection does not
   depend on the local machine clock.
2. Count word frequencies in the last 1 hour and in the previous 24 hours
   (excluding the most recent hour).
3. Burst Score = frequency in the last hour / (frequency in the past 24 hours + 1);
   the +1 smooths the ratio and prevents division by zero.
4. Sort by score in descending order, keep the Top 20, clear trending_keywords,
   then bulk insert the new results.
5. Repeat every 60 seconds.

Member 5 (PM) quality fixes, 11/15/2026:
* a standard stop-word list is applied to tokens (the 18-word list in Member
  2's cleaner let `will / give / though / show / hn` top the chart);
* a minimum 1 h frequency threshold removes one-off hapax words. It is NOT
  relaxed to 1: when the sample is too small the chart honestly shows fewer
  keywords instead of padding it with noise;
* the baseline is RATE-NORMALISED. Member 3 divided a 1-hour count by a
  23-hour count, so every previously unseen word automatically scored 1.0
  and outranked words that were genuinely being discussed repeatedly. The
  default now compares like-for-like hourly rates. Set
  CS5481_BURST_FORMULA=original to reproduce Member 3's exact formula;
* ties are broken by real frequency, then alphabetically;
* recent_count / baseline_count are persisted: the brief explicitly asks
  Member 3 for "hot keyword frequency data", not just a ratio.
"""

import json
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from text_quality import filter_tokens
except ImportError:                                  # pragma: no cover
    def filter_tokens(words):
        return [w.strip().lower() for w in words
                if isinstance(w, str) and len(w.strip()) > 1]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Directory containing this script (the workspace root)
BASE_DIR = Path(__file__).resolve().parent


def find_db_path():
    """Locate app.db automatically: check common locations first, then recurse.

    Returns a Path, or None when no app.db exists anywhere in the workspace
    (the caller prints a hint instead of raising an exception).
    """
    # 1) Check the two most common locations: root and the data/ subdirectory
    for candidate in (BASE_DIR / "app.db", BASE_DIR / "data" / "app.db"):
        if candidate.is_file():
            return candidate

    # 2) Fallback: recursively search the workspace for the first app.db
    for found in BASE_DIR.rglob("app.db"):
        if found.is_file():
            return found

    return None


# Member 5 (PM) integration: an explicit shared path wins, so the detector
# never accidentally picks a stale app.db from the reference packages folder.
DB_PATH = Path(os.environ["CS5481_DB"]) if os.environ.get("CS5481_DB") else find_db_path()

RECENT_SECONDS = 3600          # window of the last 1 hour
BASELINE_SECONDS = 86400       # window of the past 24 hours
TOP_N = 20                     # keep the top 20 trending words
LOOP_INTERVAL = 60             # run a detection cycle every 60 seconds

# PM quality gate. Override with CS5481_MIN_RECENT_COUNT (default 2 because the
# delivered 1 h window only holds ~150 tokens; raise it as data accumulates).
MIN_RECENT_COUNT = max(1, int(os.environ.get("CS5481_MIN_RECENT_COUNT", "2")))
LOW_SAMPLE_BELOW = max(1, int(os.environ.get("CS5481_MIN_KEYWORDS", "8")))
# "rate" = compare hourly rates (default); "original" = Member 3's raw ratio.
BURST_FORMULA = os.environ.get("CS5481_BURST_FORMULA", "rate").strip().lower()
# Filled by detect_trending() each cycle; surfaced by the dashboard.
LAST_CYCLE_INFO = {"effective_min": MIN_RECENT_COUNT, "formula": BURST_FORMULA,
                   "n_candidates": 0, "n_recent_tokens": 0,
                   "low_sample": False}

# Fallback tokenizer: match runs of lowercase English letters only
WORD_PATTERN = re.compile(r"\b[a-z]+\b")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def connect_db():
    """Open a database connection.

    timeout=5 means waiting up to 5 seconds for a write lock;
    busy_timeout=5000 (milliseconds) is SQLite's native equivalent.
    Together they avoid a "database is locked" error while Member 2's
    pipeline writes to the same database.
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_trending_table(conn):
    """Create/upgrade the result table, so the dashboard can read it directly.

    Member 3's original DDL has 3 columns. The PM fix adds the raw frequency
    counts (the brief asks for hot-keyword frequency data). ALTER TABLE keeps
    existing databases working without a rebuild.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trending_keywords (
            keyword        TEXT,
            burst_score    REAL,
            updated_at     REAL,
            recent_count   INTEGER,
            baseline_count INTEGER
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trending_keywords)")}
    for name, decl in (("recent_count", "INTEGER"),
                       ("baseline_count", "INTEGER")):
        if name not in cols:
            conn.execute(
                "ALTER TABLE trending_keywords ADD COLUMN {} {}".format(name, decl))
    conn.commit()


def get_latest_created_at(conn):
    """Return the newest created_at in clean_posts as the "now" baseline.

    created_at is ISO 8601 UTC text (e.g. 2026-11-15T08:14:40+00:00).
    For a fixed format the lexicographic order equals the chronological order,
    so SQL MAX can be used directly. Returns None when the table is empty.
    """
    row = conn.execute("SELECT MAX(created_at) FROM clean_posts").fetchone()
    return row[0] if row else None


def parse_utc(text):
    """Parse ISO 8601 text into a timezone-aware UTC datetime.

    Naive values are treated as UTC to stay consistent with the stored data.
    """
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Text -> word frequency
# ---------------------------------------------------------------------------

def iter_words(tokens_json, title_clean, text_clean):
    """Yield the words of a single post that participate in counting.

    Prefer Member 2's cleaned tokens column (JSON array, already lowercased
    and stop-word filtered). If tokens are empty or unparsable, fall back to
    splitting the concatenated title and body with a regex.
    """
    if tokens_json:
        try:
            words = json.loads(tokens_json)
        except (TypeError, ValueError):
            words = None
        if isinstance(words, list):
            cleaned = filter_tokens(words)
            if cleaned:
                yield from cleaned
                return  # tokens are usable, so skip the fallback path

    # Fallback: join title and body, then extract lowercase words
    text = " ".join(part for part in (title_clean, text_clean) if part)
    yield from filter_tokens(WORD_PATTERN.findall(text.lower()))


def count_window(conn, start_iso, end_iso, include_end=True):
    """Count word frequencies in the [start_iso, end_iso] window; return a Counter.

    Time boundaries are compared as ISO strings: because created_at has a
    uniform format (fixed length, always +00:00), lexicographic order equals
    chronological order. This also uses the idx_clean_created index, which is
    faster than parsing timestamps inside SQL.
    include_end keeps the two windows from counting the same boundary post twice.
    """
    operator = "<=" if include_end else "<"
    cursor = conn.execute(
        "SELECT tokens, title_clean, text_clean FROM clean_posts "
        "WHERE created_at >= ? AND created_at {} ?".format(operator),
        (start_iso, end_iso),
    )

    counter = Counter()
    for tokens_json, title_clean, text_clean in cursor:
        counter.update(iter_words(tokens_json, title_clean, text_clean))
    return counter


def detect_trending(conn):
    """Return [(keyword, burst_score, recent_count, baseline_count), ...], top 20.

    Burst formula is Member 3's original one. The PM gate only decides which
    candidates are allowed to compete and how ties are broken.
    """
    try:
        latest = get_latest_created_at(conn)
    except sqlite3.OperationalError as exc:
        # clean_posts does not exist yet (Member 2's pipeline has not run), not an error
        print("[Trend] No data yet, waiting... ({})".format(exc))
        return []

    if not latest:
        # The table exists but is empty (e.g. right after startup)
        print("[Trend] No data yet, waiting...")
        return []

    now = parse_utc(latest)
    recent_start = now - timedelta(seconds=RECENT_SECONDS)
    baseline_start = now - timedelta(seconds=BASELINE_SECONDS)

    # Last 1 hour: [now - 3600, now]
    recent_counter = count_window(conn, recent_start.isoformat(), now.isoformat())

    # Past 24 hours excluding the last hour: [now - 86400, now - 3600)
    # The right boundary is exclusive so the boundary post is not counted twice
    baseline_counter = count_window(
        conn, baseline_start.isoformat(), recent_start.isoformat(), include_end=False
    )

    # Baseline window is 24 h MINUS the most recent 1 h, i.e. 23 h.
    # Convert it to an expected count per 1 h so the two windows are
    # comparable. Without this, "recent / (baseline + 1)" gives 1.0 to
    # EVERY word seen once and never before, which flooded the Top-20.
    baseline_hours = (BASELINE_SECONDS - RECENT_SECONDS) / 3600.0

    def burst(recent: int, baseline: int) -> float:
        if BURST_FORMULA == "original":
            return recent / (baseline + 1)
        return recent / (baseline / baseline_hours + 1)

    scored = [
        (word, burst(count, baseline_counter[word]),
         count, baseline_counter[word])
        for word, count in recent_counter.items()
    ]

    # PM quality gate: keep only words repeated in the last hour. No relaxation
    # to 1 -- a short but honest list beats a padded, meaningless one.
    eligible = [item for item in scored if item[2] >= MIN_RECENT_COUNT]

    # Descending by score; ties broken by REAL frequency, then alphabetically.
    eligible.sort(key=lambda item: (-item[1], -item[2], item[0]))
    LAST_CYCLE_INFO.update({
        "effective_min": MIN_RECENT_COUNT,
        "formula": BURST_FORMULA,
        "n_candidates": len(eligible),
        "n_recent_tokens": sum(recent_counter.values()),
        "low_sample": len(eligible) < LOW_SAMPLE_BELOW,
    })
    return eligible[:TOP_N]


# ---------------------------------------------------------------------------
# Result writing
# ---------------------------------------------------------------------------

def save_trending(conn, rows):
    """Overwrite trending_keywords: clear old rows, then bulk insert new ones.

    Persists recent_count / baseline_count alongside the score so the dashboard
    can show the real frequency data instead of recomputing it.
    """
    now_ts = time.time()
    conn.execute("DELETE FROM trending_keywords")
    if rows:
        conn.executemany(
            "INSERT INTO trending_keywords "
            "(keyword, burst_score, updated_at, recent_count, baseline_count) "
            "VALUES (?, ?, ?, ?, ?)",
            [(kw, score, now_ts, recent, baseline)
             for kw, score, recent, baseline in rows],
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once():
    """Run one full cycle: connect -> ensure table -> detect -> write -> close.

    A fresh connection is opened and closed per cycle, so a long-running process
    does not hold a database handle forever and a broken connection cannot affect
    the next cycle.
    """
    conn = None
    try:
        # If app.db is missing in the workspace, print a hint instead of raising
        if DB_PATH is None:
            print("[Trend] app.db not found, no data yet, waiting...")
            return

        conn = connect_db()
        ensure_trending_table(conn)

        rows = detect_trending(conn)
        save_trending(conn, rows)

        if rows:
            preview = ", ".join(
                "{}({:.2f},1h={})".format(k, s, c) for k, s, c, _ in rows[:5])
            print("[Trend] Wrote {} trending keywords (formula={}, "
                  "min 1h freq={}, {} eligible, {} tokens in 1h{}), top 5: {}".format(
                      len(rows), LAST_CYCLE_INFO["formula"],
                      LAST_CYCLE_INFO["effective_min"],
                      LAST_CYCLE_INFO["n_candidates"],
                      LAST_CYCLE_INFO["n_recent_tokens"],
                      " [LOW SAMPLE]" if LAST_CYCLE_INFO["low_sample"] else "",
                      preview))
        else:
            print("[Trend] No data yet, waiting...")
    except sqlite3.Error as exc:
        # Database-level issues: missing file, locked database, wrong schema, etc.
        print("[Trend] Database operation failed: {}".format(exc))
    except Exception as exc:  # Catch-all so the loop never dies
        print("[Trend] Unexpected error in this cycle: {}".format(exc))
    finally:
        if conn is not None:
            conn.close()


def main():
    print("[Trend] Started, database: {}".format(DB_PATH if DB_PATH else "app.db not found in workspace"))
    print("[Trend] Running a detection cycle every {} seconds, press Ctrl+C to exit.".format(LOOP_INTERVAL))

    try:
        while True:
            run_once()
            time.sleep(LOOP_INTERVAL)
    except KeyboardInterrupt:
        print("\n[Trend] Exit signal received, script stopped.")


if __name__ == "__main__":
    main()
