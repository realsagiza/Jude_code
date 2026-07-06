"""PyInstaller runtime hook for Jude Code on macOS.

This runs when the .app bundle starts up, *before* any judecode code is
imported. Its job is to load the user's saved config from
``~/Library/Application Support/JudeCode/config.env`` into ``os.environ``
so that :mod:`judecode.config` picks up the right API keys / provider.

Without this, a frozen .app would have no environment variables at all
(since double-clicked apps don't inherit the shell environment).
"""

import os
import sys
from pathlib import Path


def _load_config_env() -> None:
    """Load KEY=value lines from the user config file into os.environ."""
    config_path = Path.home() / "Library" / "Application Support" / "JudeCode" / "config.env"
    if not config_path.exists():
        return
    try:
        text = config_path.read_text(encoding="utf-8")
    except Exception:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        # Don't overwrite values already set in the environment (shell wins).
        if key and key not in os.environ:
            os.environ[key] = val


def _setup_macos_paths() -> None:
    """Ensure the frozen app can find its bundled resources."""
    # When frozen, sys._MEIPASS points to the bundle's resource dir.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        # Add to PATH so any bundled binaries are discoverable.
        current_path = os.environ.get("PATH", "")
        if meipass not in current_path:
            os.environ["PATH"] = meipass + os.pathsep + current_path


# ── Run setup ──
_setup_macos_paths()
_load_config_env()
