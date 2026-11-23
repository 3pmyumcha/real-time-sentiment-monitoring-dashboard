"""Member 5 (PM) orchestrator: run Members 1-4 in order against ONE SQLite DB.

    python run_pipeline.py --once                    # one full cycle (needs network)
    python run_pipeline.py --offline                # M3+M4 only, no crawling
    python run_pipeline.py --watch --interval 60    # continuous near-real-time loop
    python -m streamlit run dashboard.py            # separate terminal

PM integration steps (all additive, none of them changes a teammate's
scoring formula):

    --reclean      recompute stored tokens with the shared stop-word list
    prune          drop sentiment_results rows for entities no longer tracked
    snapshot       copy Member 3's current Top-20 into the PM history DB even
                   when no browser is open

Every module is invoked through its own public API:

    M1+M2  src.processing.pipeline.run_once()      (collect_data -> clean -> store)
    M3     trend.run_once()                        (detect + rewrite Top-20)
    M4     src.sentiment.analyzer.analyze_posts()  (VADER + hourly trends)

SQLite allows one writer at a time, so these steps are SEQUENTIAL by design.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_FILE = Path(os.environ.get("CS5481_DB") or (ROOT / "data" / "app.db"))
os.environ["CS5481_DB"] = str(DB_FILE)

import db                                            # noqa: E402  (after env set)


def ensure_db() -> None:
    """Create the shared DB with Member 2's own DDL if it does not exist."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    schema = (ROOT / "src" / "processing" / "schema.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(str(DB_FILE), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()


