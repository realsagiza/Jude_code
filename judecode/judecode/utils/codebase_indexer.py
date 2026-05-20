"""
Codebase Indexer for Jude Code.

Similar to Claude Code's codebase indexing system.
Scans the project directory, extracts structure (classes, functions, imports, etc.),
and creates a lightweight searchable index.

Benefits:
- AI can understand project structure WITHOUT reading all files into context
- Saves massive tokens on large projects
- Fast keyword-based search (no embedding model needed)
- Caches index to disk for reuse across sessions
"""

import ast
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

# ── Default ignore patterns (same as .gitignore) ──
DEFAULT_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".tox", ".eggs", "dist", "build", ".next", ".nuxt",
    ".idea", ".vscode", ".vs", ".DS_Store",
    "*.egg-info", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".terraform", ".serverless", "vendor", "bower_components",
    "target", "bin", "obj", "out", ".stack-work",
}

DEFAULT_IGNORE_EXTS = {
    ".pyc", ".pyo", ".so", ".dll", ".dylib",
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg", ".webp",
    ".mp4", ".mp3", ".wav", ".ogg", ".mov",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".msi", ".deb", ".rpm",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".min.js", ".min.css",
    ".map", ".swp", ".swo", ".bak", ".orig",
    ".log", ".cache", ".lock",
}

BINARY_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".ico", ".svg", ".webp",
    ".mp4", ".mp3", ".wav", ".ogg", ".mov", ".avi",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".msi", ".deb", ".rpm",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".pyc", ".pyo", ".so", ".dll", ".dylib",
    ".o", ".obj", ".a", ".lib",
}

# ── File types that are indexable (text files with code) ──
INDEXABLE_EXTS = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".jsx": "javascriptreact",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".r": "r",
    ".m": "matlab",
    ".scala": "scala",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".vue": "vue",
    ".svelte": "svelte",
    ".astro": "astro",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".xml": "xml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".bat": "batch",
    ".md": "markdown",
    ".rst": "rst",
    ".tex": "latex",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".dockerfile": "dockerfile",
    ".tf": "terraform",
    ".zig": "zig",
    ".nim": "nim",
    ".dart": "dart",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".groovy": "groovy",
    ".gradle": "gradle",
    ".vue": "vue",
    ".svelte": "svelte",
    ".wgsl": "wgsl",
    ".glsl": "glsl",
}


def _load_gitignore(root: Path) -> list[str]:
    """Load .gitignore patterns if they exist."""
    patterns = []
    gitignore_path = root / ".gitignore"
    if gitignore_path.exists():
        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except OSError:
            pass
    return patterns


def _should_ignore(
    rel_path: str,
    gitignore_patterns: list[str],
) -> bool:
    """Check if a relative path should be ignored."""
    parts = rel_path.replace("\\", "/").split("/")

    # Check default ignore dirs
    for part in parts:
        if part in DEFAULT_IGNORE_DIRS:
            return True

    # Check default ignore extensions
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in DEFAULT_IGNORE_EXTS:
        return True

    # Check .gitignore patterns (simple fnmatch matching)
    import fnmatch
    for pattern in gitignore_patterns:
        # Strip leading slash for relative matching
        p = pattern.lstrip("/")
        if fnmatch.fnmatch(rel_path, p) or fnmatch.fnmatch(os.path.basename(rel_path), p):
            return True
        # Check parent directories (e.g., "node_modules/" ignores all under it)
        if p.rstrip("/") in parts:
            return True

    return False


