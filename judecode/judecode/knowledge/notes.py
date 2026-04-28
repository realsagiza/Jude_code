"""Note operations for the Knowledge Vault - CRUD, tags, links."""

import re
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from judecode.knowledge.vault import (
    get_vault_path,
    get_note_path,
    slugify,
    note_exists,
)


def create_note(
    title: str,
    content: str = "",
    tags: Optional[list[str]] = None,
    links: Optional[list[str]] = None,
) -> str:
    """Create a new note. Returns the file path."""
    note_path = get_note_path(title)
    
    # Build frontmatter
    frontmatter_lines = ["---"]
    frontmatter_lines.append(f'title: "{title}"')
    frontmatter_lines.append(f'created: {datetime.now().isoformat()}')
    if tags:
        frontmatter_lines.append(f'tags: {tags}')
    if links:
        frontmatter_lines.append(f'links: {links}')
    frontmatter_lines.append("---")
    frontmatter_lines.append("")
    
    full_content = "\n".join(frontmatter_lines) + content
    
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
    # Preserve frontmatter
    frontmatter_match = re.match(r"^(---\n.*?\n---\n\n?)", old_text, re.DOTALL)
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
    """List all notes in the vault with metadata."""
    vault = get_vault_path()
    notes = []
    for md_file in sorted(vault.rglob("*.md")):
        rel = md_file.relative_to(vault)
        content = md_file.read_text(encoding="utf-8")
        tags = re.findall(r'#(\w[\w-]*)', content)
        links = re.findall(r'\[\[([^\]]+)\]\]', content)
        notes.append({
            "title": str(rel.with_suffix("")),
            "path": str(rel),
            "tags": tags,
            "links": links,
        })
    return notes


def extract_tags(content: str) -> list[str]:
    """Extract all #tags from content."""
    return list(set(re.findall(r'#(\w[\w-]*)', content)))


def extract_links(content: str) -> list[str]:
    """Extract all [[links]] from content."""
    return list(set(re.findall(r'\[\[([^\]]+)\]\]', content)))


def get_backlinks(title: str) -> list[str]:
    """Find all notes that link TO this note."""
    vault = get_vault_path()
    slug = slugify(title)
    backlinks = []
    search_term = f"[[{title}]]"
    for md_file in vault.rglob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        if search_term in content or f"[[{title}|" in content:
            rel = md_file.relative_to(vault)
            backlinks.append(str(rel.with_suffix("")))
    return backlinks
