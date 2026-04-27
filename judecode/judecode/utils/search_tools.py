"""Search tools for the agent."""

import fnmatch
import json
import os
import re
from pathlib import Path
from typing import Optional


def glob_search(pattern: str, root: str = ".") -> str:
    """Find files matching a glob pattern."""
    root_path = Path(root).resolve()
    results = []
    for path in root_path.rglob(pattern):
        if path.is_file():
            results.append(str(path.relative_to(root_path)))
    return "\n".join(sorted(set(results))) if results else "No files found."


def grep_search(
    pattern: str,
    path: str = ".",
    glob: Optional[str] = None,
    output_mode: str = "content",
) -> str:
    """
    Search file contents with regex.
    Tries to use ripgrep first, falls back to Python regex.
    """
    root = Path(path)

    try:
        cmd = ["rg", "--json", "-n", pattern]
        if glob:
            cmd.extend(["--glob", glob])
        cmd.append(str(root))

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode in (0, 1):
            lines_out = []
            for line in proc.stdout.strip().split("\n"):
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                    if parsed.get("type") == "match":
                        data = parsed.get("data", {})
                        file_path = data.get("path", {}).get("text", "")
                        line_no = data.get("line_number", 0)
                        submatches = data.get("submatches", [])
                        matched_text = ""
                        if submatches:
                            match_data = submatches[0]
                            matched_text = match_data.get("match", {}).get("text", "")
                        lines_out.append(f"{file_path}:{line_no}:{matched_text}")
                except json.JSONDecodeError:
                    continue
            return "\n".join(lines_out) if lines_out else "No matches found."
        return f"rg error: {proc.stderr}"
    except FileNotFoundError:
        pass

    # Fallback to Python regex
    return _grep_fallback(pattern, root, glob, output_mode)


def _grep_fallback(
    pattern: str,
    root: Path,
    glob: Optional[str],
    output_mode: str,
) -> str:
    results = []
    regex = re.compile(pattern)

    if root.is_file():
        file_list = [root]
    else:
        file_list = list(root.rglob("*"))

    for fp in file_list:
        if not fp.is_file():
            continue
        if glob and not fnmatch.fnmatch(fp.name, glob):
            continue
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            for m in regex.finditer(content):
                line_count = content[:m.start()].count("\n") + 1
                line_start = content.rfind("\n", 0, m.start()) + 1
                line_end = content.find("\n", m.end())
                if line_end == -1:
                    line_end = len(content)
                line_text = content[line_start:line_end]
                rel = str(fp.relative_to(Path.cwd()) if fp.is_relative_to(Path.cwd()) else fp)
                results.append(f"{rel}:{line_count}:{line_text.strip()}")
        except (OSError, UnicodeDecodeError):
            continue

    return "\n".join(results[:200]) if results else "No matches found."
