"""
Health Check & Self-Healing System for JudeCode — Phase 5: Long-Running Autonomous

Ensures the agent can run 8+ hours unattended by:
  5.1 Health Monitor — periodic self-checks for stuck/hung states
  5.2 Stuck Detection — detect repeated actions, no progress, or loops
  5.3 Self-Healing — auto-recovery from common issues
  5.4 Context Compaction — manage memory/context for long sessions
  5.5 Progress Reporter — periodic status updates and summaries

This module is called by engine.py on each turn to assess agent health
and take corrective action when needed.
"""

import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from judecode.utils.logger import get_logger

logger = get_logger("judecode.health")


# ═══════════════════════════════════════════════════════════════
#  5.1 Health Monitor
# ═══════════════════════════════════════════════════════════════

class HealthMonitor:
    """Periodic health checks for the autonomous agent.

    Checks:
    - Is the agent making progress? (tasks completed over time)
    - Is the agent stuck in a loop? (repeated tool calls/results)
    - Is context growing too large?
    - Is memory usage reasonable?
    - Has the session been running too long without progress?

    Called after each tool execution to assess health.
    """

    # How often (in turns) to run a full health check
    CHECK_INTERVAL_TURNS = 5

    # Maximum time (seconds) without any task completion before considering stuck
    STUCK_THRESHOLD_SECONDS = 600  # 10 minutes

    # Maximum number of consecutive identical tool calls before loop detection
    LOOP_DETECTION_THRESHOLD = 3

    # Maximum context messages before recommending compaction
    MAX_CONTEXT_MESSAGES = 80

    # Maximum session duration before recommending a checkpoint (seconds)
    CHECKPOINT_INTERVAL_SECONDS = 1800  # 30 minutes

    def __init__(
        self,
        check_interval: int = 5,
        stuck_threshold: int = 600,
        loop_threshold: int = 3,
        max_context_messages: int = 80,
        checkpoint_interval: int = 1800,
    ):
        self.check_interval = check_interval
        self.stuck_threshold = stuck_threshold
        self.loop_threshold = loop_threshold
        self.max_context_messages = max_context_messages
        self.checkpoint_interval = checkpoint_interval

        # Tracking state
        self.last_task_completion_time: float = time.time()
        self.last_checkpoint_time: float = time.time()
        self.turn_count: int = 0
        self.recent_tool_calls: list[dict[str, Any]] = []
        self.health_history: list[dict[str, Any]] = []
        self.last_health_status: str = "healthy"
        self.recovery_actions_taken: list[str] = []

    def record_tool_call(self, tool_name: str, args: dict, result: str) -> None:
        """Record a tool call for stuck/loop detection."""
        # Create a fingerprint of the tool call (name + key args)
        fingerprint = self._make_fingerprint(tool_name, args)
        self.recent_tool_calls.append({
            "tool": tool_name,
            "fingerprint": fingerprint,
            "result_hash": hashlib.md5(result[:500].encode()).hexdigest()[:8],
            "timestamp": time.time(),
        })

        # Keep only last 20 tool calls for analysis
        if len(self.recent_tool_calls) > 20:
            self.recent_tool_calls = self.recent_tool_calls[-20:]

    def record_task_completion(self) -> None:
        """Record that a task was completed."""
        self.last_task_completion_time = time.time()

    def check_health(self, message_count: int = 0, session_start_time: float = 0) -> dict[str, Any]:
        """Run a health check and return status + recommendations.

        Args:
            message_count: Current number of messages in conversation
            session_start_time: Timestamp when session started

        Returns:
            dict with keys: status, issues, recommendations, should_compact,
                           should_checkpoint, should_nudge, nudge_message
        """
        self.turn_count += 1

        # Only run full check every N turns
        if self.turn_count % self.check_interval != 0:
            return {
                "status": self.last_health_status,
                "issues": [],
                "recommendations": [],
                "should_compact": False,
                "should_checkpoint": False,
                "should_nudge": False,
                "nudge_message": "",
            }

        issues = []
        recommendations = []
        should_compact = False
        should_checkpoint = False
        should_nudge = False
        nudge_message = ""

        # ── Check 1: Stuck detection (no task completion for too long) ──
        time_since_completion = time.time() - self.last_task_completion_time
        if time_since_completion > self.stuck_threshold:
            issues.append("stuck_no_progress")
            recommendations.append(
                f"No task completed in {int(time_since_completion / 60)} minutes. "
                "Consider re-evaluating approach."
            )
            should_nudge = True
            nudge_message = (
                f"[SYSTEM: ⚠️ Health Check — No task has been completed in "
                f"{int(time_since_completion / 60)} minutes. "
                f"You may be stuck. Consider: (1) breaking the current task into smaller steps, "
                f"(2) trying a different approach, or (3) marking the current task as cancelled "
                f"and moving to the next one.]"
            )

        # ── Check 2: Loop detection (repeated identical tool calls) ──
        loop_detected = self._detect_loop()
        if loop_detected:
            issues.append("loop_detected")
            recommendations.append(
                f"Loop detected: same action repeated {loop_detected['count']} times. "
                "Try a different approach."
            )
            should_nudge = True
            nudge_message = (
                f"[SYSTEM: ⚠️ Health Check — Loop detected! You've called "
                f"'{loop_detected['tool']}' with similar parameters {loop_detected['count']} times "
                f"with the same result. This approach isn't working. "
                f"Try: (1) a completely different strategy, (2) read documentation, "
                f"(3) search for solutions online, or (4) skip this task.]"
            )

        # ── Check 3: Context size check ──
        if message_count > self.max_context_messages:
            issues.append("context_large")
            recommendations.append(
                f"Context has {message_count} messages. Consider compacting."
            )
            should_compact = True

        # ── Check 4: Checkpoint interval ──
        time_since_checkpoint = time.time() - self.last_checkpoint_time
        if time_since_checkpoint > self.checkpoint_interval:
            should_checkpoint = True
            recommendations.append("Periodic checkpoint recommended.")

        # ── Check 5: Session duration warning ──
        if session_start_time > 0:
            session_duration = time.time() - session_start_time
            if session_duration > 4 * 3600:  # 4+ hours
                issues.append("long_session")
                recommendations.append(
                    f"Session running for {int(session_duration / 3600)} hours. "
                    "Ensure state is being saved periodically."
                )

        # ── Determine overall status ──
        if any(i in issues for i in ["stuck_no_progress", "loop_detected"]):
            status = "unhealthy"
        elif issues:
            status = "warning"
        else:
            status = "healthy"

        self.last_health_status = status

        # Record health check
        health_record = {
            "timestamp": datetime.now().isoformat(),
            "turn": self.turn_count,
            "status": status,
            "issues": issues,
            "message_count": message_count,
            "time_since_completion": int(time_since_completion),
        }
        self.health_history.append(health_record)
        # Keep only last 100 health records
        if len(self.health_history) > 100:
            self.health_history = self.health_history[-100:]

        return {
            "status": status,
            "issues": issues,
            "recommendations": recommendations,
            "should_compact": should_compact,
            "should_checkpoint": should_checkpoint,
            "should_nudge": should_nudge,
            "nudge_message": nudge_message,
        }

    def _detect_loop(self) -> Optional[dict[str, Any]]:
        """Detect if the agent is in a loop (repeated identical actions).

        Returns loop info dict if detected, None otherwise.
        """
        if len(self.recent_tool_calls) < self.loop_threshold:
            return None

        # Check the last N tool calls for repetition
        recent = self.recent_tool_calls[-self.loop_threshold * 2:]

        # Count consecutive identical fingerprints
        fingerprint_counts: dict[str, int] = {}
        for tc in recent:
            fp = tc["fingerprint"]
            result_hash = tc["result_hash"]
            key = f"{fp}:{result_hash}"
            fingerprint_counts[key] = fingerprint_counts.get(key, 0) + 1

        # If any fingerprint appears more than threshold times, it's a loop
        for key, count in fingerprint_counts.items():
            if count >= self.loop_threshold:
                tool_name = key.split(":")[0]
                return {
                    "tool": tool_name,
                    "count": count,
                    "fingerprint": key,
                }

        return None

    def _make_fingerprint(self, tool_name: str, args: dict) -> str:
        """Create a fingerprint for a tool call to detect repetition."""
        # Normalize args - only keep key identifying information
        key_args = {}
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 100:
                key_args[k] = v[:100] + "..."
            else:
                key_args[k] = v
        return f"{tool_name}:{json.dumps(key_args, sort_keys=True)}"

    def record_checkpoint(self) -> None:
        """Record that a checkpoint was just made."""
        self.last_checkpoint_time = time.time()

    def record_recovery_action(self, action: str) -> None:
        """Record a self-healing action taken."""
        self.recovery_actions_taken.append({
            "action": action,
            "timestamp": datetime.now().isoformat(),
        })

    def get_summary(self) -> str:
        """Get a health summary for display."""
        lines = [
            f"🏥 Health Status: {self.last_health_status.upper()}",
            f"   Turns: {self.turn_count}",
            f"   Recent tool calls: {len(self.recent_tool_calls)}",
            f"   Recovery actions: {len(self.recovery_actions_taken)}",
            f"   Last task completed: {int(time.time() - self.last_task_completion_time)}s ago",
        ]
        if self.health_history:
            last = self.health_history[-1]
            if last["issues"]:
                lines.append(f"   Issues: {', '.join(last['issues'])}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  5.2 Self-Healing Engine
# ═══════════════════════════════════════════════════════════════

class SelfHealingEngine:
    """Automatically recover from common issues during long-running sessions.

    Recovery strategies:
    - Context overflow → compact conversation history
    - Stuck in loop → suggest alternative approach via nudge
    - No progress → recommend skipping task
    - Memory pressure → clear caches and non-essential state
    - Repeated errors → switch strategy or escalate
    """

    def __init__(self, health_monitor: HealthMonitor):
        self.health = health_monitor
        self.recovery_attempts: dict[str, int] = {}  # issue_type -> attempt_count
        self.max_recovery_attempts = 3

    def should_recover(self, health_result: dict[str, Any]) -> bool:
        """Determine if self-healing should be triggered."""
        if health_result["status"] == "healthy":
            return False

        for issue in health_result["issues"]:
            attempts = self.recovery_attempts.get(issue, 0)
            if attempts < self.max_recovery_attempts:
                return True

        return False

    def get_recovery_action(self, health_result: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Get the recommended recovery action for current health issues.

        Returns a dict with:
            - action: the recovery action type
            - nudge: a nudge message to send to the agent
            - priority: how urgent this recovery is
        """
        issues = health_result.get("issues", [])
        if not issues:
            return None

        # Priority order: loop > stuck > context > long_session
        if "loop_detected" in issues:
            self.recovery_attempts["loop_detected"] = self.recovery_attempts.get("loop_detected", 0) + 1
            attempt = self.recovery_attempts["loop_detected"]
            self.health.record_recovery_action("loop_break")

            if attempt == 1:
                return {
                    "action": "loop_break_suggest_alternative",
                    "nudge": (
                        "[SYSTEM: 🔄 Self-Healing — Loop detected. "
                        "You're repeating the same action without success. "
                        "Please try a COMPLETELY DIFFERENT approach. "
                        "Consider: using different tools, reading documentation, "
                        "searching online for solutions, or simplifying the problem.]"
                    ),
                    "priority": "high",
                }
            elif attempt == 2:
                return {
                    "action": "loop_break_skip_task",
                    "nudge": (
                        "[SYSTEM: 🔄 Self-Healing — Loop persists after 2 attempts. "
                        "Consider cancelling this task and moving to the next one. "
                        "Use task_cancel() to skip this task, then task_next() to continue.]"
                    ),
                    "priority": "high",
                }
            else:
                return {
                    "action": "loop_break_force_skip",
                    "nudge": (
                        "[SYSTEM: 🔄 Self-Healing — Loop continues after 3 attempts. "
                        "You MUST cancel this task now. Use task_cancel() immediately, "
                        "then move to the next task with task_next(). "
                        "This task will be flagged for manual review.]"
                    ),
                    "priority": "critical",
                }

        if "stuck_no_progress" in issues:
            self.recovery_attempts["stuck_no_progress"] = self.recovery_attempts.get("stuck_no_progress", 0) + 1
            attempt = self.recovery_attempts["stuck_no_progress"]
            self.health.record_recovery_action("stuck_recovery")

            if attempt == 1:
                return {
                    "action": "stuck_reassess",
                    "nudge": (
                        "[SYSTEM: ⏳ Self-Healing — No progress for a while. "
                        "Reassess the current situation: "
                        "(1) What's blocking you? (2) Can you break it into smaller steps? "
                        "(3) Should you try a different approach? "
                        "Use think() to plan your next steps.]"
                    ),
                    "priority": "medium",
                }
            else:
                return {
                    "action": "stuck_skip",
                    "nudge": (
                        "[SYSTEM: ⏳ Self-Healing — Still stuck. "
                        "Consider cancelling this task and moving on. "
                        "Use task_cancel() then task_next(). "
                        "You can come back to this task later.]"
                    ),
                    "priority": "high",
                }

        if "context_large" in issues:
            self.recovery_attempts["context_large"] = self.recovery_attempts.get("context_large", 0) + 1
            self.health.record_recovery_action("context_compaction")
            return {
                "action": "compact_context",
                "nudge": (
                    "[SYSTEM: 🧠 Self-Healing — Context is getting large. "
                    "To keep the session running smoothly: "
                    "(1) Summarize what you've done so far, "
                    "(2) Focus on the current task only, "
                    "(3) Don't repeat information from earlier turns.]"
                ),
                "priority": "low",
            }

        if "long_session" in issues:
            self.health.record_recovery_action("long_session_check")
            return {
                "action": "long_session_verify",
                "nudge": (
                    "[SYSTEM: ⏰ Self-Healing — Session has been running for several hours. "
                    "Quick check: (1) Are you still making progress? "
                    "(2) Is the remaining work well-defined? "
                    "(3) Should you save a checkpoint now? "
                    "Use /status to review overall progress.]"
                ),
                "priority": "low",
            }

        return None

    def reset_recovery_attempts(self, issue_type: Optional[str] = None) -> None:
        """Reset recovery attempt counters.

        Call this when a recovery action succeeds (e.g., task completed after nudge).
        """
        if issue_type:
            self.recovery_attempts.pop(issue_type, None)
        else:
            self.recovery_attempts.clear()

    def get_summary(self) -> str:
        """Get recovery status summary."""
        lines = ["🔧 Self-Healing Status:"]
        if self.recovery_attempts:
            for issue, count in self.recovery_attempts.items():
                lines.append(f"   {issue}: {count} attempts")
        else:
            lines.append("   No recovery actions needed")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  5.3 Context Compaction
# ═══════════════════════════════════════════════════════════════

class ContextCompactor:
    """Manage conversation context for long-running sessions.

    Strategies:
    - Summarize old messages (keep recent ones intact)
    - Remove verbose tool results from history
    - Keep task-related messages and remove idle chatter
    - Preserve system prompts and recent context

    This is NOT automatic — it provides recommendations and
    compaction helpers that the engine can call when needed.
    """

    # Maximum messages before compaction is recommended
    COMPACTION_THRESHOLD = 80

    # How many recent messages to keep intact
    RECENT_WINDOW = 20

    # Maximum tool result length in compacted history
    MAX_COMPACTED_RESULT_LENGTH = 500

    def __init__(
        self,
        compaction_threshold: int = 80,
        recent_window: int = 20,
        max_result_length: int = 500,
    ):
        self.compaction_threshold = compaction_threshold
        self.recent_window = recent_window
        self.max_result_length = max_result_length

    def should_compact(self, messages: list[dict]) -> bool:
        """Check if context should be compacted."""
        return len(messages) > self.compaction_threshold

    def compact(self, messages: list[dict]) -> list[dict]:
        """Compact message history to reduce context size.

        Strategy:
        1. Keep first message (system prompt) intact
        2. Keep last N messages intact (recent context)
        3. For middle messages:
           - Truncate long tool results
           - Remove duplicate/redundant messages
           - Summarize groups of tool calls
        """
        if len(messages) <= self.compaction_threshold:
            return messages

        # Split messages into sections
        first_msg = messages[0] if messages else None
        middle_messages = messages[1:-self.recent_window] if len(messages) > self.recent_window + 1 else []
        recent_messages = messages[-self.recent_window:] if len(messages) > self.recent_window else messages[1:]

        # Compact middle section
        compacted_middle = self._compact_middle(middle_messages)

        # Reassemble
        result = []
        if first_msg:
            result.append(first_msg)

        # Add a compaction notice
        if compacted_middle:
            result.append({
                "role": "system",
                "content": (
                    f"[CONTEXT COMPACTED: Earlier conversation history has been summarized. "
                    f"{len(middle_messages)} messages were compacted to {len(compacted_middle)}. "
                    f"Focus on the current task and recent context below.]"
                ),
            })

        result.extend(compacted_middle)
        result.extend(recent_messages)

        return result

    def _compact_middle(self, messages: list[dict]) -> list[dict]:
        """Compact the middle section of messages."""
        compacted = []
        skip_next = False

        for i, msg in enumerate(messages):
            if skip_next:
                skip_next = False
                continue

            role = msg.get("role", "")

            # Truncate long tool results
            if role == "tool":
                content = msg.get("content", "")
                if len(content) > self.max_result_length:
                    msg = {
                        **msg,
                        "content": (
                            content[:self.max_result_length // 2]
                            + f"\n\n... [compacted, {len(content):,} chars total] ...\n\n"
                            + content[-self.max_result_length // 4:]
                        ),
                    }
                compacted.append(msg)
                continue

            # Keep user messages (they contain nudges and instructions)
            if role == "user":
                compacted.append(msg)
                continue

            # Keep assistant messages but truncate reasoning
            if role == "assistant":
                # Remove old reasoning content to save space
                if "reasoning_content" in msg:
                    msg = {k: v for k, v in msg.items() if k != "reasoning_content"}
                compacted.append(msg)
                continue

            # Keep system messages
            if role == "system":
                compacted.append(msg)
                continue

        return compacted

    def get_compaction_summary(self, messages: list[dict]) -> str:
        """Get a summary of what compaction would do."""
        total = len(messages)
        if total <= self.compaction_threshold:
            return f"Context OK: {total} messages (threshold: {self.compaction_threshold})"

        middle = max(0, total - self.recent_window - 1)
        return (
            f"Context large: {total} messages (threshold: {self.compaction_threshold})\n"
            f"  Would compact ~{middle} older messages, keep {self.recent_window} recent"
        )


# ═══════════════════════════════════════════════════════════════
#  5.4 Progress Reporter
# ═══════════════════════════════════════════════════════════════

class ProgressReporter:
    """Generate periodic progress reports for long-running sessions.

    Saves reports to ~/.judecode/sessions/<session_id>/reports/
    and optionally sends notifications at milestones.
    """

    # Report intervals (in minutes)
    DEFAULT_REPORT_INTERVAL = 30  # Report every 30 minutes
    MILESTONE_INTERVALS = [60, 120, 240, 480]  # 1h, 2h, 4h, 8h milestones

    def __init__(
        self,
        session_id: str,
        report_interval: int = 30,
    ):
        self.session_id = session_id
        self.report_interval = report_interval
        self.reports_dir = (
            Path.home() / ".judecode" / "sessions" / session_id / "reports"
        )
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.last_report_time: float = time.time()
        self.session_start_time: float = time.time()
        self.report_count: int = 0

    def should_report(self) -> bool:
        """Check if it's time for a progress report."""
        elapsed = time.time() - self.last_report_time
        return elapsed >= self.report_interval * 60

    def is_milestone(self) -> bool:
        """Check if current session duration hits a milestone."""
        elapsed_minutes = (time.time() - self.session_start_time) / 60
        for milestone in self.MILESTONE_INTERVALS:
            # Within 1 minute of milestone
            if abs(elapsed_minutes - milestone) < 1:
                return True
        return False

    def generate_report(
        self,
        session_state: Optional[dict] = None,
        health_status: str = "healthy",
        budget_status: str = "",
        task_summary: str = "",
    ) -> str:
        """Generate and save a progress report.

        Args:
            session_state: Current session state dict
            health_status: Current health status
            budget_status: Budget status string
            task_summary: Summary of task progress

        Returns:
            The report text
        """
        self.report_count += 1
        self.last_report_time = time.time()

        elapsed = time.time() - self.session_start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        report_lines = [
            f"📊 JudeCode Progress Report #{self.report_count}",
            f"   Generated: {now}",
            f"   Session: {self.session_id}",
            f"   Duration: {hours}h {minutes}m",
            f"   Health: {health_status}",
            "",
        ]

        if task_summary:
            report_lines.append(f"📋 Tasks:\n{task_summary}")
            report_lines.append("")

        if budget_status:
            report_lines.append(f"💰 Budget:\n{budget_status}")
            report_lines.append("")

        if session_state:
            report_lines.append(f"📊 Session State:")
            for k, v in session_state.items():
                report_lines.append(f"   {k}: {v}")

        report_text = "\n".join(report_lines)

        # Save report
        report_file = self.reports_dir / f"report_{self.report_count:04d}.md"
        report_file.write_text(report_text, encoding="utf-8")

        # Also save as latest
        latest_file = self.reports_dir / "latest.md"
        latest_file.write_text(report_text, encoding="utf-8")

        logger.info(f"Progress report #{self.report_count} saved")
        return report_text

    def get_latest_report(self) -> Optional[str]:
        """Get the latest progress report."""
        latest = self.reports_dir / "latest.md"
        if latest.exists():
            return latest.read_text(encoding="utf-8")
        return None

    def get_summary(self) -> str:
        """Get a brief progress summary."""
        elapsed = time.time() - self.session_start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        return (
            f"📊 Session Duration: {hours}h {minutes}m | "
            f"Reports: {self.report_count}"
        )


# ═══════════════════════════════════════════════════════════════
#  5.5 Auto-Rollback on Failure
# ═══════════════════════════════════════════════════════════════

class AutoRollbackManager:
    """Automatically rollback to last checkpoint when a task fails.

    Flow:
    1. Task fails after max retries
    2. Auto-rollback to last checkpoint
    3. Log the rollback reason
    4. Notify user
    5. Continue with next task or stop

    Configuration:
    - JUDECODE_AUTO_ROLLBACK=true|false (default: true)
    - JUDECODE_AUTO_ROLLBACK_ON_RETRY_FAIL=true|false (default: true)
    """

    def __init__(
        self,
        enabled: bool = True,
        rollback_on_retry_fail: bool = True,
        notify_on_rollback: bool = True,
    ):
        self.enabled = enabled
        self.rollback_on_retry_fail = rollback_on_retry_fail
        self.notify_on_rollback = notify_on_rollback
        self.rollback_history: list[dict[str, Any]] = []
        self.checkpoint_ref: Optional[Any] = None  # Will be set to CheckpointManager

    def set_checkpoint_manager(self, checkpoint_manager: Any) -> None:
        """Set reference to the CheckpointManager."""
        self.checkpoint_ref = checkpoint_manager

    def should_auto_rollback(self, tool_name: str, tool_result: str, retry_count: int, max_retries: int) -> bool:
        """Determine if auto-rollback should be triggered.

        Triggers:
        - Task failed after max retries (retry_count >= max_retries)
        - Verification failed after all retries
        - Critical error detected
        """
        if not self.enabled:
            return False

        # Check if this is a failure result
        is_failure = (
            "❌" in tool_result
            or "error" in tool_result.lower()
            or "failed" in tool_result.lower()
        )

        if not is_failure:
            return False

        # Auto-rollback when max retries exhausted
        if self.rollback_on_retry_fail and retry_count >= max_retries:
            return True

        # Check for critical errors that warrant immediate rollback
        critical_errors = [
            "file not found",
            "permission denied",
            "disk full",
            "out of memory",
            "corrupted",
            "data loss",
        ]
        result_lower = tool_result.lower()
        for err in critical_errors:
            if err in result_lower:
                return True

        return False

    def execute_rollback(self, reason: str, task_id: Optional[int] = None) -> dict[str, Any]:
        """Execute an auto-rollback to the last checkpoint.

        Args:
            reason: Why the rollback is happening
            task_id: The task that triggered the rollback

        Returns:
            dict with rollback results
        """
        if not self.checkpoint_ref:
            return {
                "success": False,
                "error": "No checkpoint manager available",
                "files_restored": 0,
            }

        # Find the last checkpoint
        checkpoints = self.checkpoint_ref.list_checkpoints()
        if not checkpoints:
            return {
                "success": False,
                "error": "No checkpoints available for rollback",
                "files_restored": 0,
            }

        # Rollback to the most recent checkpoint
        last_checkpoint = checkpoints[-1]
        step = last_checkpoint.get("step", 0)

        try:
            result = self.checkpoint_ref.rollback(step=step)

            # Record the rollback
            rollback_record = {
                "timestamp": datetime.now().isoformat(),
                "reason": reason,
                "task_id": task_id,
                "checkpoint_step": step,
                "result": "success",
            }
            self.rollback_history.append(rollback_record)

            # Save rollback log
            self._save_rollback_log(rollback_record)

            logger.warning(
                f"Auto-rollback executed: step={step}, reason={reason}, task={task_id}"
            )

            return {
                "success": True,
                "checkpoint_step": step,
                "files_restored": result if isinstance(result, str) else "unknown",
                "reason": reason,
            }

        except Exception as e:
            rollback_record = {
                "timestamp": datetime.now().isoformat(),
                "reason": reason,
                "task_id": task_id,
                "checkpoint_step": step,
                "result": "failed",
                "error": str(e),
            }
            self.rollback_history.append(rollback_record)

            logger.error(f"Auto-rollback failed: {e}")

            return {
                "success": False,
                "error": str(e),
                "checkpoint_step": step,
            }

    def get_rollback_nudge(self, rollback_result: dict[str, Any]) -> str:
        """Generate a nudge message after auto-rollback."""
        if rollback_result["success"]:
            return (
                f"[SYSTEM: 🔄 Auto-Rollback Executed — "
                f"Files have been restored to checkpoint #{rollback_result.get('checkpoint_step', '?')} "
                f"because: {rollback_result.get('reason', 'task failure')}. "
                f"The previous approach didn't work. Please try a different strategy. "
                f"If this task keeps failing, consider using task_cancel() and moving on.]"
            )
        else:
            return (
                f"[SYSTEM: ⚠️ Auto-Rollback Failed — "
                f"Could not restore from checkpoint: {rollback_result.get('error', 'unknown')}. "
                f"Manual intervention may be needed. Use /rollback to try manually.]"
            )

    def _save_rollback_log(self, record: dict[str, Any]) -> None:
        """Save rollback record to log file."""
        log_dir = Path.home() / ".judecode" / "rollback_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = log_dir / f"{datetime.now().strftime('%Y%m%d')}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_summary(self) -> str:
        """Get auto-rollback status summary."""
        lines = [
            f"🔄 Auto-Rollback: {'✅ Enabled' if self.enabled else '❌ Disabled'}",
            f"   Rollbacks executed: {len(self.rollback_history)}",
        ]
        if self.rollback_history:
            last = self.rollback_history[-1]
            lines.append(
                f"   Last rollback: {last.get('timestamp', 'N/A')[:19]} "
                f"(reason: {last.get('reason', 'N/A')})"
            )
        return "\n".join(lines)
