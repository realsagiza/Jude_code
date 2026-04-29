"""Automation tools - batch file operations, clipboard, and workflow automation."""

import os
import shutil
import re
import time
from pathlib import Path
from typing import Optional


def batch_rename(
    directory: str,
    pattern: str,
    replacement: str,
    dry_run: bool = True,
    recursive: bool = False,
) -> str:
    """Rename multiple files matching a pattern.

    Args:
        directory: Directory to operate in
        pattern: Regex pattern to match in filenames
        replacement: Replacement string (can use \\1, \\2 etc.)
        dry_run: If True, only show what would be renamed
        recursive: If True, search subdirectories

    Returns:
        Summary of operations
    """
    base = Path(directory)
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    regex = re.compile(pattern)
    results = []
    count = 0

    if recursive:
        files = list(base.rglob("*"))
    else:
        files = list(base.iterdir())

    for f in sorted(files):
        if not f.is_file():
            continue
        new_name = regex.sub(replacement, f.name)
        if new_name != f.name:
            new_path = f.parent / new_name
            if dry_run:
                results.append(f"  Would rename: {f.name} -> {new_name}")
            else:
                f.rename(new_path)
                results.append(f"  Renamed: {f.name} -> {new_name}")
            count += 1

    header = f"[{'DRY RUN' if dry_run else 'EXECUTED'}] Batch rename in {directory}"
    if not results:
        return f"{header}\n  No files matched pattern: {pattern}"
    return header + "\n" + "\n".join(results) + f"\n  ({count} files affected)"


def batch_copy(
    source_dir: str,
    dest_dir: str,
    pattern: Optional[str] = None,
    recursive: bool = False,
    dry_run: bool = True,
) -> str:
    """Copy multiple files matching a pattern.

    Args:
        source_dir: Source directory
        dest_dir: Destination directory
        pattern: Optional glob pattern to filter files
        recursive: If True, search subdirectories
        dry_run: If True, only show what would be copied

    Returns:
        Summary of operations
    """
    src = Path(source_dir)
    dst = Path(dest_dir)
    if not src.is_dir():
        raise NotADirectoryError(f"Source not a directory: {source_dir}")

    dst.mkdir(parents=True, exist_ok=True)

    if recursive:
        files = list(src.rglob(pattern or "*"))
    else:
        files = list(src.glob(pattern or "*"))

    results = []
    count = 0
    for f in sorted(files):
        if not f.is_file():
            continue
        rel = f.relative_to(src) if recursive else f.name
        target = dst / rel
        if dry_run:
            results.append(f"  Would copy: {f.name} -> {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            results.append(f"  Copied: {f.name} -> {target}")
        count += 1

    header = f"[{'DRY RUN' if dry_run else 'EXECUTED'}] Batch copy from {source_dir} to {dest_dir}"
    if not results:
        return f"{header}\n  No files matched."
    return header + "\n" + "\n".join(results) + f"\n  ({count} files copied)"


def batch_delete(
    directory: str,
    pattern: str,
    dry_run: bool = True,
    recursive: bool = False,
) -> str:
    """Delete multiple files matching a glob pattern.

    Args:
        directory: Directory to operate in
        pattern: Glob pattern (e.g. "*.tmp", "*.log")
        dry_run: If True, only show what would be deleted
        recursive: If True, search subdirectories

    Returns:
        Summary of operations
    """
    base = Path(directory)
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    results = []
    count = 0

    # Use pathlib's glob which is cross-platform
    file_list = list(base.rglob(pattern)) if recursive else list(base.glob(pattern))
    for f in sorted(file_list):
        if not f.is_file():
            continue
        if dry_run:
            results.append(f"  Would delete: {f.relative_to(base)}")
        else:
            f.unlink()
            results.append(f"  Deleted: {f.relative_to(base)}")
        count += 1

    header = f"[{'DRY RUN' if dry_run else 'EXECUTED'}] Batch delete in {directory}"
    if not results:
        return f"{header}\n  No files matched pattern: {pattern}"
    return header + "\n" + "\n".join(results) + f"\n  ({count} files deleted)"


def organize_by_extension(
    directory: str,
    dry_run: bool = True,
    recursive: bool = False,
) -> str:
    """Organize files into folders by their extension.

    Args:
        directory: Directory to organize
        dry_run: If True, only show what would be moved
        recursive: If True, also organize files in subdirectories

    Returns:
        Summary of operations
    """
    base = Path(directory)
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    if recursive:
        files = list(base.rglob("*"))
    else:
        files = list(base.iterdir())

    results = []
    moved = 0
    for f in sorted(files):
        if not f.is_file():
            continue
        ext = f.suffix.lower() or "no_extension"
        # Clean extension name
        folder_name = ext.lstrip(".").upper() if ext != "no_extension" else "NO_EXTENSION"
        if not folder_name:
            folder_name = "NO_EXTENSION"

        target_dir = base / folder_name
        target_path = target_dir / f.name

        # Skip if already in correct folder
        if f.parent == target_dir:
            continue

        if dry_run:
            results.append(f"  Would move: {f.relative_to(base)} -> {folder_name}/")
        else:
            target_dir.mkdir(parents=True, exist_ok=True)
            # Handle name conflicts
            if target_path.exists():
                stem = target_path.stem
                counter = 1
                while target_path.exists():
                    target_path = target_dir / f"{stem}_{counter}{target_path.suffix}"
                    counter += 1
            shutil.move(str(f), str(target_path))
            results.append(f"  Moved: {f.relative_to(base)} -> {folder_name}/")
        moved += 1

    header = f"[{'DRY RUN' if dry_run else 'EXECUTED'}] Organize by extension in {directory}"
    if not results:
        return f"{header}\n  No files to organize."
    return header + "\n" + "\n".join(results) + f"\n  ({moved} files organized)"


def find_duplicates(directory: str, by_content: bool = False) -> str:
    """Find duplicate files in a directory.

    Args:
        directory: Directory to search
        by_content: If True, compare by file content (slower but accurate)
                    If False, compare by filename and size

    Returns:
        List of duplicate files
    """
    base = Path(directory)
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    import hashlib
    from collections import defaultdict

    if by_content:
        # Group by file hash
        hash_map = defaultdict(list)
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            try:
                h = hashlib.md5(f.read_bytes()).hexdigest()
                hash_map[h].append(str(f.relative_to(base)))
            except (OSError, PermissionError):
                continue

        duplicates = {h: paths for h, paths in hash_map.items() if len(paths) > 1}
    else:
        # Group by (name, size)
        name_size_map = defaultdict(list)
        for f in base.rglob("*"):
            if not f.is_file():
                continue
            try:
                key = (f.name, f.stat().st_size)
                name_size_map[key].append(str(f.relative_to(base)))
            except (OSError, PermissionError):
                continue

        duplicates = {k: paths for k, paths in name_size_map.items() if len(paths) > 1}

    if not duplicates:
        return "No duplicate files found."

    lines = [f"Found {len(duplicates)} set(s) of duplicate files:"]
    for key, paths in duplicates.items():
        lines.append("")
        if by_content:
            lines.append(f"  Hash: {key}")
        else:
            lines.append(f"  Name: {key[0]}, Size: {key[1]:,} bytes")
        for p in paths:
            lines.append(f"    - {p}")

    return "\n".join(lines)


def export_directory_tree(directory: str, max_depth: int = 3, show_size: bool = False) -> str:
    """Export the directory tree structure as text.

    Args:
        directory: Directory to scan
        max_depth: Maximum depth to traverse
        show_size: If True, show file sizes

    Returns:
        Tree structure as formatted text
    """
    base = Path(directory)
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    lines = [f"Directory tree: {base.resolve()}", ""]

    def _walk(dir_path: Path, prefix: str = "", depth: int = 0):
        if depth > max_depth:
            return
        entries = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        for i, entry in enumerate(entries):
            is_last = (i == len(entries) - 1)
            connector = "└── " if is_last else "├── "
            name = entry.name + ("/" if entry.is_dir() else "")
            if show_size and entry.is_file():
                try:
                    size = entry.stat().st_size
                    if size < 1024:
                        size_str = f" ({size} B)"
                    elif size < 1024 * 1024:
                        size_str = f" ({size/1024:.1f} KB)"
                    else:
                        size_str = f" ({size/(1024*1024):.1f} MB)"
                    name += size_str
                except OSError:
                    pass
            lines.append(prefix + connector + name)
            if entry.is_dir():
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension, depth + 1)

    _walk(base)
    return "\n".join(lines)


