"""Member 5 read layer over the REAL shared SQLite database (data/app.db).

Design rules
------------
* The dashboard opens the shared DB with ``mode=ro``. It never writes there,
  so it cannot contend with Members 1-4 for SQLite's single writer lock.
* Anything the dashboard needs to remember (trend snapshot history, KPI
  history, pipeline run log) goes into a separate PM-owned file
  ``data/member5_history.db`` with WAL + busy_timeout.
* Every query is tolerant of a missing table / missing DB: the dashboard must
  degrade to a helpful hint, never crash, while teammates are still wiring up.
* Time anchoring uses ``MAX(clean_posts.created_at)`` as "data now", exactly
  like Member 3's detector. Using the wall clock would make a static snapshot
  look empty (the shipped data ends about 3 hours before the current time).

Time formats actually used by the team (all UTC):
  clean_posts.created_at / raw_posts.collected_at / sentiment_results.analyzed_at
      ISO 8601 text, e.g. 2026-11-15T08:14:40+00:00
  sentiment_trends.time_bucket
      ISO 8601 text truncated to the hour, e.g. 2026-11-15T08:00:00+00:00
  trending_keywords.updated_at
      REAL Unix seconds (time.time()), e.g. 1794736540.47
All of the above stay UTC. The dashboard converts them to Hong Kong Time
(HKT, UTC+8) for display only; see config.DISPLAY_TZ and iso().
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

import pandas as pd

import config

# Tables the four teammates write. Used for the ops tab and graceful checks.
SHARED_TABLES = (
    "raw_posts", "clean_posts", "trending_keywords",
    "sentiment_results", "sentiment_trends",
)


def db_path():
    return config.DB_PATH


def history_path():
    return config.HISTORY_DB_PATH


def connect(readonly: bool = True) -> sqlite3.Connection:
    """Open the shared DB. Read-only by default; missing file -> FileNotFoundError."""
    p = db_path()
    if not p.exists():
        raise FileNotFoundError(f"shared database not found: {p}")
    if readonly:
        uri = f"file:{p.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.execute("PRAGMA query_only=ON")
    else:
        conn = sqlite3.connect(str(p), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def q(sql: str, params=(), errors: list | None = None) -> pd.DataFrame:
    """Read query -> DataFrame. Missing table/DB -> empty frame, not an exception."""
    conn = None
    try:
        conn = connect()
        return pd.read_sql_query(sql, conn, params=params)
    except Exception as exc:                        # noqa: BLE001
        if errors is not None:
            msg = f"{type(exc).__name__}: {exc}"
            if msg not in errors:
                errors.append(msg)
        return pd.DataFrame()
    finally:
        if conn is not None:
            conn.close()


def scalar(sql: str, params=(), default=None):
    conn = None
    try:
        conn = connect()
        row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:                              # noqa: BLE001
        return default
    finally:
        if conn is not None:
            conn.close()


def table_columns(name: str) -> list[str]:
    conn = None
    try:
        conn = connect()
        return [r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')]
    except Exception:                              # noqa: BLE001
        return []
    finally:
        if conn is not None:
            conn.close()


def table_ddl(name: str) -> str:
    return scalar("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                  (name,), "") or ""


def table_counts() -> pd.DataFrame:
    rows = []
    for t in SHARED_TABLES:
        n = scalar(f'SELECT COUNT(*) FROM "{t}"', default=None)
        rows.append({"Table": t, "Rows": n if n is not None else None,
                     "Status": "Ready" if n is not None else "Missing"})
    return pd.DataFrame(rows)


# --------------------------------------------------------------- time utils
def data_clock() -> pd.Timestamp:
    """'Now' from the data itself, so static snapshots still render."""
    latest = scalar("SELECT MAX(created_at) FROM clean_posts")
    if latest:
        try:
            return pd.to_datetime(latest, utc=True)
        except Exception:                          # noqa: BLE001
            pass
    return pd.Timestamp.now(tz="UTC")


def iso_cutoff(hours: float) -> str:
    """SQL-comparable ISO string for data_clock() - hours (fixed UTC format)."""
    t = data_clock() - pd.Timedelta(hours=hours)
    try:
        return t.strftime("%Y-%m-%dT%H:%M:%S+00:00")
    except OverflowError:
        return "0000-01-01T00:00:00+00:00"


def iso(ts) -> str:
    """Format a Unix timestamp / datetime-ish value for DISPLAY in HKT (UTC+8).

    Stored values are still UTC (ISO 8601 text / REAL Unix seconds) — only this
    human-readable rendering is converted, per the group's interface contract.
    """
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "—"
    try:
        if isinstance(ts, (int, float)):
            d = datetime.fromtimestamp(float(ts), timezone.utc)
        else:
            d = pd.to_datetime(ts, utc=True).to_pydatetime()
        return d.astimezone(config.DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S HKT")
    except Exception:                              # noqa: BLE001
        return str(ts)


def utc_now() -> float:
    return time.time()


# --------------------------------------------------- PM-owned history store
def init_history() -> None:
    p = history_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trend_snapshots (
                source_updated_at REAL NOT NULL,
                keyword           TEXT NOT NULL,
                burst_score       REAL NOT NULL,
                rank_no           INTEGER,
                captured_at       REAL NOT NULL,
                formula           TEXT,
                PRIMARY KEY (source_updated_at, keyword)
            );
            CREATE TABLE IF NOT EXISTS kpi_snapshots (
                source_updated_at REAL PRIMARY KEY,
                captured_at       REAL NOT NULL,
                n_raw             INTEGER,
                n_clean           INTEGER,
                n_trend           INTEGER,
                n_results         INTEGER,
                n_sent_trends     INTEGER,
                newest_post_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS run_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                component   TEXT NOT NULL,
                ran_at      REAL NOT NULL,
                rows_in     INTEGER,
                note        TEXT
            );
            """
        )
        # Migration: history files created before the burst-formula fix have
        # no formula column. Those snapshots are explicitly marked 'pre-fix'
        # so a chart never silently lines up two incompatible scoring schemes.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trend_snapshots)")}
        if "formula" not in cols:
            conn.execute("ALTER TABLE trend_snapshots ADD COLUMN formula TEXT")
        conn.execute("UPDATE trend_snapshots SET formula='pre-fix' "
                     "WHERE formula IS NULL")
        conn.commit()
    finally:
        conn.close()


