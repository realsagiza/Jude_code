"""Note operations for the Knowledge Vault - CRUD, tags, links.

=== Tag Storage Convention ===
Tags are stored CONSISTENTLY in two formats:
1. Frontmatter YAML (primary):  tags: [tag1, tag2]
2. Inline #tags (secondary):     #tag1 #tag2

Both are detected when listing notes.
When creating notes, tags go to frontmatter YAML AND as #tags in content.
"""

import re
import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from judecode.knowledge.vault import (
    get_vault_path,
    get_note_path,
    slugify,
    note_exists,
)


# ── Frontmatter Parsing ──


def _parse_frontmatter(content: str) -> dict:
    """Parse YAML-like frontmatter from markdown content.

    Returns dict with keys: title, created, tags, links, updated, status
    All fields are optional - returns what's found.
    """
    result = {}
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return result

    fm_text = match.group(1)
    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Handle quoted strings
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]

        # Handle YAML list like [a, b, c] or - a
        if value.startswith("["):
            # Parse inline list: [tag1, tag2, tag3]
            try:
                value = json.loads(value.replace("'", '"'))
            except (json.JSONDecodeError, TypeError):
                value = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        elif key in ("tags", "links") and isinstance(value, str) and not value.startswith("["):
            # Multi-line YAML list (not commonly used, but handle it)
            value = [value]

        result[key] = value

    return result


def _extract_tags_from_content(content: str) -> list[str]:
    """Extract all #tags from markdown content (excluding frontmatter)."""
    # Strip frontmatter first
    body = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL)
    return list(set(re.findall(r'#(\w[\w-]*)', body)))


def _extract_links_from_content(content: str) -> list[str]:
    """Extract all [[links]] from markdown content."""
    body = re.sub(r"^---\n.*?\n---\n?", "", content, flags=re.DOTALL)
    return list(set(re.findall(r'\[\[([^\]]+)\]\]', body)))


def _build_frontmatter(
    title: str,
    tags: Optional[list[str]] = None,
    links: Optional[list[str]] = None,
    extra: Optional[dict] = None,
) -> str:
    """Build a consistent frontmatter block."""
    lines = ["---"]
    lines.append(f'title: "{title}"')
    lines.append(f"created: {datetime.now().isoformat()}")

    if extra:
        for k, v in extra.items():
            if k not in ("title", "created"):
                if isinstance(v, str):
                    lines.append(f'{k}: "{v}"')
                else:
                    lines.append(f"{k}: {v}")

    if tags:
        lines.append(f"tags: {json.dumps(tags)}")
    if links:
        lines.append(f"links: {json.dumps(links)}")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _build_tag_line(tags: list[str]) -> str:
    """Build a line of inline #tags for the content body."""
    if not tags:
        return ""
    return " ".join(f"#{t}" for t in tags)


# ── CRUD Operations ──


def create_note(
    title: str,
    content: str = "",
    tags: Optional[list[str]] = None,
    links: Optional[list[str]] = None,
) -> str:
    """Create a new note. Tags go to frontmatter AND as inline #tags."""
    note_path = get_note_path(title)

    # Build tags: write to frontmatter + as inline #tags in content
    tag_parts = []
    if tags:
        tag_parts.append(_build_tag_line(tags))

    # Prepend inline tags to content (after frontmatter)
    header = "\n".join(tag_parts) if tag_parts else ""
    body_content = content
    if header and content:
        body_content = header + "\n\n" + content
    elif header and not content:
        body_content = header

    full_content = _build_frontmatter(title, tags=tags, links=links) + body_content

    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(full_content, encoding="utf-8")
    return str(note_path)


def read_note(title: str) -> str:
    """Read a note by title. Returns content or error message."""
    note_path = get_note_path(title)
    if not note_path.exists():
        return f"Note '{title}' not found."
    return note_path.read_text(encoding="utf-8")


def update_note(title: str, new_content: str) -> str:
    """Overwrite a note's content (keeping frontmatter)."""
    note_path = get_note_path(title)
    if not note_path.exists():
        return f"Note '{title}' not found."

    old_text = note_path.read_text(encoding="utf-8")
    frontmatter_match = re.match(r"^(---\n.*?\n---\n?)", old_text, re.DOTALL)
    if frontmatter_match:
        frontmatter = frontmatter_match.group(1)
        note_path.write_text(frontmatter + new_content, encoding="utf-8")
    else:
        note_path.write_text(new_content, encoding="utf-8")
    return f"Note '{title}' updated."


def append_to_note(title: str, content: str) -> str:
    """Append content to an existing note."""
    note_path = get_note_path(title)
    if not note_path.exists():
        return f"Note '{title}' not found."

    with open(note_path, "a", encoding="utf-8") as f:
        f.write("\n\n" + content)
    return f"Appended to '{title}'."


def delete_note(title: str) -> str:
    """Delete a note by title."""
    note_path = get_note_path(title)
    if not note_path.exists():
        return f"Note '{title}' not found."
    note_path.unlink()
    return f"Note '{title}' deleted."


def list_notes() -> list[dict]:
    """List all notes in the vault with metadata.

    Tags are gathered from TWO sources and merged:
    1. Frontmatter YAML 'tags' field (primary)
    2. Inline #tag syntax in content body (secondary)
    """
    vault = get_vault_path()
    notes = []
    for md_file in sorted(vault.rglob("*.md")):
        rel = md_file.relative_to(vault)
        content = md_file.read_text(encoding="utf-8")

        # Parse frontmatter for structured tags/links
        fm = _parse_frontmatter(content)
        fm_tags = fm.get("tags", [])
        if isinstance(fm_tags, str):
            fm_tags = [fm_tags]
        fm_links = fm.get("links", [])
        if isinstance(fm_links, str):
            fm_links = [fm_links]

        # Also find inline #tags and [[links]] in content
        inline_tags = _extract_tags_from_content(content)
        inline_links = _extract_links_from_content(content)

        # MERGE: frontmatter tags + inline tags (unique, deduplicated)
        all_tags = list(set(fm_tags + inline_tags))
        all_links = list(set(fm_links + inline_links))

        notes.append({
            "title": str(rel.with_suffix("")),
            "path": str(rel),
            "tags": all_tags,
            "links": all_links,
            "created": fm.get("created", ""),
            "updated": fm.get("updated", ""),
        })
    return notes


def extract_tags(content: str) -> list[str]:
    """Extract all #tags from content body (inline syntax)."""
    return _extract_tags_from_content(content)


def extract_links(content: str) -> list[str]:
    """Extract all [[links]] from content body."""
    return _extract_links_from_content(content)


def get_backlinks(title: str) -> list[str]:
    """Find all notes that link TO this note."""
    vault = get_vault_path()
    search_term = f"[[{title}]]"
    alt_search = f"[[{title}|"
    backlinks = []
    for md_file in vault.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        if search_term in content or alt_search in content:
            rel = md_file.relative_to(vault)
            backlinks.append(str(rel.with_suffix("")))
    return backlinks