def reclean_stored_tokens() -> int:
    """Apply the shared stop-word list to tokens already in clean_posts.

    The 500 delivered posts were cleaned with Member 2's original 18-word
    list. Without this one-off migration, the noise fix would only affect
    newly crawled posts and the existing chart would stay polluted.
    Uses raw_posts as the source of truth, so it is idempotent.
    """
    from src.processing.cleaner import process_row
    conn = sqlite3.connect(str(DB_FILE), timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        rows = conn.execute(
            "SELECT r.post_id, r.title, r.text FROM raw_posts r "
            "JOIN clean_posts c ON c.post_id = r.post_id").fetchall()
        updates = []
        for post_id, title, text in rows:
            tokens = process_row({"title": title or "", "text": text or ""})["tokens"]
            updates.append((json.dumps(tokens, ensure_ascii=False), post_id))
        with conn:
            conn.executemany(
                "UPDATE clean_posts SET tokens = ? WHERE post_id = ?", updates)
        print(f"[PM] re-cleaned tokens for {len(updates)} stored posts")
        db.log_run("PM re-clean stored tokens", len(updates), "shared stop-list")
        return len(updates)
    finally:
        conn.close()


def prune_stale_entities() -> int:
    """Delete sentiment rows for entities that are no longer tracked.

    Member 4 uses INSERT OR REPLACE and never deletes, so switching the entity
    profile would otherwise leave Apple/AWS/GitHub/Google rows in the chart.
    """
    import entity_registry
    keep = sorted(entity_registry.TRACKED_ENTITIES)
    if not keep:
        return 0
    placeholders = ",".join("?" * len(keep))
    conn = sqlite3.connect(str(DB_FILE), timeout=10)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='sentiment_results'").fetchone()
        if not exists:
            return 0
        n = conn.execute(
            f"SELECT COUNT(*) FROM sentiment_results "
            f"WHERE entity NOT IN ({placeholders})", keep).fetchone()[0]
        if n:
            with conn:
                conn.execute(
                    f"DELETE FROM sentiment_results "
                    f"WHERE entity NOT IN ({placeholders})", keep)
        print(f"[PM] active profile={entity_registry.PROFILE}, "
              f"removed {n} stale entity rows")
        db.log_run("PM prune stale entities", int(n),
                   f"profile={entity_registry.PROFILE}")
        return int(n)
    finally:
        conn.close()


def run_ingestion(limit: int, offline: bool) -> int:
    if offline:
        print("[1/2] skipped (--offline): using existing raw_posts / clean_posts")
        return 0
    from src.processing.pipeline import run_once
    n = run_once(limit=limit)
    print(f"[1/2] M1+M2: {n} new posts ingested + cleaned")
    db.log_run("M1+M2 ingestion+cleaning", n, f"limit={limit}")
    return int(n or 0)


def run_trend() -> int:
    import trend
    trend.run_once()
    n = db.scalar("SELECT COUNT(*) FROM trending_keywords", default=0) or 0
    print(f"[3/4] M3 trend detection: {n} keywords in trending_keywords")
    db.log_run("M3 trend detection", int(n), "1h vs prior 24h")
    return int(n)


def run_sentiment() -> int:
    from src.sentiment.analyzer import analyze_posts, load_posts
    posts = load_posts(DB_FILE)
    n = analyze_posts(DB_FILE, posts)
    nt = db.scalar("SELECT COUNT(*) FROM sentiment_trends", default=0) or 0
    print(f"[4/4] M4 VADER: {n} entity mentions, {nt} hourly trend buckets")
    db.log_run("M4 sentiment analysis", int(n), f"{nt} hourly buckets")
    return int(n)


def cycle(limit: int, offline: bool, reclean: bool = False) -> None:
    ensure_db()
    try:
        run_ingestion(limit, offline)
    except Exception as exc:                        # noqa: BLE001
        # A network/API failure must not stop trend + sentiment from refreshing.
        print(f"[1/2] ingestion failed (continuing with stored data): "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        db.log_run("M1+M2 ingestion+cleaning", 0, f"FAILED: {exc}"[:200])
    if reclean:
        try:
            reclean_stored_tokens()
        except Exception as exc:                    # noqa: BLE001
            print(f"[PM] re-clean failed: {exc}", file=sys.stderr)
    try:
        prune_stale_entities()
    except Exception as exc:                        # noqa: BLE001
        print(f"[PM] entity prune failed: {exc}", file=sys.stderr)
    try:
        run_trend()
    except Exception as exc:                        # noqa: BLE001
        print(f"[3/4] trend failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        db.log_run("M3 trend detection", 0, f"FAILED: {exc}"[:200])
    try:
        run_sentiment()
    except Exception as exc:                        # noqa: BLE001
        print(f"[4/4] sentiment failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        db.log_run("M4 sentiment analysis", 0, f"FAILED: {exc}"[:200])
    # PM history must keep growing even when the browser tab is closed.
    try:
        n = db.record_snapshot()
        if n:
            print(f"[PM] recorded {n} keyword rows into member5_history.db")
    except Exception as exc:                        # noqa: BLE001
        print(f"[PM] snapshot failed: {exc}", file=sys.stderr)
    print(f"[done] DB = {DB_FILE}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true", help="run exactly one cycle")
    ap.add_argument("--watch", action="store_true", help="loop forever")
    ap.add_argument("--interval", type=float, default=60.0,
                    help="seconds between cycles in --watch mode")
    ap.add_argument("--limit", type=int, default=50,
                    help="M1 fetch size per cycle (1..500, keep small to avoid rate limits)")
    ap.add_argument("--offline", action="store_true",
                    help="skip M1/M2 crawling, recompute M3+M4 from stored posts")
    ap.add_argument("--reclean", action="store_true",
                    help="one-off migration: re-tokenise stored posts with the shared stop-word list")
    args = ap.parse_args()
    if not args.watch:
        args.once = True
    if not 1 <= args.limit <= 500 or args.interval <= 0:
        ap.error("--limit must be 1..500 and --interval must be positive")

    print(f"[start] shared DB = {DB_FILE}")
    while True:
        cycle(args.limit, args.offline, args.reclean)
        args.reclean = False          # migration is a one-off, not per-cycle work
        if not args.watch:
            return 0
        print(f"[wait] {args.interval:g}s until next cycle (Ctrl+C to stop)")
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n[stop] pipeline stopped")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
