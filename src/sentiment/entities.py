"""Tracked entities and their common aliases (Member 4's public interface).

The inline fallback keeps this module importable even when the workspace root
is not on sys.path (e.g. someone runs the file in isolation).
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from entity_registry import TRACKED_ENTITIES  # type: ignore
    TRACKED_ENTITIES = dict(TRACKED_ENTITIES)
except ImportError:                                  # pragma: no cover
    # Must match the DEFAULT profile in entity_registry.py
    TRACKED_ENTITIES = {
        "Apple": ("apple", "iphone", "ios", "mac"),
        "AWS": ("aws", "amazon web services"),
        "GitHub": ("github", "git hub"),
        "Google": ("google", "android", "pixel"),
        "OpenAI": ("openai", "chatgpt"),
    }

__all__ = ["TRACKED_ENTITIES"]
