"""
Task Continuation / Nudge System for Jude Code.

Detects when the agent stops mid-task (stream interruption, tool error, etc.)
and automatically triggers a continuation to nudge the agent to keep going.

Key features:
- Detects stream interruptions and partial content
- Detects tool execution errors
- Detects tool results that indicate incomplete work
- Auto-continues with a nudge message (up to max_continuations limit)
- Tracks continuation history to prevent infinite loops
- Verifies task completion before deciding to continue
"""

import json
import re
from datetime import datetime
from typing import Any, Optional


# ── Indicators that work might be incomplete ──

PARTIAL_CONTENT_INDICATORS = [
    "partial content",
    "stream interrupted",
    "connection",
    "timeout",
    "retry",
]

TOOL_ERROR_INDICATORS = [
    "error executing tool",
    "typeerror",
    "keyerror",
    "valueerror",
    "timeout",
    "connection refused",
    "connection reset",
]

# ── Indicators that content was truncated (token limit exceeded) ──

TOKEN_LIMIT_INDICATORS = [
    "token limit",
    "max tokens",
    "maximum tokens",
    "context length",
    "maximum context",
    "context window",
    "too many tokens",
    "token budget",
    "finish_reason.*length",
    "truncated",
    "content truncated",
    "output truncated",
]

INCOMPLETE_WORK_PATTERNS = [
    # File operations that didn't complete
    r"(?:still|need|must|should|going to).*(?:write|create|edit|update|delete|add)",
    r"(?:writing|creating|editing|updating).*(?:next|remaining|rest|other)",
    r"(?:not yet|haven't|hasn't).*(?:finished|completed|done|written)",
    r"let me (?:also|now|then|next)",
    r"i (?:will|need to|should|must).*(?:next|now|then|also)",
    # Partial file writes
    r"content truncated",
    r"output truncated",
    r"too long",
    r"max.*length",
    r"exceeded",
    r"token limit",
    r"max tokens",
    r"maximum tokens",
    r"context length",
    r"truncated",
    # Shell errors
    r"command not found",
    r"permission denied",
    r"no such file",
    r"not found",
    r"failed",
    r"error:",
    r"warning:",
    r"exit code: 1",
    r"exit code: 2",
    # Continuation signals
    r"continuing|continuing from|resuming|picking up",
    r"next (?:step|file|part|task|thing)",
    r"remaining (?:work|tasks|files|steps)",
    r"more (?:work|files|tasks|steps|to do)",
    r"incomplete",
    r"partially (?:done|complete|finished)",
]

COMPLETION_INDICATORS = [
    r"(?:all|everything).*(?:done|finished|completed|ready|set)",
    r"(?:task|work).*(?:complete|finished|done)",
    r"successfully (?:created|written|updated|deleted|completed)",
    r"finished!",
    r"done!",
    r"completed!",
    r"that's (?:it|all|everything)",
    r"no (?:more|further|additional).*(?:steps|work|tasks)",
    r"i'm (?:done|finished)",
    r"let me know if",
    r"anything else",
    r"is there anything",
    r"task (?:is|was) (?:complete|successful)",
    r"all (?:files|tasks|steps).*(?:created|updated|written|done)",
    r"everything is in order",
    r"ready for (?:review|testing|next)",
    r"all set",
    r"completed successfully",
    r"finished successfully",
]


def detect_stream_interruption(last_content: str) -> bool:
    """Check if the last content from a stream suggests an interruption."""
    if not last_content:
        return False
    lower = last_content.lower()
    return any(indicator in lower for indicator in PARTIAL_CONTENT_INDICATORS)


def detect_token_limit_truncation(last_content: str, finish_reason: str = "") -> bool:
    """Check if the response was truncated due to token limit."""
    if finish_reason == "length":
        return True
    if not last_content:
        return False
    lower = last_content.lower()
    return any(indicator in lower for indicator in TOKEN_LIMIT_INDICATORS)


def detect_tool_error(tool_result: str) -> bool:
    """Check if a tool result indicates an error."""
    if not tool_result:
        return False
    lower = tool_result.lower()
    # Check for known error patterns
    for indicator in TOOL_ERROR_INDICATORS:
        if indicator in lower:
            return True
    return False


def detect_incomplete_work(
    assistant_message: str,
    tool_results: list[str],
) -> bool:
    """Check if the assistant's message + tool results suggest incomplete work."""
    # Check if assistant message indicates more work to do
    if assistant_message:
        for pattern in INCOMPLETE_WORK_PATTERNS:
            if re.search(pattern, assistant_message, re.IGNORECASE):
                return True

    # Check tool results for errors
    for result in tool_results:
        if detect_tool_error(result):
            return True

    return False


def detect_completion(assistant_message: str) -> bool:
    """Check if the assistant message indicates the task is complete."""
    if not assistant_message:
        return False
    for pattern in COMPLETION_INDICATORS:
        if re.search(pattern, assistant_message, re.IGNORECASE):
            return True
    return False


