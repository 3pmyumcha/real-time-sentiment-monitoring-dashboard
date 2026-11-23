"""VADER-based entity sentiment analysis and hourly trend aggregation."""

import argparse
import logging
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from .entities import TRACKED_ENTITIES

import os
LOG = logging.getLogger(__name__)
# Honour the shared database environment variable.
DEFAULT_DB_PATH = Path(
    os.environ.get("APP_DB")
    or (Path(__file__).resolve().parents[2] / "data" / "app.db")
)
SHIFT_THRESHOLD = 0.20


def _entity_patterns(
    entities: Mapping[str, Sequence[str]],
) -> Dict[str, Tuple[re.Pattern, ...]]:
    patterns: Dict[str, Tuple[re.Pattern, ...]] = {}
    for entity, aliases in entities.items():
        # The canonical entity name is always an alias. This prevents a custom
        # dashboard configuration containing only a ticker (e.g. MSFT) from
        # disabling direct name matching (e.g. Microsoft).
        all_aliases = (entity, *aliases)
        unique_aliases = tuple(dict.fromkeys(
            alias.strip().lower()
            for alias in all_aliases
            if isinstance(alias, str) and alias.strip()
        ))
        patterns[entity] = tuple(
            re.compile(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", re.IGNORECASE)
            for alias in unique_aliases
        )
    return patterns


def match_entities(
    text: str, entities: Mapping[str, Sequence[str]] = TRACKED_ENTITIES
) -> List[str]:
    """Return configured entities mentioned in *text*, without substring matches."""
    patterns = _entity_patterns(entities)
    return [
        entity
        for entity, aliases in patterns.items()
        if any(pattern.search(text) for pattern in aliases)
    ]


def classify(compound: float) -> str:
    """Map VADER's compound score to the project's three labels."""
    if compound >= 0.05:
        return "positive"
    if compound <= -0.05:
        return "negative"
    return "neutral"


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_posts(db_path: Path, limit: Optional[int] = None) -> List[dict]:
    """Load cleaned posts from the project database."""
    query = (
        "SELECT post_id, title_clean, text_clean, created_at "
        "FROM clean_posts ORDER BY created_at DESC"
    )
    parameters: Tuple[object, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        parameters = (limit,)

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(query, parameters).fetchall()
    finally:
        connection.close()
    return [
        {
            "post_id": row[0],
            "title_clean": row[1] or "",
            "text_clean": row[2] or "",
            "created_at": row[3],
        }
        for row in rows
    ]


def ensure_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sentiment_results (
            post_id TEXT NOT NULL,
            entity TEXT NOT NULL,
            positive_score REAL NOT NULL,
            negative_score REAL NOT NULL,
            neutral_score REAL NOT NULL,
            compound_score REAL NOT NULL,
            sentiment_label TEXT NOT NULL CHECK (
                sentiment_label IN ('positive', 'negative', 'neutral')
            ),
            analyzed_at TEXT NOT NULL,
            PRIMARY KEY (post_id, entity)
        );

        CREATE TABLE IF NOT EXISTS sentiment_trends (
            entity TEXT NOT NULL,
            time_bucket TEXT NOT NULL,
            post_count INTEGER NOT NULL,
            avg_compound REAL NOT NULL,
            positive_count INTEGER NOT NULL,
            negative_count INTEGER NOT NULL,
            neutral_count INTEGER NOT NULL,
            sentiment_shift TEXT NOT NULL CHECK (
                sentiment_shift IN ('positive', 'negative', 'stable')
            ),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (entity, time_bucket)
        );

        CREATE INDEX IF NOT EXISTS idx_sentiment_trends_time
            ON sentiment_trends(time_bucket);
        """
    )


def analyze_posts(
    db_path: Path,
    posts: Optional[Iterable[Mapping[str, object]]] = None,
    entities: Mapping[str, Sequence[str]] = TRACKED_ENTITIES,
    sentiment_analyzer: Optional[SentimentIntensityAnalyzer] = None,
) -> int:
    """Analyze posts, upsert per-entity scores, and rebuild hourly trends."""
    selected_posts = list(posts) if posts is not None else load_posts(db_path)
    analyzer = sentiment_analyzer or SentimentIntensityAnalyzer()
    analyzed_at = datetime.now(timezone.utc).isoformat()
    rows = []

    for post in selected_posts:
        text = " ".join(
            value
            for value in (
                str(post.get("title_clean") or ""),
                str(post.get("text_clean") or ""),
            )
            if value
        )
        for entity in match_entities(text, entities):
            scores = analyzer.polarity_scores(text)
            rows.append(
                (
                    str(post["post_id"]),
                    entity,
                    scores["pos"],
                    scores["neg"],
                    scores["neu"],
                    scores["compound"],
                    classify(scores["compound"]),
                    analyzed_at,
                )
            )

    connection = sqlite3.connect(db_path)
    try:
        ensure_tables(connection)
        connection.executemany(
            """
            INSERT OR REPLACE INTO sentiment_results
            (post_id, entity, positive_score, negative_score, neutral_score,
             compound_score, sentiment_label, analyzed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        rebuild_trends(connection, analyzed_at)
        connection.commit()
    finally:
        connection.close()

    LOG.info("Analyzed %d entity mentions from %d posts", len(rows), len(selected_posts))
    return len(rows)


def _hour_bucket(value: str) -> str:
    return _parse_utc(value).replace(minute=0, second=0, microsecond=0).isoformat()


def rebuild_trends(connection: sqlite3.Connection, updated_at: str) -> None:
    """Rebuild hourly aggregates from the durable per-post result table."""
    grouped = defaultdict(list)
    query = """
        SELECT r.entity, p.created_at, r.compound_score, r.sentiment_label
        FROM sentiment_results AS r
        JOIN clean_posts AS p ON p.post_id = r.post_id
    """
    for entity, created_at, compound, label in connection.execute(query):
        grouped[(entity, _hour_bucket(created_at))].append((compound, label))

    ordered = sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1]))
    previous_avg: Dict[str, float] = {}
    trend_rows = []
    for (entity, time_bucket), values in ordered:
        average = sum(value[0] for value in values) / len(values)
        delta = average - previous_avg[entity] if entity in previous_avg else 0.0
        shift = (
            "positive"
            if delta >= SHIFT_THRESHOLD
            else "negative"
            if delta <= -SHIFT_THRESHOLD
            else "stable"
        )
        counts = {label: sum(1 for _, item_label in values if item_label == label)
                  for label in ("positive", "negative", "neutral")}
        trend_rows.append(
            (
                entity,
                time_bucket,
                len(values),
                average,
                counts["positive"],
                counts["negative"],
                counts["neutral"],
                shift,
                updated_at,
            )
        )
        previous_avg[entity] = average

    connection.execute("DELETE FROM sentiment_trends")
    connection.executemany(
        """
        INSERT INTO sentiment_trends
        (entity, time_bucket, post_count, avg_compound, positive_count,
         negative_count, neutral_count, sentiment_shift, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        trend_rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.db.is_file():
        parser.error("database not found: {}".format(args.db))
    count = analyze_posts(args.db, load_posts(args.db, args.limit))
    print("Analyzed {} entity mentions; wrote sentiment trends.".format(count))


if __name__ == "__main__":
    main()