def _parse_python_file(content: str, file_path: str) -> dict[str, Any]:
    """Parse a Python file and extract structure info."""
    result = {
        "imports": [],
        "classes": [],
        "functions": [],
        "variables": [],
        "errors": [],
    }

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        result["errors"].append(f"SyntaxError: {e}")
        # Fallback: basic regex-based extraction
        result["imports"] = _regex_extract_imports(content)
        result["functions"] = _regex_extract_functions(content)
        result["classes"] = _regex_extract_classes(content)
        return result

    # ── Imports ──
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append({
                    "name": alias.name,
                    "alias": alias.asname,
                    "line": node.lineno,
                })
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                result["imports"].append({
                    "name": f"{module}.{alias.name}" if module else alias.name,
                    "alias": alias.asname,
                    "module": module,
                    "line": node.lineno,
                })

    # ── Classes ──
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            cls_info = {
                "name": node.name,
                "line": node.lineno,
                "docstring": ast.get_docstring(node) or "",
                "bases": [ast.unparse(b) for b in node.bases] if hasattr(ast, "unparse") else [],
                "methods": [],
                "decorators": [],
            }
            # Decorators
            for dec in node.decorator_list:
                if hasattr(ast, "unparse"):
                    cls_info["decorators"].append(ast.unparse(dec))
                else:
                    cls_info["decorators"].append(getattr(dec, "id", str(dec)))
            # Methods
            for item in ast.iter_child_nodes(node):
                if isinstance(item, ast.FunctionDef):
                    method_info = {
                        "name": item.name,
                        "line": item.lineno,
                        "docstring": ast.get_docstring(item) or "",
                        "decorators": [],
                    }
                    for dec in item.decorator_list:
                        if hasattr(ast, "unparse"):
                            method_info["decorators"].append(ast.unparse(dec))
                        else:
                            method_info["decorators"].append(getattr(dec, "id", str(dec)))
                    cls_info["methods"].append(method_info)
            result["classes"].append(cls_info)

    # ── Functions ──
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef):
            func_info = {
                "name": node.name,
                "line": node.lineno,
                "docstring": ast.get_docstring(node) or "",
                "decorators": [],
            }
            for dec in node.decorator_list:
                if hasattr(ast, "unparse"):
                    func_info["decorators"].append(ast.unparse(dec))
                else:
                    func_info["decorators"].append(getattr(dec, "id", str(dec)))
            result["functions"].append(func_info)

    # ── Top-level variables (simple assignments) ──
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    result["variables"].append({
                        "name": target.id,
                        "line": node.lineno,
                    })

    return result


def _regex_extract_imports(content: str) -> list[dict]:
    """Fallback: regex-based import extraction for syntactically broken files."""
    imports = []
    for m in re.finditer(r'^(?:from\s+(\S+)\s+)?import\s+(.+)$', content, re.MULTILINE):
        module = m.group(1) or ""
        names = m.group(2)
        for name in re.split(r'\s*,\s*', names):
            name = name.strip()
            if " as " in name:
                name, alias = name.split(" as ", 1)
                imports.append({"name": name.strip(), "alias": alias.strip(), "module": module, "line": 0})
            else:
                imports.append({"name": name, "alias": None, "module": module, "line": 0})
    return imports


def _regex_extract_functions(content: str) -> list[dict]:
    """Fallback: regex-based function extraction."""
    functions = []
    for m in re.finditer(r'^def\s+(\w+)\s*\(', content, re.MULTILINE):
        functions.append({"name": m.group(1), "line": content[:m.start()].count("\n") + 1, "docstring": ""})
    return functions


def _regex_extract_classes(content: str) -> list[dict]:
    """Fallback: regex-based class extraction."""
    classes = []
    for m in re.finditer(r'^class\s+(\w+)', content, re.MULTILINE):
        classes.append({"name": m.group(1), "line": content[:m.start()].count("\n") + 1, "docstring": "", "methods": [], "bases": []})
    return classes


