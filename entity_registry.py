"""Single source of truth for tracked entities and aliases."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CUSTOM_ENTITIES_PATH = ROOT / "data" / "entity_overrides.json"

# --- Profile A: the agreed slide (DEFAULT) --------------------------------
# Aliases use word-boundary regexes, so multi-word
# and hyphenated aliases are fine. Deliberately conservative: no generic words
# that would create false positives (e.g. "bard", "opus", "sonnet" are omitted).
SLIDE_ENTITIES: dict[str, tuple[str, ...]] = {
    "ChatGPT": ("chatgpt", "chat gpt", "gpt-4", "gpt4", "gpt 4",
                "gpt-4o", "gpt-4.1", "gpt-5", "gpt5", "gpt 5",
                "chatgpt plus", "openai o1", "o1 model", "openai o3",
                "o3 model"),
    "OpenAI": ("openai", "open ai", "sam altman", "sora"),
    "Claude": ("claude", "claude 3", "claude 3.5", "claude 3.7",
               "claude sonnet", "claude opus", "claude haiku",
               "claude code"),
    "Gemini": ("gemini", "gemini 1.5", "gemini 2.0", "gemini 2.5",
               "google gemini", "google bard", "bard", "google deepmind",
               "deepmind"),
}

# --- Default profile -----------------------------------------------------
DEFAULT_ENTITIES: dict[str, tuple[str, ...]] = {
    "Apple": ("apple", "iphone", "ios", "mac", "macbook", "siri"),
    "AWS": ("aws", "amazon web services", "amazon bedrock", "aws bedrock"),
    "GitHub": ("github", "git hub", "github copilot", "copilot"),
    "Google": ("google", "android", "pixel", "google ai", "google cloud"),
    "Microsoft": ("microsoft", "msft", "azure", "windows", "github copilot",
                  "copilot"),
    "NVIDIA": ("nvidia", "nvda", "cuda", "geforce", "rtx"),
    "OpenAI": ("openai", "open ai", "chatgpt", "gpt-4", "gpt-4o",
               "gpt-4.1", "gpt-5", "o1", "o3", "sora", "sam altman"),
    "Anthropic": ("anthropic", "claude", "claude 3", "claude 3.5",
                  "claude 3.7", "claude sonnet", "claude opus",
                  "claude haiku"),
    "Gemini": ("gemini", "gemini 1.5", "gemini 2.0", "gemini 2.5",
               "google gemini", "google bard", "bard", "deepmind"),
    "Meta": ("meta", "meta ai", "llama", "llama 2", "llama 3",
             "llama 4", "instagram", "whatsapp"),
    "Mistral": ("mistral", "mistral ai", "mixtral", "mistral large",
                "mistral small", "codestral"),
    "xAI": ("xai", "x.ai", "grok", "grok 2", "grok 3"),
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
    """Persist dashboard-managed entities as the active profile."""
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
    "default": CUSTOM_ENTITIES or DEFAULT_ENTITIES,
    "slide": SLIDE_ENTITIES,            # opt-in: the slide's suggestion
}

_ALIASES = {
    "default": "default",
    "original": "default",
    "slide": "slide",
    "suggested": "slide",
}


def active_profile() -> str:
    """Resolve the profile name from ENTITY_PROFILE (default 'default')."""
    raw = (os.environ.get("ENTITY_PROFILE") or "default").strip().lower()
    return _ALIASES.get(raw, "default")


PROFILE = active_profile()
TRACKED_ENTITIES: dict[str, tuple[str, ...]] = dict(PROFILES[PROFILE])
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
