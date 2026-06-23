"""
Autonomous Controller for JudeCode — Phase 1: Autonomous MVP

Handles:
  1.1 Auto-Advance Task Queue — after task_complete, auto-start next task
  1.2 State Persistence — save/restore session progress via Vault
  1.3 Self-Evaluation Loop — verify after task, auto-retry on failure
  1.4 Budget & Safety Guardrails — token/cost tracking, circuit breaker

This module is called by engine.py after tool execution to decide
whether to auto-continue, nudge, or stop.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from judecode.utils.logger import get_logger

logger = get_logger("judecode.autonomous")


# ═══════════════════════════════════════════════════════════════
#  1.1 Auto-Advance Task Queue
# ═══════════════════════════════════════════════════════════════

def check_auto_advance(tool_name: str, tool_result: str) -> Optional[str]:
    """After task_complete, check if there's a next task and return a nudge.

    Returns a nudge message if auto-advance should happen, or None.
    This is called by engine.py after each tool execution.
    """
    if tool_name != "task_complete":
        return None

    # Only auto-advance on successful completion
    if "❌" in tool_result or "not found" in tool_result.lower():
        return None

    # Import here to avoid circular imports
    from judecode.utils.task_tools import _get_manager

    try:
        mgr = _get_manager()
        if mgr is None:
            return None

        # Get next pending task
        next_task = mgr.next_task()
        if next_task is None:
            # No more tasks — check if all are done
            summary = mgr.get_summary()
            done = summary.get("done", 0)
            total = summary.get("total", 0)
            if done == total and total > 0:
                return (
                    "[SYSTEM: 🎉 All tasks completed! "
                    "Provide a final summary of what was accomplished.]"
                )
            return None

        # Auto-start the next task
        try:
            mgr.start_task(next_task.id)
        except Exception:
            pass  # Task might already be started

        return (
            f"[SYSTEM: ✅ Task completed. Auto-advancing to next task: "
            f"[{next_task.id}] {next_task.title} "
            f"(priority: {next_task.priority}). "
            f"Please start working on it now.]"
        )

    except Exception as e:
        logger.debug(f"Auto-advance check failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  1.2 State Persistence
# ═══════════════════════════════════════════════════════════════

class SessionState:
    """Persist session state to disk for crash recovery and resume.

    Saves to ~/.judecode/session_state.json after each significant event.
    On startup, checks for incomplete sessions and offers to resume.
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.state_dir = Path.home() / ".judecode" / "sessions"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / f"{self.session_id}.json"

        # Runtime state
        self.started_at = datetime.now().isoformat()
        self.last_saved_at: Optional[str] = None
        self.original_goal: str = ""
        self.completed_tasks: list[int] = []
        self.current_task_id: Optional[int] = None
        self.total_turns: int = 0
        self.total_tool_calls: int = 0
        self.errors: list[dict[str, Any]] = []
        self.status: str = "active"  # active | paused | completed | crashed

    def save(self, **extra) -> None:
        """Save current state to disk."""
        self.last_saved_at = datetime.now().isoformat()
        state = {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "last_saved_at": self.last_saved_at,
            "original_goal": self.original_goal,
            "completed_tasks": self.completed_tasks,
            "current_task_id": self.current_task_id,
            "total_turns": self.total_turns,
            "total_tool_calls": self.total_tool_calls,
            "errors": self.errors,
            "status": self.status,
            **extra,
        }
        try:
            self.state_file.write_text(
                json.dumps(state, ensure_ascii=False, indent=2)
            )
            logger.debug(f"Session state saved: {self.state_file}")
        except Exception as e:
            logger.warning(f"Failed to save session state: {e}")

    def mark_completed(self) -> None:
        """Mark session as successfully completed."""
        self.status = "completed"
        self.save()

    def mark_crashed(self, error: str = "") -> None:
        """Mark session as crashed (for recovery on next startup)."""
        self.status = "crashed"
        if error:
            self.errors.append({
                "timestamp": datetime.now().isoformat(),
                "error": error,
            })
        self.save()

    def record_task_complete(self, task_id: int) -> None:
        """Record a completed task."""
        if task_id not in self.completed_tasks:
            self.completed_tasks.append(task_id)
        self.current_task_id = None
        self.save()

    def record_task_start(self, task_id: int) -> None:
        """Record starting a task."""
        self.current_task_id = task_id
        self.save()

    def record_error(self, error: str) -> None:
        """Record an error."""
        self.errors.append({
            "timestamp": datetime.now().isoformat(),
            "error": error,
        })
        self.save()

    def get_progress_summary(self) -> str:
        """Get a human-readable progress summary."""
        lines = [
            f"📋 Session: {self.session_id}",
            f"🎯 Goal: {self.original_goal or '(not set)'}",
            f"✅ Completed: {len(self.completed_tasks)} tasks",
            f"🔄 Current: Task #{self.current_task_id}" if self.current_task_id else "🔄 Current: None",
            f"📊 Turns: {self.total_turns} | Tool calls: {self.total_tool_calls}",
            f"❌ Errors: {len(self.errors)}",
            f"📡 Status: {self.status}",
        ]
        return "\n".join(lines)

    @classmethod
    def find_incomplete_sessions(cls) -> list["SessionState"]:
        """Find all incomplete (active/crashed) sessions for resume."""
        sessions_dir = Path.home() / ".judecode" / "sessions"
        if not sessions_dir.exists():
            return []

        incomplete = []
        for f in sessions_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if data.get("status") in ("active", "crashed"):
                    session = cls(session_id=data["session_id"])
                    session.started_at = data.get("started_at", "")
                    session.original_goal = data.get("original_goal", "")
                    session.completed_tasks = data.get("completed_tasks", [])
                    session.current_task_id = data.get("current_task_id")
                    session.total_turns = data.get("total_turns", 0)
                    session.total_tool_calls = data.get("total_tool_calls", 0)
                    session.errors = data.get("errors", [])
                    session.status = data.get("status", "active")
                    incomplete.append(session)
            except Exception:
                continue

        return incomplete

    @classmethod
    def load(cls, session_id: str) -> Optional["SessionState"]:
        """Load a specific session by ID."""
        state_file = Path.home() / ".judecode" / "sessions" / f"{session_id}.json"
        if not state_file.exists():
            return None
        try:
            data = json.loads(state_file.read_text())
            session = cls(session_id=data["session_id"])
            session.started_at = data.get("started_at", "")
            session.original_goal = data.get("original_goal", "")
            session.completed_tasks = data.get("completed_tasks", [])
            session.current_task_id = data.get("current_task_id")
            session.total_turns = data.get("total_turns", 0)
            session.total_tool_calls = data.get("total_tool_calls", 0)
            session.errors = data.get("errors", [])
            session.status = data.get("status", "active")
            session.last_saved_at = data.get("last_saved_at")
            return session
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════
#  1.3 Self-Evaluation Loop
# ═══════════════════════════════════════════════════════════════

