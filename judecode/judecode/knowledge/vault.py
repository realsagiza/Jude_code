"""Knowledge Vault Manager - manages the Obsidian-style vault for Jude Code."""

import os
import re
from pathlib import Path
from typing import Optional

from judecode.config import VAULT_PATH

DEFAULT_VAULT_PATH = VAULT_PATH


def get_vault_path() -> Path:
    """Get the vault path, creating it if necessary."""
    vault_path = Path(os.environ.get("JUDECODE_VAULT", DEFAULT_VAULT_PATH))
    vault_path.mkdir(parents=True, exist_ok=True)
    return vault_path


def get_vault_structure() -> dict:
    """Return the current vault structure as a tree."""
    vault = get_vault_path()
    result = []
    for item in sorted(vault.rglob("*.md")):
        rel = item.relative_to(vault)
        result.append(str(rel))
    return {
        "vault_path": str(vault),
        "note_count": len(result),
        "notes": result,
    }


def slugify(title: str) -> str:
    """Convert a title to a safe filename."""
    s = re.sub(r"[^\w\s-]", "", title).strip().lower()
    s = re.sub(r"[-\s]+", "-", s)
    return s or "untitled"


def note_exists(title: str) -> bool:
    """Check if a note file already exists."""
    vault = get_vault_path()
    filename = f"{slugify(title)}.md"
    return (vault / filename).exists()


def get_note_path(title: str) -> Path:
    """Get the full path for a note title."""
    return get_vault_path() / f"{slugify(title)}.md"
