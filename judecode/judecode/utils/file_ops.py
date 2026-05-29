"""File operation utilities."""

from pathlib import Path
from typing import Optional

# Maximum recommended lines per read to avoid context overflow
MAX_RECOMMENDED_LINES = 300


def read_file(path: str, offset: int = 1, limit: Optional[int] = None) -> str:
    """Read file contents with optional line offset and limit.
    
    If no limit is specified and the file is large (>MAX_RECOMMENDED_LINES lines),
    a warning is appended to the output recommending chunked reading.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not p.is_file():
        raise IsADirectoryError(f"Path is a directory: {path}")

    with open(p, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    total_lines = len(lines)
    start = max(0, offset - 1)
    end = total_lines
    if limit is not None:
        end = min(start + limit, total_lines)

    result = lines[start:end]
    output = "".join(result)
    
    # Warn if reading a large file without limit
    if limit is None and total_lines > MAX_RECOMMENDED_LINES:
        warning = (
            f"\n\n⚠️  WARNING: This file has {total_lines} lines total. "
            f"You read ALL of it at once! This wastes context tokens.\n"
            f"Next time, use offset+limit to read in chunks:\n"
            f"  read(path=\"{path}\", offset=1, limit=200)\n"
            f"  read(path=\"{path}\", offset=201, limit=200)\n"
        )
        output += warning
    
    return output


def write_file(path: str, content: str) -> None:
    """Write content to a file, creating directories if needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def edit_file(path: str, old_string: str, new_string: str) -> bool:
    """
    Search and replace text in a file.
    Raises ValueError if old_string is not unique.
    """
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    with open(p, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    count = content.count(old_string)
    if count == 0:
        raise ValueError(f"Could not find the text to replace in {path}")
    if count > 1:
        raise ValueError(
            f"Found {count} occurrences. Make old_string more unique."
        )

    content = content.replace(old_string, new_string, 1)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def delete_file(path: str) -> None:
    """Delete a file."""
    p = Path(path)
    if p.exists() and p.is_file():
        p.unlink()
    else:
        raise FileNotFoundError(f"File not found: {path}")


def list_directory(path: str = ".") -> str:
    """List directory contents."""
    p = Path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    lines = []
    for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        marker = "/" if item.is_dir() else ""
        lines.append(f"{item.name}{marker}")
    return "\n".join(lines)