def history_q(sql: str, params=()) -> pd.DataFrame:
    conn = None
    try:
        init_history()
        conn = sqlite3.connect(f"file:{history_path().resolve().as_posix()}?mode=ro",
                               uri=True, timeout=5)
        return pd.read_sql_query(sql, conn, params=params)
    except Exception:                              # noqa: BLE001
        return pd.DataFrame()
    finally:
        if conn is not None:
            conn.close()


def record_snapshot() -> int:
    """Copy one Member-3 trend snapshot into PM history, deduped by updated_at.

    Member 3 DELETEs and rewrites trending_keywords every 60 s, so without this
    the dashboard can only show the current Top-20 and has no time series at all.
    Returns the number of keyword rows inserted (0 when already recorded).
    """
    stamp = scalar("SELECT MAX(updated_at) FROM trending_keywords")
    if stamp is None:
        return 0
    p = history_path()
    init_history()
    conn = sqlite3.connect(str(p), timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        already = conn.execute(
            "SELECT 1 FROM trend_snapshots WHERE source_updated_at=? LIMIT 1",
            (stamp,)).fetchone()
        if already:
            return 0
        rows = q("SELECT keyword, burst_score FROM trending_keywords "
                 "WHERE updated_at=? ORDER BY burst_score DESC, keyword", (stamp,))
        now = utc_now()
        conn.executemany(
            "INSERT OR IGNORE INTO trend_snapshots "
            "(source_updated_at, keyword, burst_score, rank_no, captured_at, formula) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(float(stamp), r.keyword, float(r.burst_score), i + 1, now,
              config.TREND_BURST_FORMULA)
             for i, r in enumerate(rows.itertuples(index=False))])
        conn.execute(
            "INSERT OR IGNORE INTO kpi_snapshots "
            "(source_updated_at, captured_at, n_raw, n_clean, n_trend, "
            " n_results, n_sent_trends, newest_post_at) VALUES (?,?,?,?,?,?,?,?)",
            (float(stamp), now,
             scalar("SELECT COUNT(*) FROM raw_posts", default=0),
             scalar("SELECT COUNT(*) FROM clean_posts", default=0),
             int(len(rows)),
             scalar("SELECT COUNT(*) FROM sentiment_results", default=0),
             scalar("SELECT COUNT(*) FROM sentiment_trends", default=0),
             scalar("SELECT MAX(created_at) FROM clean_posts", default="")))
        conn.commit()
        return len(rows)
    except Exception:                              # noqa: BLE001
        return 0
    finally:
        conn.close()


def log_run(component: str, rows_in: int = 0, note: str = "") -> None:
    init_history()
    conn = sqlite3.connect(str(history_path()), timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("INSERT INTO run_log (component, ran_at, rows_in, note) "
                     "VALUES (?, ?, ?, ?)", (component, utc_now(), rows_in, note))
        conn.commit()
    except Exception:                              # noqa: BLE001
        pass
    finally:
        conn.close()


if __name__ == "__main__":
    init_history()
    print("shared DB :", db_path(), "exists=", db_path().exists())
    print("PM history:", history_path(), "exists=", history_path().exists())
    print(table_counts().to_string(index=False))
    print("data clock:", data_clock())
