"""Configuration persistence for the native Mac app.

The Mac app stores its config in a writable, user-specific location so it
works correctly when packaged as a read-only ``.app`` bundle:

    ~/Library/Application Support/JudeCode/config.env

The file uses the same ``KEY=value`` format as the project ``.env`` so the
existing :mod:`judecode.config` loader picks it up automatically (the
runtime hook loads it into ``os.environ`` before judecode is imported).

This module also supports falling back to the project ``.env`` when running
from source (development mode).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "JudeCode"
CONFIG_PATH = APP_SUPPORT_DIR / "config.env"

# All provider-related keys we manage in the UI.
PROVIDERS = ["deepseek", "anthropic", "zai"]

PROVIDER_LABELS = {
    "deepseek": "DeepSeek",
    "anthropic": "Anthropic (Claude)",
    "zai": "Z.AI / Zhipu GLM",
}

# (env_key, default_value, is_secret, label)
PROVIDER_FIELDS = {
    "deepseek": [
        ("JUDECODE_DEEPSEEK_API_KEY", "", True, "API Key"),
        ("JUDECODE_DEEPSEEK_MODEL", "deepseek-chat", False, "Model"),
        ("JUDECODE_DEEPSEEK_BASE_URL", "https://api.deepseek.com", False, "Base URL"),
    ],
    "anthropic": [
        ("JUDECODE_ANTHROPIC_API_KEY", "", True, "API Key"),
        ("JUDECODE_ANTHROPIC_MODEL", "claude-sonnet-4-20250514", False, "Model"),
    ],
    "zai": [
        ("JUDECODE_ZAI_API_KEY", "", True, "API Key"),
        ("JUDECODE_ZAI_MODEL", "glm-4.6", False, "Model"),
        ("JUDECODE_ZAI_BASE_URL", "https://api.z.ai/api/paas/v4", False, "Base URL"),
    ],
}

# Optional vision provider fields.
VISION_FIELDS = [
    ("JUDECODE_VISION_BASE_URL", "", False, "Vision Base URL"),
    ("JUDECODE_VISION_API_KEY", "", True, "Vision API Key"),
    ("JUDECODE_VISION_MODEL", "", False, "Vision Model"),
]


def ensure_config_dir() -> None:
    """Create the Application Support directory if it does not exist."""
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)


def _project_env_path() -> Optional[Path]:
    """Return the project ``.env`` path when running from source, else None."""
    here = Path(__file__).resolve()
    # judecode/ui/mac_config.py → project .env is 3 levels up
    candidate = here.parents[2] / ".env"
    if candidate.exists():
        return candidate
    candidate2 = here.parents[3] / ".env"
    if candidate2.exists():
        return candidate2
    return None


def load_config() -> dict[str, str]:
    """Load all known config keys from environment + config files.

    Returns a flat dict of ``KEY → value``. Values already in
    ``os.environ`` win (they may have been set by the runtime hook or the
    user's shell).

    Priority (highest wins):
      1. ``os.environ`` (set by runtime hook / shell)
      2. Project ``.env`` (development mode — auto-imported on first run)
      3. ``~/Library/Application Support/JudeCode/config.env``
    """
    values: dict[str, str] = {}

    # 1. Start with the on-disk config file (lowest priority)
    if CONFIG_PATH.exists():
        values.update(_parse_env_file(CONFIG_PATH))

    # 2. Project .env — auto-import on first run so the user doesn't have
    #    to re-enter keys they already have in their repo .env.
    proj = _project_env_path()
    if proj is not None:
        values.update(_parse_env_file(proj))
        # If the app-support config doesn't exist yet, seed it from .env
        # so the values persist after the user quits.
        if not CONFIG_PATH.exists():
            try:
                save_config(values)
            except Exception:
                pass  # non-critical

    # 3. Environment variables (highest priority — set by runtime hook / shell)
    all_keys = {"JUDECODE_PROVIDER", "TAVILY_API_KEY"}
    for provider in PROVIDERS:
        for key, default, _, _ in PROVIDER_FIELDS[provider]:
            all_keys.add(key)
    for key, _, _, _ in VISION_FIELDS:
        all_keys.add(key)

    for key in all_keys:
        env_val = os.environ.get(key)
        if env_val is not None:
            values[key] = env_val

    # Fill defaults for missing provider fields
    for provider in PROVIDERS:
        for key, default, _, _ in PROVIDER_FIELDS[provider]:
            values.setdefault(key, default)
    for key, default, _, _ in VISION_FIELDS:
        values.setdefault(key, default)
    values.setdefault("JUDECODE_PROVIDER", "deepseek")

    return values


def save_config(values: dict[str, str]) -> Path:
    """Persist the given config values to the app-support config file.

    Also updates ``os.environ`` in-place so the change takes effect
    immediately for the running process (the caller is responsible for
    re-creating the API client / agent if needed).
    """
    ensure_config_dir()

    lines: list[str] = []
    lines.append("# Jude Code configuration (managed by the Mac app)")
    lines.append("# This file is loaded at startup by runtime_hook_mac.py")
    lines.append("")

    provider = values.get("JUDECODE_PROVIDER", "deepseek")
    lines.append("# ── Provider Selection ──")
    lines.append(f"JUDECODE_PROVIDER={provider}")
    lines.append("")

    for p in PROVIDERS:
        lines.append(f"# ── {PROVIDER_LABELS[p]} ──")
        for key, default, _, label in PROVIDER_FIELDS[p]:
            val = values.get(key, default)
            # Keep empty values as empty (don't write the placeholder default)
            lines.append(f"{key}={val}")
        lines.append("")

    lines.append("# ── Vision API (optional) ──")
    for key, default, _, _ in VISION_FIELDS:
        val = values.get(key, default)
        lines.append(f"{key}={val}")
    lines.append("")

    tavily = values.get("TAVILY_API_KEY", "")
    lines.append("# ── Tavily Search API (optional) ──")
    lines.append(f"TAVILY_API_KEY={tavily}")
    lines.append("")

    CONFIG_PATH.write_text("\n".join(lines), encoding="utf-8")

    # Update the live environment so a freshly-created client picks it up.
    for p in PROVIDERS:
        for key, default, _, _ in PROVIDER_FIELDS[p]:
            os.environ[key] = values.get(key, default)
    for key, default, _, _ in VISION_FIELDS:
        os.environ[key] = values.get(key, default)
    os.environ["JUDECODE_PROVIDER"] = provider
    if tavily:
        os.environ["TAVILY_API_KEY"] = tavily

    return CONFIG_PATH


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a simple ``KEY=value`` .env file (no shell expansion)."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Strip surrounding quotes if present
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        values[key] = val
    return values


def has_api_key_for_provider(values: dict[str, str], provider: str) -> bool:
    """Return True if the given provider has a non-empty API key configured."""
    for key, _, _, _ in PROVIDER_FIELDS.get(provider, []):
        if "_API_KEY" in key and values.get(key, "").strip():
            return True
    return False


def import_from_env_file(env_path: Path) -> dict[str, str]:
    """Import all known keys from an external .env file.

    Returns a dict of only the keys that were actually found in the file
    (does not include defaults). Useful for the "Import .env" button.
    """
    if not env_path.exists():
        return {}
    parsed = _parse_env_file(env_path)
    # Filter to only the keys we care about.
    known: set[str] = {"JUDECODE_PROVIDER", "TAVILY_API_KEY"}
    for provider in PROVIDERS:
        for key, _, _, _ in PROVIDER_FIELDS[provider]:
            known.add(key)
    for key, _, _, _ in VISION_FIELDS:
        known.add(key)
    return {k: v for k, v in parsed.items() if k in known}


def find_project_env() -> Optional[Path]:
    """Find the project .env file (for the Import button default location)."""
    return _project_env_path()