def _index_file(file_path: Path, root: Path) -> Optional[dict[str, Any]]:
    """Index a single file and return its structure info."""
    try:
        rel_path = str(file_path.relative_to(root))
        stat = file_path.stat()
        ext = file_path.suffix.lower()
        language = INDEXABLE_EXTS.get(ext, "text")

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        line_count = len(lines)

        # Detect shebang
        first_line = lines[0].strip() if lines else ""
        shebang = first_line if first_line.startswith("#!") else ""

        file_info: dict[str, Any] = {
            "path": rel_path,
            "language": language,
            "size": stat.st_size,
            "lines": line_count,
            "shebang": shebang,
            "imports": [],
            "classes": [],
            "functions": [],
            "variables": [],
            "errors": [],
        }

        # Parse based on language
        if ext == ".py":
            parsed = _parse_python_file(content, rel_path)
            file_info.update(parsed)
        else:
            # For non-Python files, use simple regex extraction
            file_info["imports"] = _regex_extract_imports(content)
            file_info["functions"] = _regex_extract_functions(content)
            file_info["classes"] = _regex_extract_classes(content)

        return file_info

    except (OSError, UnicodeDecodeError, MemoryError) as e:
        # Binary or unreadable file
        return None


def _compute_index_hash(root: Path, files: list[str]) -> str:
    """Compute a hash of the file list + modification times for cache invalidation."""
    hasher = hashlib.sha256()
    for f in sorted(files):
        fpath = root / f
        if fpath.exists():
            hasher.update(f.encode())
            hasher.update(str(fpath.stat().st_mtime).encode())
            hasher.update(str(fpath.stat().st_size).encode())
    return hasher.hexdigest()[:16]


def _get_index_cache_path(root: Path) -> Path:
    """Get the cache file path for the codebase index."""
    cache_dir = root / ".judecode"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "codebase_index.json"


def build_index(
    root: str = ".",
    force: bool = False,
    max_files: int = 10000,
    max_size_kb: int = 1024,  # Skip files larger than 1MB
) -> dict[str, Any]:
    """
    Build (or rebuild) the codebase index for the project.

    Args:
        root: Project root directory
        force: If True, rebuild even if cache is fresh
        max_files: Maximum number of files to index
        max_size_kb: Skip files larger than this (KB)

    Returns:
        Index dict with files, stats, and project overview
    """
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return {
            "error": f"Directory not found: {root}",
            "files": {},
            "stats": {"total_files": 0, "total_lines": 0},
            "indexed_at": time.time(),
        }

    gitignore_patterns = _load_gitignore(root_path)
    cache_path = _get_index_cache_path(root_path)

    # Check cache
    if not force and cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            # Verify cache is still valid (quick check: root matches)
            if cached.get("project_root") == str(root_path):
                return cached
        except (json.JSONDecodeError, OSError):
            pass  # Invalid cache, rebuild

    # ── Scan files ──
    indexable_files: list[Path] = []
    for file_path in root_path.rglob("*"):
        if not file_path.is_file():
            continue

        rel_path = str(file_path.relative_to(root_path))
        if _should_ignore(rel_path, gitignore_patterns):
            continue

        # Skip files that are too large
        try:
            if file_path.stat().st_size > max_size_kb * 1024:
                continue
        except OSError:
            continue

        ext = file_path.suffix.lower()
        if ext in BINARY_EXTENSIONS:
            continue

        indexable_files.append(file_path)

        if len(indexable_files) >= max_files:
            break

    # ── Index each file ──
    files_index: dict[str, Any] = {}
    total_lines = 0
    stats = {
        "total_files": 0,
        "total_lines": 0,
        "languages": {},
        "total_classes": 0,
        "total_functions": 0,
        "largest_files": [],
    }

    for file_path in indexable_files:
        file_info = _index_file(file_path, root_path)
        if file_info is None:
            continue

        rel_path = file_info["path"]
        files_index[rel_path] = file_info
        total_lines += file_info["lines"]

        # Update stats
        lang = file_info["language"]
        stats["languages"][lang] = stats["languages"].get(lang, 0) + 1
        stats["total_classes"] += len(file_info.get("classes", []))
        stats["total_functions"] += len(file_info.get("functions", []))

    stats["total_files"] = len(files_index)
    stats["total_lines"] = total_lines

    # Top 10 largest files
    sorted_files = sorted(files_index.items(), key=lambda x: x[1]["lines"], reverse=True)
    stats["largest_files"] = [
        {"path": p, "lines": info["lines"], "language": info["language"]}
        for p, info in sorted_files[:10]
    ]

    index_data = {
        "project_root": str(root_path),
        "project_name": root_path.name,
        "indexed_at": time.time(),
        "index_version": 2,
        "files": files_index,
        "stats": stats,
    }

    # ── Save cache ──
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # Cache save is best-effort

    return index_data