# Verification commands to try after task completion (in order of priority)
DEFAULT_VERIFY_COMMANDS = [
    # Python projects
    {"pattern": "pytest", "cmd": "cd {root} && python -m pytest --tb=short -q 2>&1 | head -50"},
    {"pattern": "unittest", "cmd": "cd {root} && python -m unittest discover -q 2>&1 | head -50"},
    {"pattern": "tox", "cmd": "cd {root} && tox -q 2>&1 | head -50"},
    # JavaScript/TypeScript projects
    {"pattern": "jest", "cmd": "cd {root} && npx jest --no-coverage 2>&1 | head -50"},
    {"pattern": "vitest", "cmd": "cd {root} && npx vitest run 2>&1 | head -50"},
    # Linting
    {"pattern": "ruff", "cmd": "cd {root} && ruff check . 2>&1 | head -30"},
    {"pattern": "eslint", "cmd": "cd {root} && npx eslint . 2>&1 | head -30"},
    # Build check
    {"pattern": "build", "cmd": "cd {root} && python -c 'import {module}' 2>&1"},
]

# Max auto-retries before asking human
MAX_SELF_EVAL_RETRIES = 3


class SelfEvaluator:
    """Run verification after task completion and auto-retry on failure."""

    def __init__(self, project_root: str = ".", max_retries: int = MAX_SELF_EVAL_RETRIES):
        self.project_root = project_root
        self.max_retries = max_retries
        self.retry_counts: dict[int, int] = {}  # task_id -> retry count
        self.last_verify_result: Optional[str] = None

    def should_verify(self, tool_name: str, tool_result: str) -> bool:
        """Check if we should run verification after this tool call.

        Run verification after:
        - task_complete (always)
        - write/edit that modifies code files (sometimes)
        """
        if tool_name == "task_complete":
            return True
        # Don't verify on every write/edit (too noisy), only on task_complete
        return False

    def run_verification(self) -> dict[str, Any]:
        """Run available verification commands and return results.

        Returns:
            {
                "passed": bool,
                "results": [{"cmd": str, "output": str, "passed": bool}],
                "summary": str,
            }
        """
        results = []
        root = self.project_root

        # Detect what verification tools are available
        for vcmd in DEFAULT_VERIFY_COMMANDS:
            # Skip if config file doesn't exist
            if vcmd["pattern"] == "pytest":
                if not os.path.exists(os.path.join(root, "pytest.ini")) and \
                   not os.path.exists(os.path.join(root, "pyproject.toml")) and \
                   not os.path.exists(os.path.join(root, "setup.cfg")):
                    continue
            elif vcmd["pattern"] in ("jest", "vitest"):
                if not os.path.exists(os.path.join(root, "package.json")):
                    continue
            elif vcmd["pattern"] == "ruff":
                if not os.path.exists(os.path.join(root, "pyproject.toml")):
                    continue
            elif vcmd["pattern"] == "eslint":
                if not os.path.exists(os.path.join(root, "package.json")):
                    continue
            else:
                continue  # Skip unknown patterns

            cmd = vcmd["cmd"].format(root=root, module=root.replace("/", "."))
            try:
                import subprocess
                proc = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=60
                )
                output = proc.stdout + proc.stderr
                passed = proc.returncode == 0
                results.append({
                    "cmd": vcmd["pattern"],
                    "output": output[:2000],  # Truncate long output
                    "passed": passed,
                })
            except subprocess.TimeoutExpired:
                results.append({
                    "cmd": vcmd["pattern"],
                    "output": "TIMEOUT (60s)",
                    "passed": False,
                })
            except Exception as e:
                results.append({
                    "cmd": vcmd["pattern"],
                    "output": f"Error: {e}",
                    "passed": False,
                })

        all_passed = all(r["passed"] for r in results) if results else True

        # Build summary
        if not results:
            summary = "No verification commands found for this project."
        elif all_passed:
            summary = "✅ All verifications passed!"
        else:
            failed = [r for r in results if not r["passed"]]
            summary = f"❌ {len(failed)}/{len(results)} verification(s) failed:\n"
            for f in failed:
                summary += f"  - {f['cmd']}: {f['output'][:200]}\n"

        self.last_verify_result = summary
        return {
            "passed": all_passed,
            "results": results,
            "summary": summary,
        }

    def should_auto_retry(self, task_id: int) -> bool:
        """Check if we should auto-retry after verification failure."""
        count = self.retry_counts.get(task_id, 0)
        return count < self.max_retries

    def record_retry(self, task_id: int) -> None:
        """Record a retry attempt for a task."""
        self.retry_counts[task_id] = self.retry_counts.get(task_id, 0) + 1

    def get_retry_nudge(self, task_id: int, verify_result: dict[str, Any]) -> str:
        """Generate a nudge message for auto-retry."""
        retry_count = self.retry_counts.get(task_id, 0) + 1
        return (
            f"[SYSTEM: ⚠️ Verification failed after task completion. "
            f"Auto-retry attempt {retry_count}/{self.max_retries}. "
            f"Please fix the issues and try again.\n\n"
            f"Verification results:\n{verify_result['summary']}]"
        )


