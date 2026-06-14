"""
Decision Log & Cross-Session Memory for JudeCode — Phase 2

2.3 Decision Log:
  - Records every significant decision the agent makes
  - Includes: task, strategy, result, learnings
  - Searchable for future reference

2.4 Cross-Session Memory:
  - Session summaries stored in Vault
  - Project context (tech stack, conventions, structure)
  - Learned patterns (what worked, what didn't)

These enable the agent to learn from past sessions and avoid
repeating mistakes.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from judecode.utils.logger import get_logger

logger = get_logger("judecode.decision_log")


# ═══════════════════════════════════════════════════════════════
#  2.3 Decision Log
# ═══════════════════════════════════════════════════════════════

class DecisionLog:
    """Log agent decisions with reasoning and outcomes.

    Each entry records:
    - What task was being worked on
    - What strategy/approach was chosen
    - What the result was (pass/fail/partial)
    - What was learned (for future reference)

    Stored in ~/.judecode/decisions/<session_id>.jsonl
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.decisions_dir = Path.home() / ".judecode" / "decisions"
        self.decisions_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = self.decisions_dir / f"{self.session_id}.jsonl"
        self._counter = 0

    def record(
        self,
        task: str,
        strategy: str,
        result: str = "pending",
        learnings: str = "",
        task_id: Optional[int] = None,
        tags: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Record a decision entry.

        Args:
            task: What was being worked on
            strategy: What approach/strategy was chosen
            result: Outcome (pass, fail, partial, pending)
            learnings: What was learned from this decision
            task_id: Associated task ID
            tags: Categorization tags

        Returns:
            The decision entry dict
        """
        self._counter += 1
        entry = {
            "id": self._counter,
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "task": task,
            "strategy": strategy,
            "result": result,
            "learnings": learnings,
            "task_id": task_id,
            "tags": tags or [],
        }

        # Append as JSONL (one JSON per line)
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return entry

    def update_result(self, decision_id: int, result: str, learnings: str = "") -> bool:
        """Update the result of a previously recorded decision."""
        if not self.log_file.exists():
            return False

        entries = []
        found = False
        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("id") == decision_id:
                        entry["result"] = result
                        if learnings:
                            entry["learnings"] = learnings
                        entry["updated_at"] = datetime.now().isoformat()
                        found = True
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue

        if found:
            with open(self.log_file, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return found

    def get_entries(
        self,
        result: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get decision entries, optionally filtered."""
        if not self.log_file.exists():
            return []

        entries = []
        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if result and entry.get("result") != result:
                        continue
                    if tag and tag not in entry.get("tags", []):
                        continue
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue

        return entries[-limit:]

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search decisions by text content."""
        if not self.log_file.exists():
            return []

        query_lower = query.lower()
        results = []
        with open(self.log_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    text = f"{entry.get('task', '')} {entry.get('strategy', '')} {entry.get('learnings', '')}"
                    if query_lower in text.lower():
                        results.append(entry)
                except json.JSONDecodeError:
                    continue

        return results[-limit:]

    def get_learnings(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get all decisions that have learnings."""
        return [
            e for e in self.get_entries(limit=100)
            if e.get("learnings")
        ][-limit:]

    def get_summary(self) -> str:
        """Get a summary of all decisions in this session."""
        entries = self.get_entries(limit=100)
        if not entries:
            return "No decisions recorded yet."

        passed = sum(1 for e in entries if e.get("result") == "pass")
        failed = sum(1 for e in entries if e.get("result") == "fail")
        pending = sum(1 for e in entries if e.get("result") == "pending")
        with_learnings = sum(1 for e in entries if e.get("learnings"))

        return (
            f"📋 Decisions: {len(entries)} total\n"
            f"   ✅ Pass: {passed} | ❌ Fail: {failed} | ⏳ Pending: {pending}\n"
            f"   💡 With learnings: {with_learnings}"
        )

    @classmethod
    def search_all_sessions(cls, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search decisions across all sessions."""
        decisions_dir = Path.home() / ".judecode" / "decisions"
        if not decisions_dir.exists():
            return []

        results = []
        query_lower = query.lower()
        for log_file in decisions_dir.glob("*.jsonl"):
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        text = f"{entry.get('task', '')} {entry.get('strategy', '')} {entry.get('learnings', '')}"
                        if query_lower in text.lower():
                            results.append(entry)
                    except json.JSONDecodeError:
                        continue

        return results[-limit:]


# ═══════════════════════════════════════════════════════════════
#  2.4 Cross-Session Memory
# ═══════════════════════════════════════════════════════════════

class CrossSessionMemory:
    """Store and retrieve knowledge across sessions.

    Three types of memory:
    1. Session Summaries — what was done in each session
    2. Project Context — tech stack, conventions, structure
    3. Learned Patterns — what worked and what didn't

    All stored in ~/.judecode/memory/
    """

    def __init__(self):
        self.memory_dir = Path.home() / ".judecode" / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Sub-directories
        self.summaries_dir = self.memory_dir / "session_summaries"
        self.context_dir = self.memory_dir / "project_context"
        self.patterns_dir = self.memory_dir / "learned_patterns"

        for d in [self.summaries_dir, self.context_dir, self.patterns_dir]:
            d.mkdir(exist_ok=True)

    # ── Session Summaries ──

    def save_session_summary(
        self,
        session_id: str,
        goal: str,
        completed_tasks: list[int],
        total_tasks: int,
        duration: str = "",
        key_decisions: Optional[list[dict]] = None,
        errors_encountered: Optional[list[str]] = None,
    ) -> str:
        """Save a summary of a completed session."""
        summary = {
            "session_id": session_id,
            "timestamp": datetime.now().isoformat(),
            "goal": goal,
            "completed_tasks": len(completed_tasks),
            "total_tasks": total_tasks,
            "completion_rate": len(completed_tasks) / max(total_tasks, 1),
            "duration": duration,
            "key_decisions": key_decisions or [],
            "errors_encountered": errors_encountered or [],
        }

        # Save as JSON
        summary_file = self.summaries_dir / f"{session_id}.json"
        summary_file.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2)
        )

        # Also append to all_summaries.jsonl for quick search
        all_file = self.summaries_dir / "all_summaries.jsonl"
        with open(all_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")

        return f"Session summary saved: {session_id}"

    def get_session_summaries(self, limit: int = 10) -> list[dict]:
        """Get recent session summaries."""
        all_file = self.summaries_dir / "all_summaries.jsonl"
        if not all_file.exists():
            return []

        summaries = []
        with open(all_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    summaries.append(json.loads(line.strip()))
                except json.JSONDecodeError:
                    continue

        return summaries[-limit:]

    # ── Project Context ──

    def save_project_context(
        self,
        project_path: str,
        tech_stack: Optional[list[str]] = None,
        conventions: Optional[dict] = None,
        structure: Optional[dict] = None,
    ) -> str:
        """Save or update project context."""
        # Use a safe filename from project path
        safe_name = project_path.replace("/", "_").replace("\\", "_").strip("_")
        context_file = self.context_dir / f"{safe_name}.json"

        # Load existing context and merge
        existing = {}
        if context_file.exists():
            try:
                existing = json.loads(context_file.read_text())
            except Exception:
                pass

        context = {
            "project_path": project_path,
            "updated_at": datetime.now().isoformat(),
            "tech_stack": tech_stack or existing.get("tech_stack", []),
            "conventions": {**existing.get("conventions", {}), **(conventions or {})},
            "structure": structure or existing.get("structure", {}),
            "access_count": existing.get("access_count", 0) + 1,
        }

        context_file.write_text(
            json.dumps(context, ensure_ascii=False, indent=2)
        )

        return f"Project context saved: {safe_name}"

    def get_project_context(self, project_path: str) -> Optional[dict]:
        """Get project context."""
        safe_name = project_path.replace("/", "_").replace("\\", "_").strip("_")
        context_file = self.context_dir / f"{safe_name}.json"

        if not context_file.exists():
            return None

        try:
            context = json.loads(context_file.read_text())
            # Update access count
            context["access_count"] = context.get("access_count", 0) + 1
            context["last_accessed"] = datetime.now().isoformat()
            context_file.write_text(
                json.dumps(context, ensure_ascii=False, indent=2)
            )
            return context
        except Exception:
            return None

    # ── Learned Patterns ──

    def save_pattern(
        self,
        pattern: str,
        context: str,
        result: str,
        category: str = "general",
    ) -> str:
        """Save a learned pattern.

        Args:
            pattern: The pattern/approach that was used
            context: When/where this pattern applies
            result: Whether it worked (pass/fail/partial)
            category: Category (debugging, refactoring, testing, etc.)
        """
        entry = {
            "pattern": pattern,
            "context": context,
            "result": result,
            "category": category,
            "timestamp": datetime.now().isoformat(),
        }

        # Append to category file
        cat_file = self.patterns_dir / f"{category}.jsonl"
        with open(cat_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return f"Pattern saved: {category}/{pattern[:30]}..."

    def get_patterns(
        self,
        category: Optional[str] = None,
        result: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Get learned patterns, optionally filtered."""
        patterns = []

        if category:
            cat_files = [self.patterns_dir / f"{category}.jsonl"]
        else:
            cat_files = list(self.patterns_dir.glob("*.jsonl"))

        for cat_file in cat_files:
            if not cat_file.exists():
                continue
            with open(cat_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if result and entry.get("result") != result:
                            continue
                        patterns.append(entry)
                    except json.JSONDecodeError:
                        continue

        return patterns[-limit:]

    def search_patterns(self, query: str, limit: int = 10) -> list[dict]:
        """Search patterns by text content."""
        query_lower = query.lower()
        results = []

        for cat_file in self.patterns_dir.glob("*.jsonl"):
            with open(cat_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        text = f"{entry.get('pattern', '')} {entry.get('context', '')}"
                        if query_lower in text.lower():
                            results.append(entry)
                    except json.JSONDecodeError:
                        continue

        return results[-limit:]

    def get_successful_patterns(self, category: Optional[str] = None, limit: int = 10) -> list[dict]:
        """Get patterns that worked (result=pass)."""
        return self.get_patterns(category=category, result="pass", limit=limit)

    def get_failed_patterns(self, category: Optional[str] = None, limit: int = 10) -> list[dict]:
        """Get patterns that failed — to avoid repeating mistakes."""
        return self.get_patterns(category=category, result="fail", limit=limit)
