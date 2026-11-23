"""Single source of truth for tracked entities. Owner: Member 5 (PM).

Why this file exists
--------------------
The group's slide *suggests* ChatGPT / OpenAI / Claude / Gemini as keywords.
Member 4 actually implemented Apple / AWS / GitHub / Google / OpenAI. The
group confirmed that following the MEMBERS' delivered set is fine, so that
set stays the DEFAULT here. The slide set remains available as an opt-in
profile (no code edits needed):

    CS5481_ENTITY_PROFILE=slide python run_pipeline.py --offline

The value of this file is not the choice of names: it is that Member 4's
analyzer, the orchestrator and the dashboard now read ONE dict, and that
switching profiles cannot leave stale entity rows behind (run_pipeline.py
prunes them).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CUSTOM_ENTITIES_PATH = ROOT / "data" / "entity_overrides.json"

# --- Profile A: the agreed slide (DEFAULT) --------------------------------
# Aliases are matched by Member 4 with word-boundary regexes, so multi-word
# and hyphenated aliases are fine. Deliberately conservative: no generic words
# that would create false positives (e.g. "bard", "opus", "sonnet" are omitted).
SLIDE_ENTITIES: dict[str, tuple[str, ...]] = {
    "ChatGPT": ("chatgpt", "chat gpt", "gpt-4", "gpt4", "gpt 4",
                "gpt-5", "gpt5", "gpt 5", "chatgpt plus"),
    "OpenAI": ("openai", "open ai", "sam altman", "sora", "o1 model", "o3 model"),
    "Claude": ("claude", "anthropic", "claude code"),
    "Gemini": ("gemini", "google gemini", "google ai", "deepmind"),
}

# --- Profile B: Member 4's original delivery (kept for rollback) ---------
LEGACY_ENTITIES: dict[str, tuple[str, ...]] = {
    "Apple": ("apple", "iphone", "ios", "mac"),
    "AWS": ("aws", "amazon web services"),
    "GitHub": ("github", "git hub"),
    "Google": ("google", "android", "pixel"),
    "OpenAI": ("openai", "chatgpt"),
}

SLIDE_ENTITY_NAMES = tuple(SLIDE_ENTITIES)


def load_custom_entities() -> dict[str, tuple[str, ...]]:
    """Load dashboard-managed entities; an empty or invalid file falls back to legacy."""
    if not CUSTOM_ENTITIES_PATH.is_file():
        return {}
    try:
        payload = json.loads(CUSTOM_ENTITIES_PATH.read_text(encoding="utf-8"))
        raw = payload.get("entities", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, tuple[str, ...]] = {}
        for name, aliases in raw.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if isinstance(aliases, list):
                clean = tuple(
                    str(alias).strip().lower()
                    for alias in aliases
                    if isinstance(alias, str) and str(alias).strip()
                )
            else:
                clean = ()
            if clean:
                result[name.strip()] = clean
        return result
    except (OSError, json.JSONDecodeError):
        return {}


def save_custom_entities(entities: dict[str, tuple[str, ...]]) -> None:
    """Persist dashboard-managed entities as the active member profile."""
    CUSTOM_ENTITIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entities": {
            name: list(aliases)
            for name, aliases in sorted(entities.items())
            if name.strip() and aliases
        }
    }
    CUSTOM_ENTITIES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def reset_custom_entities() -> None:
    """Remove dashboard overrides and restore the shipped default entities."""
    if CUSTOM_ENTITIES_PATH.is_file():
        CUSTOM_ENTITIES_PATH.unlink()


CUSTOM_ENTITIES = load_custom_entities()

PROFILES = {
    "member": CUSTOM_ENTITIES or LEGACY_ENTITIES,
    "slide": SLIDE_ENTITIES,            # opt-in: the slide's suggestion
}

_ALIASES = {"member": "member", "members": "member", "legacy": "member",
            "member4": "member", "original": "member", "default": "member",
            "slide": "slide", "suggested": "slide"}


def active_profile() -> str:
    """Resolve the profile name from CS5481_ENTITY_PROFILE (default 'member')."""
    raw = (os.environ.get("CS5481_ENTITY_PROFILE") or "member").strip().lower()
    return _ALIASES.get(raw, "member")


PROFILE = active_profile()
TRACKED_ENTITIES: dict[str, tuple[str, ...]] = dict(PROFILES[PROFILE])
# Kept as a *reference*, not a requirement: the group accepted the members'
# entity set. The dashboard shows both side by side without a warning.
EXPECTED_ENTITIES = SLIDE_ENTITY_NAMES


def mismatch() -> list[str]:
    """Entity names that differ between the active profile and the slide."""
    return sorted(set(EXPECTED_ENTITIES) ^ set(TRACKED_ENTITIES))


def all_alias_names() -> set[str]:
    """All entity names ever used by any profile (for pruning stale DB rows)."""
    names: set[str] = set()
    for profile in PROFILES.values():
        names.update(profile)
    return names


if __name__ == "__main__":
    print("profile:", PROFILE)
    for name, aliases in TRACKED_ENTITIES.items():
        print(f"  {name}: {', '.join(aliases)}")
    print("slide mismatch:", mismatch() or "(aligned)")