def load_index(root: str = ".") -> dict[str, Any]:
    """Load the cached codebase index, or build if not found."""
    root_path = Path(root).resolve()
    cache_path = _get_index_cache_path(root_path)

    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass

    return build_index(root=root)


def search_index(
    query: str,
    root: str = ".",
    max_results: int = 20,
) -> str:
    """
    Search the codebase index for files/classes/functions matching the query.

    This is a lightweight keyword search (no embeddings needed).
    Searches across:
    - File paths
    - Class names and docstrings
    - Function names and docstrings
    - Import names
    - Variable names

    Args:
        query: Search query (keywords separated by spaces)
        root: Project root directory
        max_results: Maximum results to return

    Returns:
        Formatted search results string
    """
    index = load_index(root)
    if "error" in index:
        return f"Error: {index['error']}"

    if not index.get("files"):
        return "No files indexed. Run `codebase_index build` first."

    keywords = query.lower().split()
    if not keywords:
        return "Please provide a search query."

    results: list[tuple[str, str, str, int]] = []
    # (file_path, match_type, description, relevance_score)

    for file_path, info in index["files"].items():
        file_lower = file_path.lower()

        # Check file path match
        if any(kw in file_lower for kw in keywords):
            desc = f"{file_path} ({info['language']}, {info['lines']} lines)"
            score = sum(kw in file_lower for kw in keywords) * 10
            results.append((file_path, "file", desc, score))

        # Check class names
        for cls in info.get("classes", []):
            cls_lower = cls["name"].lower()
            if any(kw in cls_lower for kw in keywords):
                desc = f"{file_path} → class {cls['name']} (line {cls['line']})"
                if cls.get("docstring"):
                    desc += f"\n  Doc: {cls['docstring'][:120]}"
                score = sum(kw in cls_lower for kw in keywords) * 8
                results.append((file_path, "class", desc, score))

            # Check class docstring
            if cls.get("docstring"):
                doc_lower = cls["docstring"].lower()
                if any(kw in doc_lower for kw in keywords):
                    desc = f"{file_path} → class {cls['name']} (line {cls['line']})"
                    score = sum(kw in doc_lower for kw in keywords) * 4
                    results.append((file_path, "class_doc", desc, score))

            # Check methods
            for method in cls.get("methods", []):
                method_lower = method["name"].lower()
                if any(kw in method_lower for kw in keywords):
                    desc = f"{file_path} → {cls['name']}.{method['name']}() (line {method['line']})"
                    score = sum(kw in method_lower for kw in keywords) * 7
                    results.append((file_path, "method", desc, score))

        # Check function names
        for func in info.get("functions", []):
            func_lower = func["name"].lower()
            if any(kw in func_lower for kw in keywords):
                desc = f"{file_path} → {func['name']}() (line {func['line']})"
                if func.get("docstring"):
                    desc += f"\n  Doc: {func['docstring'][:120]}"
                score = sum(kw in func_lower for kw in keywords) * 7
                results.append((file_path, "function", desc, score))

            # Check function docstring
            if func.get("docstring"):
                doc_lower = func["docstring"].lower()
                if any(kw in doc_lower for kw in keywords):
                    desc = f"{file_path} → {func['name']}() (line {func['line']})"
                    score = sum(kw in doc_lower for kw in keywords) * 4
                    results.append((file_path, "func_doc", desc, score))

        # Check imports
        for imp in info.get("imports", []):
            imp_lower = imp["name"].lower()
            if any(kw in imp_lower for kw in keywords):
                desc = f"{file_path} → imports {imp['name']}"
                score = sum(kw in imp_lower for kw in keywords) * 3
                results.append((file_path, "import", desc, score))

        # Check variables
        for var in info.get("variables", []):
            var_lower = var["name"].lower()
            if any(kw in var_lower for kw in keywords):
                desc = f"{file_path} → variable {var['name']} (line {var['line']})"
                score = sum(kw in var_lower for kw in keywords) * 2
                results.append((file_path, "variable", desc, score))

    # ── Sort by relevance score (descending) ──
    results.sort(key=lambda x: x[3], reverse=True)

    # ── Format output ──
    if not results:
        return f"No results found for: {query}"

    # Deduplicate: keep only the first (highest-scored) entry per file+type+name
    seen = set()
    unique_results = []
    for file_path, match_type, desc, score in results:
        key = (file_path, match_type, desc[:60])
        if key not in seen:
            seen.add(key)
            unique_results.append((file_path, match_type, desc, score))

    # Limit results
    unique_results = unique_results[:max_results]

    lines = [f"📦 Found {len(unique_results)} result(s) for '{query}':", ""]
    for file_path, match_type, desc, score in unique_results:
        icon = {
            "file": "📄", "class": "🏛️", "class_doc": "📝",
            "method": "🔧", "function": "⚙️", "func_doc": "📝",
            "import": "📦", "variable": "📊",
        }.get(match_type, "•")
        lines.append(f"  {icon} {desc}")

    lines.append("")
    lines.append(f"💡 Use `read` tool to open any file above.")

    return "\n".join(lines)