# ═══════════════════════════════════════════════════════════════
#  1.4 Budget & Safety Guardrails
# ═══════════════════════════════════════════════════════════════

class BudgetTracker:
    """Track token usage and estimated cost per session.

    Integrates with the API client to count tokens from usage metadata.
    Circuit breaker stops the session if too many consecutive errors occur.

    Tracks tokens by category for detailed visibility:
    - system_prompt: The static system instructions
    - input_messages: User messages + conversation history (input side)
    - output_messages: Assistant response text (output side)
    - tool_requests: Tool call definitions sent to API
    - tool_results: Tool execution results fed back into context
    - nudge_messages: System auto-nudge / health-check messages
    """

    # Default cost per 1M tokens (approximate, varies by model)
    DEFAULT_INPUT_COST_PER_M = 3.0    # $3 per 1M input tokens
    DEFAULT_OUTPUT_COST_PER_M = 15.0  # $15 per 1M output tokens

    # Category definitions with display info
    CATEGORIES = {
        "system_prompt":    {"icon": "🧠", "label": "System Prompt",     "is_input": True},
        "input_messages":   {"icon": "💬", "label": "Conversation In",   "is_input": True},
        "output_messages":  {"icon": "🤖", "label": "Assistant Output",  "is_input": False},
        "tool_requests":    {"icon": "🔧", "label": "Tool Requests",     "is_input": True},
        "tool_results":     {"icon": "📦", "label": "Tool Results",      "is_input": True},
        "nudge_messages":   {"icon": "⏰", "label": "Auto-Nudges",       "is_input": True},
    }

    def __init__(
        self,
        max_cost: float = 10.0,        # Max $ per session
        max_tokens: int = 2_000_000,    # Max total tokens per session
        max_consecutive_errors: int = 5, # Circuit breaker threshold
        max_error_rate: float = 0.5,     # Stop if >50% of recent turns are errors
        error_window: int = 10,          # Look at last N turns for error rate
    ):
        self.max_cost = max_cost
        self.max_tokens = max_tokens
        self.max_consecutive_errors = max_consecutive_errors
        self.max_error_rate = max_error_rate
        self.error_window = error_window

        # Legacy totals (kept for circuit breaker & backward compat)
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.consecutive_errors = 0
        self.recent_results: list[bool] = []  # True=success, False=error
        self.circuit_breaker_triggered = False
        self.circuit_breaker_reason: str = ""

        # Per-category token tracking
        self.tokens: dict[str, int] = {cat: 0 for cat in self.CATEGORIES}

        # Detailed audit log (last N entries for debugging)
        self.audit_log: list[dict] = []  # {category, tokens, note, timestamp}
        self._max_audit_entries = 200

        # Turn counter for per-turn stats
        self.turn_count: int = 0
        self.tokens_this_turn: int = 0  # total tokens consumed in current turn

        # Cost rates (can be overridden per model)
        self.input_cost_per_m = self.DEFAULT_INPUT_COST_PER_M
        self.output_cost_per_m = self.DEFAULT_OUTPUT_COST_PER_M

    def set_model_rates(self, input_per_m: float, output_per_m: float) -> None:
        """Set cost rates for the current model."""
        self.input_cost_per_m = input_per_m
        self.output_cost_per_m = output_per_m

    # ── Category-specific recorders ──

    def _add_tokens(self, category: str, tokens: int, note: str = "") -> None:
        """Internal: add tokens to a category and update totals."""
        if category not in self.tokens:
            # Fallback for unknown categories — treat as general input
            category = "input_messages"
        self.tokens[category] += tokens
        is_input = self.CATEGORIES.get(category, {}).get("is_input", True)
        if is_input:
            self.total_input_tokens += tokens
        else:
            self.total_output_tokens += tokens
        self.tokens_this_turn += tokens
        # Audit log
        self.audit_log.append({
            "category": category,
            "tokens": tokens,
            "note": note,
            "timestamp": __import__("datetime").datetime.now().isoformat(),
        })
        if len(self.audit_log) > self._max_audit_entries:
            self.audit_log = self.audit_log[-self._max_audit_entries:]
        # Recompute cost
        self.total_cost = (
            (self.total_input_tokens / 1_000_000) * self.input_cost_per_m
            + (self.total_output_tokens / 1_000_000) * self.output_cost_per_m
        )

    def record_system_prompt(self, tokens: int) -> None:
        """Record system prompt tokens (call once at init)."""
        self._add_tokens("system_prompt", tokens, "System prompt loaded")

    def record_input_message(self, tokens: int, note: str = "") -> None:
        """Record tokens from a user/context message sent to API."""
        self._add_tokens("input_messages", tokens, note or "User/context message")

    def record_output_message(self, tokens: int, note: str = "") -> None:
        """Record tokens from an assistant response."""
        self._add_tokens("output_messages", tokens, note or "Assistant response")

    def record_tool_request(self, tokens: int) -> None:
        """Record tokens from tool call definitions in API request."""
        self._add_tokens("tool_requests", tokens, "Tool call request")

    def record_tool_result_tokens(self, tokens: int, tool_name: str = "") -> None:
        """Record tokens from tool result fed back into context."""
        self._add_tokens("tool_results", tokens, f"Tool result: {tool_name}" if tool_name else "Tool result")

    def record_nudge(self, tokens: int, reason: str = "") -> None:
        """Record tokens from a system auto-nudge message."""
        self._add_tokens("nudge_messages", tokens, f"Nudge: {reason}" if reason else "Auto-nudge")

    def new_turn(self) -> None:
        """Mark the start of a new conversation turn."""
        self.turn_count += 1
        self.tokens_this_turn = 0

    # ── Legacy API (backward compatible) ──

    def record_usage(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage from an API response (legacy, splits to general categories)."""
        if input_tokens:
            self._add_tokens("input_messages", input_tokens, "Legacy input")
        if output_tokens:
            self._add_tokens("output_messages", output_tokens, "Legacy output")

    def record_tool_result(self, is_error: bool) -> None:
        """Record whether a tool execution succeeded or failed."""
        if is_error:
            self.consecutive_errors += 1
        else:
            self.consecutive_errors = 0

        self.recent_results.append(not is_error)
        if len(self.recent_results) > self.error_window:
            self.recent_results = self.recent_results[-self.error_window:]

    def check_circuit_breaker(self) -> tuple[bool, str]:
        """Check if the circuit breaker should trigger.

        Returns (should_stop, reason).
        """
        if self.circuit_breaker_triggered:
            return True, self.circuit_breaker_reason

        # Check budget
        if self.total_cost >= self.max_cost:
            reason = (
                f"💰 Budget exceeded: ${self.total_cost:.2f} >= ${self.max_cost:.2f} max. "
                f"Stopping to prevent overspending."
            )
            self.circuit_breaker_triggered = True
            self.circuit_breaker_reason = reason
            return True, reason

        # Check token limit
        total_tokens = self.total_input_tokens + self.total_output_tokens
        if total_tokens >= self.max_tokens:
            reason = (
                f"📊 Token limit reached: {total_tokens:,} >= {self.max_tokens:,}. "
                f"Stopping to prevent excessive usage."
            )
            self.circuit_breaker_triggered = True
            self.circuit_breaker_reason = reason
            return True, reason

        # Check consecutive errors
        if self.consecutive_errors >= self.max_consecutive_errors:
            reason = (
                f"🔴 Too many consecutive errors: {self.consecutive_errors}. "
                f"Stopping to prevent infinite error loops."
            )
            self.circuit_breaker_triggered = True
            self.circuit_breaker_reason = reason
            return True, reason

        # Check error rate
        if len(self.recent_results) >= self.error_window:
            error_rate = 1.0 - (sum(self.recent_results) / len(self.recent_results))
            if error_rate > self.max_error_rate:
                reason = (
                    f"⚠️ High error rate: {error_rate:.0%} in last {self.error_window} turns. "
                    f"Stopping — something is likely broken."
                )
                self.circuit_breaker_triggered = True
                self.circuit_breaker_reason = reason
                return True, reason

        return False, ""

    def get_status(self) -> str:
        """Get a human-readable budget status with category breakdown."""
        total_tokens = self.total_input_tokens + self.total_output_tokens
        budget_pct = (self.total_cost / self.max_cost) * 100 if self.max_cost > 0 else 0
        token_pct = (total_tokens / self.max_tokens) * 100 if self.max_tokens > 0 else 0

        lines = [
            f"💰 Budget: ${self.total_cost:.2f}/${self.max_cost:.2f} ({budget_pct:.0f}%)",
            f"📊 Tokens: {total_tokens:,}/{self.max_tokens:,} ({token_pct:.0f}%)  |  Turns: {self.turn_count}",
            f"🔴 Errors: {self.consecutive_errors} consecutive  |  CB: {'⚡TRIGGERED' if self.circuit_breaker_triggered else '✅OK'}",
            "",
            f"📋 Token Breakdown:",
        ]

        # Build category bars
        max_bar_width = 28
        for cat, info in self.CATEGORIES.items():
            cat_tokens = self.tokens.get(cat, 0)
            if total_tokens == 0:
                bar_len = 0
            else:
                bar_len = int((cat_tokens / total_tokens) * max_bar_width)
            bar = "█" * bar_len + "░" * (max_bar_width - bar_len)
            pct = (cat_tokens / total_tokens * 100) if total_tokens > 0 else 0
            lines.append(f"  {info['icon']} {info['label']:<18} {bar} {pct:5.1f}% ({cat_tokens:,})")

        # Show top 3 heaviest recent audit entries for insight
        if self.audit_log:
            recent = self.audit_log[-20:]
            # Group by category for recent summary
            recent_by_cat: dict[str, int] = {}
            for entry in recent:
                recent_by_cat[entry["category"]] = recent_by_cat.get(entry["category"], 0) + entry["tokens"]
            if recent_by_cat:
                top_cats = sorted(recent_by_cat.items(), key=lambda x: x[1], reverse=True)[:3]
                lines.append("")
                lines.append("🔥 Recent token burn (last 20 events):")
                for cat, tokens in top_cats:
                    info = self.CATEGORIES.get(cat, {})
                    lines.append(f"  {info.get('icon', '❓')} {info.get('label', cat)}: +{tokens:,} tokens")

        return "\n".join(lines)

    def get_token_breakdown(self) -> dict:
        """Get detailed token breakdown as a dict (for programmatic use)."""
        total = self.total_input_tokens + self.total_output_tokens
        return {
            "total": total,
            "total_input": self.total_input_tokens,
            "total_output": self.total_output_tokens,
            "cost": self.total_cost,
            "turns": self.turn_count,
            "categories": dict(self.tokens),
            "category_pct": {
                cat: (cnt / total * 100) if total > 0 else 0
                for cat, cnt in self.tokens.items()
            },
            "circuit_breaker": self.circuit_breaker_triggered,
        }

    def get_compact_status(self) -> str:
        """Get a compact one-line token summary for status displays."""
        total = self.total_input_tokens + self.total_output_tokens
        token_pct = (total / self.max_tokens) * 100 if self.max_tokens > 0 else 0
        cost_str = f"${self.total_cost:.2f}"
        return f"💰 {cost_str} | 📊 {total:,} tokens ({token_pct:.0f}%) | 🔄 {self.turn_count} turns"

    def reset_circuit_breaker(self) -> None:
        """Manually reset the circuit breaker (e.g. after user intervention)."""
        self.circuit_breaker_triggered = False
        self.circuit_breaker_reason = ""
        self.consecutive_errors = 0


# ═══════════════════════════════════════════════════════════════
#  Autonomous Controller — ties everything together
# ═══════════════════════════════════════════════════════════════

class AutonomousController:
    """Central controller for autonomous mode.

    Integrates:
    - Auto-advance task queue
    - Session state persistence
    - Self-evaluation loop
    - Budget & safety guardrails
    - Health monitoring & self-healing (Phase 5)
    - Auto-rollback on failure (Phase 5)

    Called by engine.py after each tool execution to decide next action.
    """

    def __init__(
        self,
        project_root: str = ".",
        max_budget: float = 10.0,
        max_retries: int = MAX_SELF_EVAL_RETRIES,
        enabled: bool = True,
        auto_rollback: bool = True,
    ):
        self.enabled = enabled
        self.session = SessionState()
        self.evaluator = SelfEvaluator(project_root=project_root, max_retries=max_retries)
        self.budget = BudgetTracker(max_cost=max_budget)

        # Phase 5: Health monitoring, self-healing, auto-rollback
        from judecode.agent.health import (
            HealthMonitor,
            SelfHealingEngine,
            ContextCompactor,
            ProgressReporter,
            AutoRollbackManager,
        )
        self.health = HealthMonitor()
        self.healing = SelfHealingEngine(self.health)
        self.compactor = ContextCompactor()
        self.reporter = ProgressReporter(session_id=self.session.session_id)
        self.auto_rollback_manager = AutoRollbackManager(enabled=auto_rollback)
        self._session_start_time: float = 0

    def on_tool_executed(
        self,
        tool_name: str,
        tool_params: dict[str, Any],
        tool_result: str,
        message_count: int = 0,
    ) -> Optional[str]:
        """Called after every tool execution. Returns nudge or None.

        This is the main integration point with engine.py.
        Returns a nudge message if the engine should auto-continue,
        or None if no action is needed.
        """
        if not self.enabled:
            return None

        # Track tool calls
        self.session.total_tool_calls += 1

        # ── Phase 5: Record tool call for health monitoring ──
        self.health.record_tool_call(tool_name, tool_params, tool_result)

        # Track errors for circuit breaker
        is_error = tool_result.lstrip().lower().startswith("error executing tool")
        self.budget.record_tool_result(is_error)

        if is_error:
            self.session.record_error(tool_result[:500])

        # ── Phase 5: Auto-Rollback on Failure ──
        if self.auto_rollback_manager.should_auto_rollback(
            tool_name=tool_name,
            tool_result=tool_result,
            retry_count=self.evaluator.retry_counts.get(
                tool_params.get("task_id", 0), 0
            ),
            max_retries=self.evaluator.max_retries,
        ):
            rollback_result = self.auto_rollback_manager.execute_rollback(
                reason=f"Task failed: {tool_name} returned error after max retries",
                task_id=tool_params.get("task_id"),
            )
            rollback_nudge = self.auto_rollback_manager.get_rollback_nudge(rollback_result)
            # After rollback, still allow auto-advance to next task
            return rollback_nudge

        # ── Check circuit breaker ──
        should_stop, reason = self.budget.check_circuit_breaker()
        if should_stop:
            self.session.mark_crashed(reason)
            return (
                f"[SYSTEM: 🛑 CIRCUIT BREAKER TRIGGERED\n{reason}\n\n"
                f"Session paused. Please review and fix the issue before continuing.]"
            )

        # ── 1.1 Auto-Advance ──
        nudge = check_auto_advance(tool_name, tool_result)
        if nudge:
            # Track task completion in session
            task_id = tool_params.get("task_id")
            if task_id:
                self.session.record_task_complete(task_id)

            # ── 1.3 Self-Evaluation ──
            if self.evaluator.should_verify(tool_name, tool_result):
                verify = self.evaluator.run_verification()
                if not verify["passed"]:
                    # Check if we can auto-retry
                    current_task = tool_params.get("task_id", 0)
                    if self.evaluator.should_auto_retry(current_task):
                        self.evaluator.record_retry(current_task)
                        return self.evaluator.get_retry_nudge(current_task, verify)
                    else:
                        return (
                            f"[SYSTEM: ⚠️ Verification failed after {self.evaluator.max_retries} retries. "
                            f"Please review manually.\n\n"
                            f"Results:\n{verify['summary']}]"
                        )

            return nudge

        # ── Track task starts ──
        if tool_name == "task_start":
            task_id = tool_params.get("task_id")
            if task_id:
                self.session.record_task_start(task_id)

        # ── Phase 5: Health Check & Self-Healing ──
        health_result = self.health.check_health(
            message_count=message_count,
            session_start_time=self._session_start_time,
        )

        # Record checkpoint if recommended
        if health_result.get("should_checkpoint"):
            self.health.record_checkpoint()

        # Self-healing if needed
        if self.healing.should_recover(health_result):
            recovery = self.healing.get_recovery_action(health_result)
            if recovery:
                return recovery["nudge"]

        # Health nudge (stuck/loop detection)
        if health_result.get("should_nudge") and health_result.get("nudge_message"):
            return health_result["nudge_message"]

        # ── Phase 5: Progress Report ──
        if self.reporter.should_report():
            report = self.reporter.generate_report(
                session_state={
                    "turns": self.session.total_turns,
                    "tool_calls": self.session.total_tool_calls,
                    "completed_tasks": len(self.session.completed_tasks),
                    "errors": len(self.session.errors),
                },
                health_status=health_result["status"],
                budget_status=self.budget.get_status(),
            )
            # Log the report but don't nudge with it
            logger.info(f"Progress report generated: {report[:200]}")

        # ── Periodic save (every 10 tool calls) ──
        if self.session.total_tool_calls % 10 == 0:
            self.session.save()

        return None

    def on_turn_complete(self) -> None:
        """Called after each turn completes."""
        self.session.total_turns += 1

    def on_session_start(self, goal: str) -> None:
        """Called when a new session starts."""
        self.session.original_goal = goal
        self.session.status = "active"
        self._session_start_time = time.time()
        self.session.save()

    def on_session_end(self) -> None:
        """Called when the session ends normally."""
        self.session.mark_completed()
        # Generate final progress report
        self.reporter.generate_report(
            session_state={
                "turns": self.session.total_turns,
                "tool_calls": self.session.total_tool_calls,
                "completed_tasks": len(self.session.completed_tasks),
                "errors": len(self.session.errors),
                "status": "completed",
            },
            health_status=self.health.last_health_status,
            budget_status=self.budget.get_status(),
        )

    def on_task_completed(self, task_id: int) -> None:
        """Called when a task is successfully completed."""
        self.health.record_task_completion()
        self.healing.reset_recovery_attempts()  # Reset since we're making progress

    def should_compact_context(self, messages: list[dict]) -> bool:
        """Check if context should be compacted for long sessions."""
        return self.compactor.should_compact(messages)

    def compact_context(self, messages: list[dict]) -> list[dict]:
        """Compact message history for long-running sessions."""
        return self.compactor.compact(messages)

    def get_status(self) -> str:
        """Get overall autonomous mode status."""
        health_summary = self.health.get_summary()
        rollback_summary = self.auto_rollback_manager.get_summary()
        reporter_summary = self.reporter.get_summary()
        healing_summary = self.healing.get_summary()
        return (
            f"{'='*50}\n"
            f"🤖 Autonomous Mode Status\n"
            f"{'='*50}\n"
            f"{self.session.get_progress_summary()}\n"
            f"{'─'*50}\n"
            f"{self.budget.get_status()}\n"
            f"{'─'*50}\n"
            f"{health_summary}\n"
            f"{'─'*50}\n"
            f"{healing_summary}\n"
            f"{'─'*50}\n"
            f"{rollback_summary}\n"
            f"{'─'*50}\n"
            f"{reporter_summary}\n"
            f"{'='*50}"
        )