def clipboard_get() -> str:
    """Get text from clipboard."""
    import pyperclip
    try:
        text = pyperclip.paste()
        if text:
            return text
        return "(clipboard is empty)"
    except Exception as e:
        return f"Error accessing clipboard: {e}"


def clipboard_set(text: str) -> str:
    """Set text to clipboard.

    Args:
        text: Text to copy to clipboard

    Returns:
        Confirmation
    """
    import pyperclip
    try:
        pyperclip.copy(text)
        return f"Copied to clipboard ({len(text)} chars)"
    except Exception as e:
        return f"Error setting clipboard: {e}"


def wait_for(seconds: int = 1) -> str:
    """Wait for a specified number of seconds. Useful in multi-step workflows.

    Args:
        seconds: Number of seconds to wait

    Returns:
        Confirmation
    """
    time.sleep(seconds)
    return f"Waited for {seconds} second(s)."


def merge_text_files(
    directory: str,
    output_file: str,
    pattern: str = "*",
    separator: str = "\n\n---\n\n",
    recursive: bool = False,
) -> str:
    """Merge multiple text files into one.

    Args:
        directory: Directory containing files to merge
        output_file: Output file path
        pattern: Glob pattern to match files
        separator: Separator between file contents
        recursive: If True, search subdirectories

    Returns:
        Summary of operation
    """
    base = Path(directory)
    if not base.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    output = Path(output_file)
    output.parent.mkdir(parents=True, exist_ok=True)

    if recursive:
        files = sorted(base.rglob(pattern))
    else:
        files = sorted(base.glob(pattern))

    text_files = [f for f in files if f.is_file()]
    if not text_files:
        return f"No files matched pattern: {pattern}"

    parts = []
    for f in text_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            parts.append(f"--- {f.relative_to(base)} ---\n{content}")
        except (OSError, UnicodeDecodeError):
            parts.append(f"--- {f.relative_to(base)} ---\n[Binary file - skipped]")

    merged = separator.join(parts)
    output.write_text(merged, encoding="utf-8")

    return f"Merged {len(text_files)} files into {output_file} ({len(merged):,} chars total)"