def should_continue(
    last_assistant_message: str,
    tool_results: list[str],
    continuation_count: int,
    max_continuations: int,
    has_tool_calls: bool,
    had_stream_error: bool,
    finish_reason: str = "",
    partial_arguments: str = "",
) -> tuple[bool, str]:
    """
    Decide if the agent should auto-continue.

    Args:
        finish_reason: The finish_reason from the API response ("stop", "length", etc.)
        partial_arguments: Any partial tool call arguments that were truncated

    Returns:
        (should_continue: bool, nudge_message: str)
    """
    # Safety: never exceed max continuations
    if continuation_count >= max_continuations:
        return (False, "")

    # If there was a stream error, ALWAYS continue (unless max reached)
    if had_stream_error:
        return (
            True,
            "[SYSTEM: The previous response was interrupted. "
            "Please continue from where you left off. "
            f"Summarize what you've done so far and complete the remaining work. "
            f"(Continuation {continuation_count + 1}/{max_continuations})]",
        )

    # If finish_reason is "length", content was truncated by token limit
    if finish_reason == "length":
        if partial_arguments:
            return (
                True,
                f"[SYSTEM: The previous response was truncated because it exceeded the token limit. "
                f"Here are the partial tool arguments that were captured:\n\n"
                f"{partial_arguments[:1000]}\n\n"
                f"Please CONTINUE writing from where you left off — do NOT restart from the beginning. "
                f"Use the edit tool to append to the file that was being written, or continue the remaining content. "
                f"(Continuation {continuation_count + 1}/{max_continuations})]",
            )
        else:
            return (
                True,
                f"[SYSTEM: The previous response was truncated because it exceeded the token limit. "
                f"Please continue from where you left off — do NOT restart. "
                f"If you were writing a file, use edit or append to add the remaining content. "
                f"(Continuation {continuation_count + 1}/{max_continuations})]",
            )

    # If no tool calls were made and no stream error, the agent probably finished
    if not has_tool_calls and not had_stream_error:
        return (False, "")

    # Check if the assistant says the work is complete
    if detect_completion(last_assistant_message):
        return (False, "")

    # Check if work seems incomplete
    if detect_incomplete_work(last_assistant_message, tool_results):
        return (
            True,
            f"[SYSTEM: It looks like the task may not be fully complete yet. "
            f"Please check what's been done and continue working if needed. "
            f"If everything is actually finished, just confirm that. "
            f"(Continuation {continuation_count + 1}/{max_continuations})]",
        )

    # If there were tool calls but no clear completion signal, do a gentle check
    if has_tool_calls:
        # Only continue if we detect something incomplete
        if detect_incomplete_work(last_assistant_message, tool_results):
            return (
                True,
                f"[SYSTEM: Please verify if the task is complete. "
                f"If not, continue working. If done, just confirm. "
                f"(Continuation {continuation_count + 1}/{max_continuations})]",
            )

    return (False, "")


def generate_continuation_nudge(
    reason: str,
    continuation_count: int,
    max_continuations: int,
    partial_content: str = "",
    partial_arguments: str = "",
) -> str:
    """Generate a context-aware nudge message for the agent."""
    remaining = max_continuations - continuation_count

    if reason == "stream_interrupted":
        return (
            f"[SYSTEM: Connection was interrupted mid-response. "
            f"Here's what was received so far:\n\n{partial_content[:500]}\n\n"
            f"Please continue from where you left off. "
            f"You have {remaining} continuation(s) remaining.]"
        )

    elif reason == "token_limit":
        args_preview = partial_arguments[:800] if partial_arguments else "(not available)"
        return (
            f"[SYSTEM: The response was truncated because it exceeded the token limit. "
            f"Here are the partial tool arguments captured:\n\n{args_preview}\n\n"
            f"IMPORTANT: Continue writing from where you left off — do NOT restart from scratch. "
            f"If you were writing a file, use the edit tool to append remaining content. "
            f"You have {remaining} continuation(s) remaining.]"
        )

    elif reason == "tool_error":
        return (
            f"[SYSTEM: A tool execution error was detected. "
            f"Please check the error and retry or find an alternative approach. "
            f"You have {remaining} continuation(s) remaining.]"
        )

    elif reason == "incomplete_work":
        return (
            f"[SYSTEM: It appears the task may not be fully complete. "
            f"Please review progress and continue if needed. "
            f"If everything is done, just confirm. "
            f"You have {remaining} continuation(s) remaining.]"
        )

    else:
        return (
            f"[SYSTEM: Please continue working on the current task. "
            f"You have {remaining} continuation(s) remaining.]"
        )


class ContinuationTracker:
    """
    Tracks continuation state across the conversation.
    Resets when the user sends a new message.
    """

    def __init__(
        self,
        max_continuations: int = 10,
        continue_on_stream_error: bool = True,
        continue_on_incomplete_work: bool = True,
        continue_on_tool_error: bool = True,
    ):
        self.max_continuations = max_continuations
        self.continue_on_stream_error = continue_on_stream_error
        self.continue_on_incomplete_work = continue_on_incomplete_work
        self.continue_on_tool_error = continue_on_tool_error
        self.count = 0
        self.history: list[dict[str, Any]] = []
        self.last_user_message: str = ""
        self.had_stream_error = False
        self.partial_content_buffer = ""
        self.last_finish_reason: str = ""
        self.partial_arguments_buffer: str = ""

    def reset(self, user_message: str = ""):
        """Reset when user sends a new message."""
        self.count = 0
        self.history = []
        self.last_user_message = user_message
        self.had_stream_error = False
        self.partial_content_buffer = ""
        self.last_finish_reason = ""
        self.partial_arguments_buffer = ""

    def record_continuation(self, reason: str, nudge: str):
        """Record a continuation event."""
        self.count += 1
        self.history.append({
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "nudge": nudge,
            "count": self.count,
        })

    def can_continue(self) -> bool:
        """Check if we can continue."""
        return self.count < self.max_continuations

    def get_summary(self) -> str:
        """Get a summary of continuation history for the agent."""
        if not self.history:
            return ""
        parts = []
        for h in self.history:
            parts.append(f"  Continuation #{h['count']}: {h['reason']}")
        return "\n".join(parts)
