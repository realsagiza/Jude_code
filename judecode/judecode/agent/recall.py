"""Memory Recall System for JudeCode — makes memory actually USED, not just stored.

The existing CrossSessionMemory / DecisionLog store data to disk, but nothing
ever loads it back into the model's context. This module closes that loop:

1. MemoryRecall.build_preamble()  — called once at engine start; assembles a
   compact "what I already know" block that is appended to the system prompt:
     • JUDE.md            (per-project memory file, like CLAUDE.md)
     • User preferences   (global, ~/.judecode/memory/preferences.json)
     • Project context    (tech stack, conventions)
     • Recent sessions    (what was done last time in THIS project)
     • Learned patterns   (what worked / what failed)

2. PreferenceStore — persistent user preferences ("answer in Thai",
   "never ask before running tests", ...). The model saves these via the
   memory_save_preference tool so the user doesn't have to repeat themselves.

3. update_project_memory_file() — appends a session log entry to JUDE.md so
   the next session starts with "Recently done: ..." already in context.

Everything is size-capped so cheap models (GLM, etc.) don't drown in tokens.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from judecode.utils.logger import get_logger

logger = get_logger("judecode.recall")

MEMORY_DIR = Path.home() / ".judecode" / "memory"
PROJECT_MEMORY_FILENAME = "JUDE.md"

# Size caps (chars) — keep the preamble cheap for low-cost models
_CAP_PROJECT_FILE = 3000
_CAP_PREFERENCES = 800
_CAP_SESSIONS = 1200
_CAP_PATTERNS = 800
_CAP_TOTAL = 6000


# ═══════════════════════════════════════════════════════════════
#  User Preferences (global, cross-project)
# ═══════════════════════════════════════════════════════════════

class PreferenceStore:
    """Persistent user preferences so the user never repeats instructions."""

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self.file = MEMORY_DIR / "preferences.json"

    def _load(self) -> list[dict]:
        if not self.file.exists():
            return []
        try:
            return json.loads(self.file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save(self, prefs: list[dict]) -> None:
        self.file.write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add(self, preference: str, category: str = "general") -> str:
        prefs = self._load()
        # Dedupe (case-insensitive substring match)
        low = preference.lower().strip()
        for p in prefs:
            if p["text"].lower().strip() == low:
                return "Preference already saved."
        prefs.append({
            "text": preference.strip(),
            "category": category,
            "created_at": datetime.now().isoformat(),
        })
        self._save(prefs)
        return f"✅ Preference saved: {preference[:60]}"

    def remove(self, keyword: str) -> str:
        prefs = self._load()
        kept = [p for p in prefs if keyword.lower() not in p["text"].lower()]
        removed = len(prefs) - len(kept)
        self._save(kept)
        return f"Removed {removed} preference(s) matching '{keyword}'."

    def list_all(self) -> list[dict]:
        return self._load()

    def as_text(self, cap: int = _CAP_PREFERENCES) -> str:
        prefs = self._load()
        if not prefs:
            return ""
        lines = [f"- {p['text']}" for p in prefs]
        text = "\n".join(lines)
        return text[:cap]


# ═══════════════════════════════════════════════════════════════
#  Per-project memory file (JUDE.md)
# ═══════════════════════════════════════════════════════════════

def get_project_memory_path(cwd: Optional[str] = None) -> Path:
    return Path(cwd or os.getcwd()) / PROJECT_MEMORY_FILENAME

def read_project_memory(cwd: Optional[str] = None) -> str:
    """Read JUDE.md from the project root (if present)."""
    path = get_project_memory_path(cwd)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")[:_CAP_PROJECT_FILE]
    except Exception:
        return ""


_SESSION_LOG_HEADER = "## Session Log (auto-updated by Jude)"
_MAX_SESSION_LOG_ENTRIES = 10


def update_project_memory_file(
    summary: str,
    cwd: Optional[str] = None,
) -> str:
    """Append a session summary line to JUDE.md's Session Log section.

    Creates JUDE.md if it doesn't exist. Keeps only the last
    _MAX_SESSION_LOG_ENTRIES entries so the file stays small.
    """
    path = get_project_memory_path(cwd)
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- [{date}] {summary.strip()}"

    try:
        if path.exists():
            content = path.read_text(encoding="utf-8")
        else:
            project_name = Path(cwd or os.getcwd()).name
            content = (
                f"# {project_name} — Project Memory\n\n"
                "Notes Jude should always remember about this project.\n"
                "(Edit freely — Jude reads this at the start of every session.)\n\n"
                f"{_SESSION_LOG_HEADER}\n"
            )

        if _SESSION_LOG_HEADER not in content:
            content = content.rstrip() + f"\n\n{_SESSION_LOG_HEADER}\n"

        head, _, log_section = content.partition(_SESSION_LOG_HEADER)
        log_lines = [
            l for l in log_section.strip().splitlines() if l.strip().startswith("- ")
        ]
        log_lines.append(entry)
        log_lines = log_lines[-_MAX_SESSION_LOG_ENTRIES:]

        new_content = (
            head.rstrip() + "\n\n" + _SESSION_LOG_HEADER + "\n" + "\n".join(log_lines) + "\n"
        )
        path.write_text(new_content, encoding="utf-8")
        return f"Session logged to {path.name}"
    except Exception as e:
        logger.debug(f"Failed to update project memory file: {e}")
        return f"Could not update {PROJECT_MEMORY_FILENAME}: {e}"


def add_project_note(note: str, cwd: Optional[str] = None) -> str:
    """Add a permanent note (above the session log) to JUDE.md."""
    path = get_project_memory_path(cwd)
    try:
        if path.exists():
            content = path.read_text(encoding="utf-8")
        else:
            update_project_memory_file("(memory file created)", cwd)
            content = path.read_text(encoding="utf-8")

        note_line = f"- {note.strip()}"
        if note_line in content:
            return "Note already exists in project memory."

        if _SESSION_LOG_HEADER in content:
            head, _, tail = content.partition(_SESSION_LOG_HEADER)
            content = head.rstrip() + f"\n{note_line}\n\n" + _SESSION_LOG_HEADER + tail
        else:
            content = content.rstrip() + f"\n{note_line}\n"

        path.write_text(content, encoding="utf-8")
        return f"✅ Note saved to {path.name}: {note[:60]}"
    except Exception as e:
        return f"Could not save note: {e}"


# ═══════════════════════════════════════════════════════════════
#  Memory Recall — build the startup preamble
# ═══════════════════════════════════════════════════════════════

class MemoryRecall:
    """Assembles everything Jude already knows into a compact context block."""

    def __init__(self, memory=None, cwd: Optional[str] = None):
        # `memory` is a CrossSessionMemory instance (passed in to avoid cycle)
        self.memory = memory
        self.cwd = cwd or os.getcwd()
        self.preferences = PreferenceStore()

    def _project_sessions_text(self, limit: int = 5) -> str:
        """Recent session summaries — filtered to the current project if possible."""
        if self.memory is None:
            return ""
        try:
            summaries = self.memory.get_session_summaries(limit=25)
        except Exception:
            return ""
        if not summaries:
            return ""

        lines = []
        for s in summaries[-limit * 3:]:
            goal = (s.get("goal") or "").strip().replace("\n", " ")
            if not goal:
                continue
            ts = (s.get("timestamp") or "")[:10]
            done = s.get("completed_tasks") or 0
            if not isinstance(done, int):
                done = len(done)
            suffix = f" ({done} tasks done)" if done else ""
            lines.append(f"- [{ts}] {goal[:120]}{suffix}")

        lines = lines[-limit:]
        return "\n".join(lines)[:_CAP_SESSIONS]

    def _project_context_text(self) -> str:
        if self.memory is None:
            return ""
        try:
            ctx = self.memory.get_project_context(self.cwd)
        except Exception:
            return ""
        if not ctx:
            return ""
        parts = []
        if ctx.get("tech_stack"):
            parts.append(f"Tech stack: {', '.join(ctx['tech_stack'][:10])}")
        conventions = ctx.get("conventions") or {}
        notable = {k: v for k, v in conventions.items() if k != "last_session"}
        if notable:
            parts.append(f"Conventions: {json.dumps(notable, ensure_ascii=False)[:300]}")
        return "\n".join(parts)

    def _patterns_text(self, limit: int = 5) -> str:
        if self.memory is None:
            return ""
        try:
            fails = self.memory.get_failed_patterns(limit=limit)
            wins = self.memory.get_successful_patterns(limit=limit)
        except Exception:
            return ""
        lines = []
        for p in wins[-3:]:
            lines.append(f"- ✅ WORKS: {p.get('pattern', '')[:100]}")
        for p in fails[-3:]:
            lines.append(f"- ❌ AVOID: {p.get('pattern', '')[:100]}")
        return "\n".join(lines)[:_CAP_PATTERNS]

    def build_preamble(self) -> str:
        """Build the memory preamble to append to the system prompt.

        Returns "" if there's nothing to recall (first-ever run).
        """
        sections = []

        proj_mem = read_project_memory(self.cwd)
        if proj_mem.strip():
            sections.append(f"## Project Memory ({PROJECT_MEMORY_FILENAME})\n{proj_mem.strip()}")

        prefs = self.preferences.as_text()
        if prefs:
            sections.append(f"## User Preferences (always follow these)\n{prefs}")

        ctx = self._project_context_text()
        if ctx:
            sections.append(f"## Known Project Context\n{ctx}")

        sessions = self._project_sessions_text()
        if sessions:
            sections.append(f"## Recent Sessions (already done — don't redo)\n{sessions}")

        patterns = self._patterns_text()
        if patterns:
            sections.append(f"## Learned Patterns\n{patterns}")

        if not sections:
            return ""

        body = "\n\n".join(sections)
        preamble = (
            "\n\n═══ MEMORY (loaded from previous sessions) ═══\n"
            + body
            + "\n\nMemory rules:\n"
            "- Use this memory. Do NOT ask the user things already answered here.\n"
            "- Do NOT redo work listed in Recent Sessions unless asked.\n"
            "- When the user states a lasting preference, save it with memory_save_preference.\n"
            "- When you learn something important about this project, save it with memory_add_note.\n"
            "- Use memory_recall to search older memories when unsure.\n"
        )
        return preamble[:_CAP_TOTAL]


# ═══════════════════════════════════════════════════════════════
#  Unified memory search (used by memory_recall tool)
# ═══════════════════════════════════════════════════════════════

def recall_search(query: str, memory=None, limit: int = 8) -> str:
    """Search across ALL memory stores and return a readable digest."""
    results = []

    # 1. Preferences
    prefs = PreferenceStore().list_all()
    for p in prefs:
        if query.lower() in p["text"].lower():
            results.append(f"[preference] {p['text']}")

    # 2. Project memory file
    proj = read_project_memory()
    if proj:
        for line in proj.splitlines():
            if query.lower() in line.lower() and line.strip():
                results.append(f"[JUDE.md] {line.strip()[:150]}")

    # 3. Session summaries + patterns (CrossSessionMemory)
    if memory is not None:
        try:
            for s in memory.get_session_summaries(limit=50):
                goal = s.get("goal") or ""
                if query.lower() in goal.lower():
                    ts = (s.get("timestamp") or "")[:10]
                    results.append(f"[session {ts}] {goal[:150]}")
        except Exception:
            pass
        try:
            for p in memory.search_patterns(query, limit=10):
                results.append(
                    f"[pattern/{p.get('result', '?')}] {p.get('pattern', '')[:150]}"
                )
        except Exception:
            pass

    # 4. Decision logs (all sessions)
    try:
        from judecode.agent.memory import DecisionLog
        for d in DecisionLog.search_all_sessions(query, limit=10):
            task = d.get("task", "")
            learn = d.get("learnings", "")
            results.append(f"[decision/{d.get('result', '?')}] {task[:80]}"
                           + (f" → {learn[:100]}" if learn else ""))
    except Exception:
        pass

    if not results:
        return f"No memories found for '{query}'."

    # Dedupe, cap
    seen, out = set(), []
    for r in results:
        if r not in seen:
            seen.add(r)
            out.append(r)
        if len(out) >= limit * 2:
            break

    return f"🧠 Memories matching '{query}':\n" + "\n".join(out[:limit * 2])
