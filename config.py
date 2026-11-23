"""CS5481 Topic 4 - group-shared configuration (owned by Member 5 / PM).

The authoritative table + column names are NOT invented here: they are whatever
Members 1-4 actually wrote into data/app.db. See schema.sql for the reverse-
engineered contract and README_member5.md for the alignment notes.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- Canonical storage ----------------------------------------------------
# Every teammate's script is pointed at this one file. Override with CS5481_DB.
DB_PATH = Path(os.environ.get("CS5481_DB") or (ROOT / "data" / "app.db"))
# Member 5 owns this second file. It never writes to the shared DB, so the
# dashboard cannot contend with Members 1-4 for the single SQLite writer lock.
HISTORY_DB_PATH = ROOT / "data" / "member5_history.db"

# --- Domain ---------------------------------------------------------------
DOMAIN_NAME = "AI Industry Sentiment & Trend Monitoring"
DOMAIN_NOTE = "Hacker News tech discussion; 1 h vs prior 24 h burst detection"
DATA_SOURCE = "Hacker News public Firebase API (no credentials)"

# --- Tracked entities: ONE source of truth (PM-owned registry) -------------
# DEFAULT = Member 4's delivered set (group confirmed this is acceptable).
# The slide's ChatGPT/OpenAI/Claude/Gemini is an OPTIONAL reference profile:
# CS5481_ENTITY_PROFILE=slide. Member 4's entities.py re-exports this dict,
# so analyzer / dashboard / orchestrator can never disagree.
from entity_registry import (              # noqa: E402
    EXPECTED_ENTITIES, PROFILE as ENTITY_PROFILE,
    SLIDE_ENTITIES as SLIDE_ENTITY_MAP,
    TRACKED_ENTITIES as _TRACKED,
    mismatch as entity_mismatch,
)
TRACKED_ENTITIES = dict(_TRACKED)
SLIDE_ENTITIES = tuple(EXPECTED_ENTITIES)

# --- Trend-detection quality (documented here, read by trend.py) -----------
# Default min frequency = 2 in the last hour. It is deliberately NOT relaxed
# to 1: a short honest keyword list beats 20 one-off words.
# CS5481_BURST_FORMULA=original restores Member 3's raw 1h/24h ratio.
TREND_MIN_RECENT_COUNT = int(os.environ.get("CS5481_MIN_RECENT_COUNT", "2"))
TREND_BURST_FORMULA = os.environ.get("CS5481_BURST_FORMULA", "rate")
TREND_LOW_SAMPLE_BELOW = 8

# --- Colors ---------------------------------------------------------------
# Product sentiment, not price movement: positive = green, negative = red.
# Swap the two if the group retargets to stock tickers (A-share: up=red).
COLOR_POSITIVE = "#2e9e7b"
COLOR_NEUTRAL = "#9aa4b2"
COLOR_NEGATIVE = "#d64550"
COLOR_ACCENT = "#3b6fd4"
COLOR_BURST_HIGH = "#e2603f"
COLOR_BURST_LOW = "#f0b429"
ENTITY_PALETTE = ["#3b6fd4", "#d64550", "#2e9e7b", "#8a63d2",
                  "#e08a2e", "#2a9dbf", "#c0507f", "#6f7b8a"]
LABEL_COLORS = {
    "positive": COLOR_POSITIVE,
    "neutral": COLOR_NEUTRAL,
    "negative": COLOR_NEGATIVE,
}
LABEL_TEXT = {"positive": "Positive", "neutral": "Neutral", "negative": "Negative"}

# --- Display timezone ------------------------------------------------------
# The shared DB MUST stay in UTC: Members 1-4 write ISO 8601 UTC text and
# REAL Unix seconds, and the SQL hour filters depend on that fixed format.
# Only human-facing labels are converted, to Hong Kong Time (HKT, UTC+8,
# no DST). A fixed offset avoids any tzdata dependency on Windows.
from datetime import timedelta, timezone as _dt_timezone       # noqa: E402
DISPLAY_TZ = _dt_timezone(timedelta(hours=8), "HKT")
DISPLAY_TZ_NAME = "HKT (UTC+8, Hong Kong Time)"
DISPLAY_TZ_SHORT = "HKT (UTC+8)"
DISPLAY_AXIS_LABEL = "Time (HKT, UTC+8)"
DISPLAY_TIME_COL = "Time (HKT)"

# --- Dashboard behaviour --------------------------------------------------
REFRESH_OPTIONS = {"10 s": 10, "30 s": 30, "60 s": 60, "5 min": 300}
DEFAULT_REFRESH = "30 s"
TIME_WINDOWS = {"Last 1 h": 1, "Last 6 h": 6, "Last 24 h": 24, "All data": 24 * 365}
DEFAULT_WINDOW = "Last 24 h"
DEFAULT_TOP_N = 20
BURST_MIN_SCORE = 0.0
STALE_AFTER_SECONDS = 300        # pipeline components: yellow above, red above 3x
SHIFT_THRESHOLD = 0.20           # Member 4's constant, mirrored for display only
