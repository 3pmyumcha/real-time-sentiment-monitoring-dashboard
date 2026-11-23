"""Project configuration for storage, entities, trend settings, and display."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- Canonical storage ----------------------------------------------------
# Every pipeline script is pointed at this one file.
DB_PATH = Path(os.environ.get("APP_DB") or (ROOT / "data" / "app.db"))
# This secondary file stores history and never writes to the shared DB.
HISTORY_DB_PATH = ROOT / "data" / "history.db"

# --- Domain ---------------------------------------------------------------
DOMAIN_NAME = "AI Industry Sentiment & Trend Monitoring"
DOMAIN_NOTE = "Hacker News tech discussion; 1 h vs prior 24 h burst detection"
DATA_SOURCE = "Hacker News public Firebase API (no credentials)"

# --- Tracked entities -----------------------------------------------------
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
# Set BURST_FORMULA=original to restore the original 1h/24h ratio.
TREND_MIN_RECENT_COUNT = int(os.environ.get("MIN_RECENT_COUNT", "2"))
TREND_BURST_FORMULA = os.environ.get("BURST_FORMULA", "rate")

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
# The shared DB MUST stay in UTC: pipeline components write ISO 8601 UTC text and
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
SHIFT_THRESHOLD = 0.20