def get_project_summary(root: str = ".") -> str:
    """Get a high-level summary of the project structure from the index."""
    index = load_index(root)
    if "error" in index:
        return f"Error: {index['error']}"

    stats = index.get("stats", {})
    files = index.get("files", {})

    if not files:
        return (
            "No index found. Run `codebase_index build` first to scan the project.\n"
            "This will help me understand your codebase without reading every file."
        )

    total_files = stats.get("total_files", 0)
    total_lines = stats.get("total_lines", 0)
    total_classes = stats.get("total_classes", 0)
    total_functions = stats.get("total_functions", 0)
    languages = stats.get("languages", {})
    largest_files = stats.get("largest_files", [])
    project_name = index.get("project_name", "unknown")

    # Build language breakdown
    lang_total = sum(languages.values())
    lang_lines = []
    for lang, count in sorted(languages.items(), key=lambda x: x[1], reverse=True)[:10]:
        pct = (count / lang_total * 100) if lang_total > 0 else 0
        lang_lines.append(f"  • {lang}: {count} file(s) ({pct:.0f}%)")

    # File type summary
    ext_counts: dict[str, int] = {}
    for fp in files:
        ext = Path(fp).suffix.lower() or "(no ext)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
    top_exts = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    # Directory structure (top 2 levels)
    dirs: dict[str, int] = {}
    for fp in files:
        parts = Path(fp).parts
        if len(parts) >= 2:
            top_dir = parts[0]
            dirs[top_dir] = dirs.get(top_dir, 0) + 1

    dir_lines = []
    for d, count in sorted(dirs.items(), key=lambda x: x[1], reverse=True)[:15]:
        dir_lines.append(f"  📁 {d}/ ({count} file(s))")

    output = [
        f"📊 Project: {project_name}",
        f"   Root: {index.get('project_root', root)}",
        "",
        f"📈 Stats:",
        f"  • {total_files} file(s), {total_lines} line(s) of code",
        f"  • {total_classes} class(es), {total_functions} function(s)",
        "",
        f"🔤 Languages:",
        *lang_lines,
        "",
        f"📁 Top Directories:",
        *(dir_lines if dir_lines else ["  (root level only)"]),
        "",
    ]

    if largest_files:
        output.append(f"📏 Largest Files:")
        for lf in largest_files[:5]:
            output.append(f"  • {lf['path']} ({lf['lines']} lines, {lf['language']})")
        output.append("")

    output.append(f"💡 Tip: Use `codebase_search <query>` to find specific code.")
    output.append(f"💡 Tip: Use `codebase_index rebuild` to refresh the index.")

    return "\n".join(output)
