"""Jude Code Agent Engine - handles the conversation loop and tool execution."""

import asyncio
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from judecode.api.client import ApiClient
from judecode.agent.tools import TOOL_DEFINITIONS, execute_tool
from judecode.agent.continuation import (
    ContinuationTracker,
    detect_incomplete_work,
    detect_completion,
    generate_continuation_nudge,
)
from judecode.config import (
    MAX_CONTINUATIONS,
    MAX_TURNS,
    CONTINUE_ON_STREAM_ERROR,
    CONTINUE_ON_INCOMPLETE_WORK,
    CONTINUE_ON_TOOL_ERROR,
    AUTONOMOUS_MODE,
    AUTONOMOUS_MAX_BUDGET,
    AUTO_ROLLBACK_ENABLED,
    HEALTH_MONITOR_ENABLED,
)
from judecode.agent.autonomous import AutonomousController
from judecode.agent.checkpoint import CheckpointManager
from judecode.agent.safety import PermissionManager, BackupManager, SandboxManager
from judecode.agent.memory import DecisionLog, CrossSessionMemory
from judecode.agent.recall import MemoryRecall, update_project_memory_file
from judecode.agent.daemon import NotificationManager
from judecode.ui.console import console
from judecode.utils.logger import get_logger, log_error_details
from rich.text import Text

logger = get_logger("judecode.engine")


class AgentEngine:
    """Main agent logic: stream completions, handle tool calls, iterate."""

    def __init__(
        self,
        system_prompt: str,
        api_client: ApiClient,
        max_continuations: int = MAX_CONTINUATIONS,
        continue_on_stream_error: bool = CONTINUE_ON_STREAM_ERROR,
        continue_on_incomplete_work: bool = CONTINUE_ON_INCOMPLETE_WORK,
        continue_on_tool_error: bool = CONTINUE_ON_TOOL_ERROR,
    ):
        self.system_prompt = system_prompt
        self.api = api_client
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        self.continuation = ContinuationTracker(
            max_continuations=max_continuations,
            continue_on_stream_error=continue_on_stream_error,
            continue_on_incomplete_work=continue_on_incomplete_work,
            continue_on_tool_error=continue_on_tool_error,
        )
        # ── Interrupt / Pause support ──
        self.cancel_requested = False
        # ── Turn counter (shared between chat() and continue_task()) ──
        self._turn_count = 0
        # ── Autonomous Controller (Phase 1+5: Auto-advance, State, Eval, Budget, Health, Rollback) ──
        self.autonomous = AutonomousController(
            max_budget=AUTONOMOUS_MAX_BUDGET,
            enabled=AUTONOMOUS_MODE,
            auto_rollback=AUTO_ROLLBACK_ENABLED,
        )
        # Record system prompt tokens upfront (one-time cost per session)
        sys_prompt_tokens = max(1, len(system_prompt) // 3)
        self.autonomous.budget.record_system_prompt(sys_prompt_tokens)
        # ── Checkpoint Manager (Phase 2) ──
        self.checkpoint = CheckpointManager(
            session_id=self.autonomous.session.session_id
        )
        # ── Link checkpoint manager to auto-rollback ──
        self.autonomous.auto_rollback_manager.set_checkpoint_manager(self.checkpoint)
        # ── Safety Systems (Phase 3) ──
        self.permissions = PermissionManager()
        self.permissions.load_from_env()
        self.backups = BackupManager()
        self.sandbox = SandboxManager()
        # ── Memory Systems (Phase 2) ──
        self.decisions = DecisionLog(
            session_id=self.autonomous.session.session_id
        )
        self.memory = CrossSessionMemory()
        # ── Notifications (Phase 4) ──
        self.notifications = NotificationManager()

    # ── Context Management (Token Optimization) ──

    # Maximum chars for a tool result stored in message history.
    # Old tool results are truncated to save context tokens on every API call.
    # The model has already processed the full result when it first arrived.
    _MAX_TOOL_RESULT_IN_HISTORY = 4000

    # Number of recent tool results to keep in full (not truncated)
    _KEEP_FULL_RECENT_RESULTS = 3

    # Maximum nudge messages to keep in history
    _MAX_NUDGE_MESSAGES = 2

    def _prune_context(self) -> None:
        """Prune conversation history to reduce token usage without losing key context.

        Optimizations applied:
        1. Truncate old tool results (keep recent ones in full)
        2. Remove old nudge messages (keep last 2)
        3. Remove reasoning_content from old assistant messages
        4. Replace old verbose tool results with short summaries

        This is called before each API call to keep context lean.
        """
        messages = self.messages
        if len(messages) <= 4:  # system + user + at least 1 exchange
            return

        # ── 1. Count recent tool results (from the end) to keep in full ──
        recent_tool_ids = set()
        tool_count = 0
        for msg in reversed(messages):
            if msg.get("role") == "tool":
                tool_count += 1
                if tool_count <= self._KEEP_FULL_RECENT_RESULTS:
                    recent_tool_ids.add(msg.get("tool_call_id", ""))
                else:
                    break

        # ── 2. Track nudge messages and keep only the last N ──
        nudge_indices = []
        for i, msg in enumerate(messages):
            if msg.get("role") == "user" and self._is_nudge_message(msg.get("content", "")):
                nudge_indices.append(i)

        # Remove old nudges (keep last _MAX_NUDGE_MESSAGES)
        indices_to_remove = set()
        if len(nudge_indices) > self._MAX_NUDGE_MESSAGES:
            for idx in nudge_indices[: -self._MAX_NUDGE_MESSAGES]:
                indices_to_remove.add(idx)

        # ── 3. Process messages ──
        new_messages = []
        for i, msg in enumerate(messages):
            # Skip old nudge messages
            if i in indices_to_remove:
                continue

            # Truncate old tool results
            if msg.get("role") == "tool":
                tool_id = msg.get("tool_call_id", "")
                content = msg.get("content", "")
                if (
                    tool_id not in recent_tool_ids
                    and len(content) > self._MAX_TOOL_RESULT_IN_HISTORY
                ):
                    head = content[: self._MAX_TOOL_RESULT_IN_HISTORY // 3]
                    tail_len = self._MAX_TOOL_RESULT_IN_HISTORY // 2
                    tail = content[-tail_len:]
                    truncated = (
                        f"{head}\n\n"
                        f"... [{len(content):,} chars total, "
                        f"truncated to save context] ...\n\n"
                        f"{tail}"
                    )
                    msg = {**msg, "content": truncated}

            # Remove reasoning from old assistant messages (keep last 2)
            if msg.get("role") == "assistant" and "reasoning_content" in msg:
                # Check if this is one of the last 2 assistant messages
                assistant_count_after = sum(
                    1 for m in messages[i:] if m.get("role") == "assistant"
                )
                if assistant_count_after > 2:
                    msg = {k: v for k, v in msg.items() if k != "reasoning_content"}

            new_messages.append(msg)

        self.messages = new_messages

    def _show_thinking(self, turn: int) -> None:
        """Show a thinking indicator before each model response turn."""
        if turn == 1:
            console.print(f"\n  [dim]⏳ Thinking...[/dim]")
        else:
            console.print(f"\n  [dim]⏳ Processing results... (turn {turn})[/dim]")

    def _show_tool_call(self, tool_name: str, args: dict) -> None:
        """Display tool call information."""
        console.print()
        if args:
            args_preview = json.dumps(args, ensure_ascii=False, indent=2)
            if len(args_preview) > 200:
                args_preview = args_preview[:197] + "..."
            console.print(
                f"  [bold yellow]⚡[/bold yellow] Using tool [bold white]{tool_name}[/bold white]"
            )
            console.print(
                f"     [dim]Parameters:[/dim] [bright_white]{args_preview}[/bright_white]"
            )
        else:
            console.print(
                f"  [bold yellow]⚡[/bold yellow] Using tool [bold white]{tool_name}[/bold white]"
            )

    def _show_tool_result(self, result: str) -> None:
        """Display a snippet of the tool result."""
        result_preview = result[:300] + "..." if len(result) > 300 else result
        console.print(
            f"     [dim green]✓ Done[/dim green] [dim]{result_preview}[/dim]"
        )

    def _show_continuation_nudge(self, reason: str, count: int, max_c: int):
        """Display a continuation nudge in the UI."""
        color = "yellow" if count <= 3 else "red"
        reason_labels = {
            "stream_interrupted": "Stream Interrupted",
            "tool_error": "Tool Error Detected",
            "incomplete_work": "Possible Incomplete Work",
            "token_limit": "Token Limit Reached",
        }
        label = reason_labels.get(reason, "Checking Progress")
        console.print(
            f"\n  [bold {color}]⟳ Continuation #{count}/{max_c}[/bold {color}] "
            f"[dim]({label})[/dim]"
        )

    def _execute_tool_safe(self, tool_name: str, args: dict) -> str:
        """Execute a tool safely, catching TypeError and other exceptions."""
        try:
            return execute_tool(tool_name, args)
        except TypeError as e:
            log_error_details(
                logger,
                f"Tool execution TypeError in '{tool_name}'",
                exc_info=True,
                extra={"args": args},
            )
            return (
                f"Error executing tool '{tool_name}': "
                f"TypeError ({type(e).__name__}: {e}). "
                "This usually means a required parameter was missing."
            )

    def _is_nudge_message(self, content: str) -> bool:
        """Check if a message is a system nudge (starts with [SYSTEM:)."""
        return content.strip().startswith("[SYSTEM:")

    def _append_nudge(self, content: str, reason: str = "") -> None:
        """Append a nudge message and track its token cost."""
        self.messages.append({"role": "user", "content": content})
        nudge_tokens = max(1, len(content) // 3)
        self.autonomous.budget.record_nudge(nudge_tokens, reason)

    def _track_token_usage(self, content: str, reasoning: str = "") -> None:
        """Estimate and track token usage with category breakdown for budget monitoring.

        Uses rough estimation: ~4 chars per token for English,
        ~2 chars per token for CJK/Thai content.
        This is approximate but sufficient for budget guardrails.
        """
        # Combine content + reasoning for output tokens
        total_text = content + reasoning
        if total_text:
            estimated_output_tokens = max(1, len(total_text) // 3)
            self.autonomous.budget.record_output_message(
                estimated_output_tokens, f"Turn {self._turn_count}"
            )

        # Estimate input tokens from message history length.
        # NOTE: the full history is re-sent to the API every turn, so counting
        # the whole history each time is technically correct for API cost.
        # BUT we only record the *delta* vs. the previous turn as "new" input
        # for budget guardrails, otherwise a 50-turn session would count the
        # system prompt 50x and trip the budget limit far too early.
        total_input_chars = sum(
            len(str(m.get("content", ""))) for m in self.messages
        )
        estimated_input_tokens = max(1, total_input_chars // 3)
        prev = getattr(self, "_last_input_token_estimate", 0)
        new_input_tokens = max(1, estimated_input_tokens - prev)
        self._last_input_token_estimate = estimated_input_tokens

        # Legacy call for backward compat (also updates totals)
        self.autonomous.budget.record_usage(
            input_tokens=new_input_tokens,
            output_tokens=estimated_output_tokens if total_text else 0,
        )

    def _save_session_memory(self) -> None:
        """Save session summary to cross-session memory on session end."""
        try:
            s = self.autonomous.session
            self.memory.save_session_summary(
                session_id=s.session_id,
                goal=s.original_goal,
                completed_tasks=s.completed_tasks,
                total_tasks=len(s.completed_tasks) + (1 if s.current_task_id else 0),
                errors_encountered=[e.get("error", "")[:100] for e in s.errors[-5:]],
            )
            # Save project context if we're in a project directory
            import os
            cwd = os.getcwd()
            self.memory.save_project_context(
                project_path=cwd,
                conventions={"last_session": s.session_id},
            )
            # ── Update per-project JUDE.md session log ──
            # Only in real project dirs (has VCS/manifest) or if JUDE.md exists,
            # and only for non-trivial sessions (avoid logging "hi" chats).
            goal = (s.original_goal or "").strip()
            is_project = any(
                os.path.exists(os.path.join(cwd, marker))
                for marker in (
                    ".git", "pyproject.toml", "package.json", "Cargo.toml",
                    "go.mod", "JUDE.md",
                )
            )
            if is_project and (len(goal) > 20 or s.completed_tasks):
                summary = goal[:150]
                if s.completed_tasks:
                    summary += f" — completed tasks: {s.completed_tasks}"
                update_project_memory_file(summary, cwd=cwd)
        except Exception as e:
            logger.debug(f"Failed to save session memory: {e}")

    def _pre_tool_hook(self, tool_name: str, tool_params: dict) -> Optional[str]:
        """Pre-execution hook: backup files, check permissions, create checkpoint.

        Returns an error string if execution should be blocked, or None.
        """
        # ── Permission check ──
        allowed, level, reason = self.permissions.check_permission(tool_name, tool_params)
        if not allowed:
            return reason

        # ── Auto-backup before file modifications ──
        file_modifying_tools = {"write", "edit", "delete"}
        if tool_name in file_modifying_tools:
            path = tool_params.get("path", "")
            if path and os.path.exists(path):
                self.backups.backup_file(path, reason=tool_name)

        # ── Checkpoint before write/edit ──
        if tool_name in ("write", "edit") and tool_params.get("path"):
            self.checkpoint.create_checkpoint(
                file_paths=[tool_params["path"]],
                reason=tool_name,
                task_id=self.autonomous.session.current_task_id,
            )

        return None  # Allow execution

    def _post_tool_hook(self, tool_name: str, tool_params: dict, result: str) -> None:
        """Post-execution hook: record decisions, notify on important events."""
        # ── Record significant decisions ──
        if tool_name == "task_complete":
            task_id = tool_params.get("task_id")
            self.decisions.record(
                task=f"Complete task #{task_id}",
                strategy="task_complete",
                result="pass" if "✅" in result else "fail",
                task_id=task_id,
            )
            # ── Notify on task completion ──
            self.notifications.notify_task_complete(
                task_name=f"Task #{task_id}",
                success="✅" in result,
            )

        elif tool_name == "task_start":
            task_id = tool_params.get("task_id")
            self.decisions.record(
                task=f"Start task #{task_id}",
                strategy="task_start",
                result="pending",
                task_id=task_id,
            )

        # ── Record errors in decision log ──
        if result.lstrip().lower().startswith("error executing tool"):
            self.decisions.record(
                task=f"Tool: {tool_name}",
                strategy=str(tool_params)[:100],
                result="fail",
                learnings=f"Error: {result[:200]}",
            )

    @staticmethod
    def _is_context_overflow_error(error_text: str) -> bool:
        """Detect if an error is caused by the context/prompt being too large.

        When the conversation grows too long, the API rejects the request with
        a 'context length exceeded' / 'too large' style error. Auto-continuing
        in that case is USELESS — the context is still too big, so it just loops
        forever. We must detect this and STOP instead of continuing.
        """
        if not error_text:
            return False
        lower = error_text.lower()
        overflow_markers = (
            "context length",
            "context_length_exceeded",
            "maximum context",
            "context window",
            "too long",
            "too large",
            "prompt is too long",
            "reduce the length",
            "request too large",
            "exceeds the maximum",
            "exceed the maximum",
            "input length",
            "max_tokens",
            "string too long",
            "payload too large",
            "413",
        )
        return any(marker in lower for marker in overflow_markers)

    def request_stop(self) -> None:
        """Request the agent to stop/pause after the current action."""
        self.cancel_requested = True
        console.print(
            "\n  [bold red]⏹ Stop requested... will pause after current action[/bold red]\n"
        )

    def reset_stop(self) -> None:
        """Clear the stop/pause flag (e.g. after user redirects)."""
        self.cancel_requested = False

    async def continue_task(self) -> None:
        """
        Manually trigger a continuation nudge.
        Called when user types /continue.
        """
        if not self.continuation.can_continue():
            console.print(
                "\n  [bold red]Max continuations reached. Start a new task or clear the conversation.[/bold red]\n"
            )
            return

        nudge = (
            f"[SYSTEM: Manual continuation requested by user. "
            f"Please continue working on the current task from where you left off. "
            f"(Continuation {self.continuation.count + 1}/{self.continuation.max_continuations})]"
        )
        self.continuation.record_continuation("manual", nudge)
        self._append_nudge(nudge, "manual")
        console.print(
            f"\n  [bold yellow]⟳ Manual continuation #{self.continuation.count}/{self.continuation.max_continuations}[/bold yellow]"
        )
        # Process the nudge directly, passing the current turn context
        self._turn_count += 1
        await self._process_turn(turn_number=self._turn_count)

    async def _process_turn(self, turn_number: int = 1) -> bool:
        """
        Process a single turn of the conversation loop.
        Args:
            turn_number: The current turn number (for display purposes)
        Returns True if more turns should follow, False if done.
        """
        full_content = ""
        full_reasoning = ""
        tool_calls: list[dict[str, Any]] = []
        has_started_output = False
        has_shown_reasoning = False
        reasoning_completed = False
        tool_results: list[str] = []
        finish_reason = ""

        # Show thinking indicator
        self._show_thinking(turn_number)

        # ── Prune context before API call to save tokens ──
        # Truncates old tool results, removes old nudges, prunes old reasoning
        self._prune_context()

        # Track whether the stream was aborted mid-flight by Ctrl+C
        stream_aborted = False

        # Stream the response
        try:
            async for chunk in self.api.chat_completion(
                self.messages, tools=TOOL_DEFINITIONS
            ):
                # ── Abort streaming IMMEDIATELY if user pressed Ctrl+C ──
                # This is the key fix: previously cancel was only checked AFTER
                # the whole stream finished, so a long response would keep
                # flowing for a long time before stopping. Now we break out of
                # the token stream as soon as the stop flag is set.
                if self.cancel_requested:
                    stream_aborted = True
                    break

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                # ── Track finish_reason from the last chunk ──
                chunk_finish = choices[0].get("finish_reason")
                if chunk_finish:
                    finish_reason = chunk_finish

                delta = choices[0].get("delta", {})

                # ── Extract reasoning/thinking content ──
                reasoning_piece = self.api._extract_reasoning(chunk)
                if reasoning_piece:
                    full_reasoning += reasoning_piece
                    if not reasoning_completed and not has_started_output:
                        if not has_shown_reasoning:
                            console.print()
                            console.print(
                                "  [dim]───────────────────── Reasoning ─────────────────────[/dim]"
                            )
                            has_shown_reasoning = True
                        # Use a Text object instead of markup string so that
                        # newlines inside the reasoning text cannot break Rich
                        # markup tags (which caused literal "[dim]" / "/dim" to
                        # leak into the output when the TUI sink split on \n).
                        console.print(
                            Text(reasoning_piece, style="dim italic"),
                            end="",
                        )

                # ── Extract normal content ──
                content_piece = delta.get("content")
                if content_piece:
                    full_content += content_piece
                    if not has_started_output:
                        if has_shown_reasoning:
                            reasoning_completed = True
                            console.print()
                            console.print(
                                "  [dim]───────────────── End Reasoning ───────────────────[/dim]"
                            )
                            console.print()
                        console.print()
                        has_started_output = True
                    # Use a Text object so the content is rendered literally
                    # (brackets, etc. are never mis-parsed as Rich markup) and
                    # newlines cannot break surrounding markup tags.
                    console.print(Text(content_piece), end="")

                # Handle tool calls
                tool_call_pieces = delta.get("tool_calls", [])
                for tc in tool_call_pieces:
                    index = tc.get("index", 0)
                    if index >= len(tool_calls):
                        tool_calls.extend(
                            [{} for _ in range(index - len(tool_calls) + 1)]
                        )
                    if "id" in tc:
                        tool_calls[index]["id"] = tc["id"]
                    if "function" in tc:
                        fn = tc["function"]
                        if "name" in fn:
                            tool_calls[index]["name"] = fn["name"]
                        if "arguments" in fn:
                            if "arguments" not in tool_calls[index]:
                                tool_calls[index]["arguments"] = ""
                            tool_calls[index]["arguments"] += fn["arguments"]

        except Exception as e:
            error_msg = f"Stream error: {type(e).__name__}: {e}"
            console.print(f"\n  [bold red]{error_msg}[/bold red]\n")
            log_error_details(
                logger,
                error_msg,
                exc_info=True,
                extra={
                    "turn": turn_number,
                    "full_content_length": len(full_content),
                    "has_partial": bool(full_content),
                },
            )

            # ── Context overflow → STOP, do NOT auto-continue ──
            # Continuing would just re-send the same oversized context and loop
            # forever. Tell the user to clear the conversation instead.
            if self._is_context_overflow_error(error_msg):
                # Save whatever partial assistant content we got so /clear works cleanly
                if full_content:
                    self.messages.append({
                        "role": "assistant",
                        "content": full_content,
                    })
                console.print(
                    "\n  [bold red]⛔ Context too large — the conversation has grown "
                    "beyond the model's limit.[/bold red]\n"
                    "  [yellow]Auto-continuation stopped to prevent an infinite loop.[/yellow]\n"
                    "  [dim]Type [bold]/clear[/bold] to start a fresh conversation, "
                    "or [bold]/compact[/bold] if available.[/dim]\n"
                )
                return False  # Hard stop — no continuation

            self.continuation.had_stream_error = True
            self.continuation.partial_content_buffer = full_content

            if (
                self.continuation.continue_on_stream_error
                and self.continuation.can_continue()
            ):
                nudge = generate_continuation_nudge(
                    reason="stream_interrupted",
                    continuation_count=self.continuation.count,
                    max_continuations=self.continuation.max_continuations,
                    partial_content=full_content,
                )
                self.continuation.record_continuation("stream_interrupted", nudge)
                self._show_continuation_nudge(
                    "stream_interrupted",
                    self.continuation.count,
                    self.continuation.max_continuations,
                )
                self._append_nudge(nudge, "stream_interrupted")
                return True  # Continue to next turn
            return False

        if has_started_output:
            console.print()
        elif has_shown_reasoning and not reasoning_completed:
            reasoning_completed = True
            console.print()
            console.print(
                "  [dim]───────────────── End Reasoning ───────────────────[/dim]"
            )
            console.print()

        # ── Ctrl+C aborted the stream mid-flight → pause immediately ──
        # We saved whatever partial text/tool-calls arrived; store the partial
        # assistant content and STOP. Do NOT execute partial tool calls and do
        # NOT auto-continue.
        if stream_aborted:
            self.cancel_requested = False
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": full_content,
            }
            if full_reasoning:
                msg["reasoning_content"] = full_reasoning
            self.messages.append(msg)
            console.print(
                "\n  [bold yellow]⏸ Stopped mid-response by user. "
                "Type a new message to redirect, or /continue to resume.[/bold yellow]\n"
            )
            return False  # Hard stop

        # ── Save finish_reason for continuation logic ──
        self.continuation.last_finish_reason = finish_reason

        # ── Determine if there were tool calls ──
        has_tool_calls = any("name" in tc for tc in tool_calls)

        # ── Capture partial tool call arguments if truncated ──
        partial_arguments = ""
        if finish_reason == "length" and has_tool_calls:
            # Save the raw (potentially incomplete) arguments for the nudge
            for tc in tool_calls:
                if "arguments" in tc and tc.get("name"):
                    partial_arguments += f"Tool: {tc['name']}\nArguments:\n{tc['arguments']}\n\n"
            self.continuation.partial_arguments_buffer = partial_arguments

        # ── Check for cancel/stop BEFORE auto-continuation ──
        if self.cancel_requested:
            self.cancel_requested = False
            console.print(
                "\n  [bold yellow]⏸ Paused by user. Type a new message to redirect, or /continue to resume.[/bold yellow]\n"
            )
            # Store the assistant message so far, then stop
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": full_content,  # Keep "" instead of None — API requires content or tool_calls
            }
            if full_reasoning:
                msg["reasoning_content"] = full_reasoning
            self.messages.append(msg)
            return False  # Stop the loop

        # If no tool calls, store assistant message and check for continuation
        if not has_tool_calls:
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": full_content,  # Keep "" instead of None — API requires content or tool_calls
            }
            if full_reasoning:
                msg["reasoning_content"] = full_reasoning
            self.messages.append(msg)

            # ── Track estimated tokens for budget ──
            self._track_token_usage(full_content, full_reasoning)
            self.autonomous.on_turn_complete()

            # ── Check for token limit truncation (only valid no-tool-call continuation) ──
            if finish_reason == "length":
                if self.continuation.can_continue():
                    nudge = generate_continuation_nudge(
                        reason="token_limit",
                        continuation_count=self.continuation.count,
                        max_continuations=self.continuation.max_continuations,
                        partial_content=full_content,
                    )
                    self.continuation.record_continuation("token_limit", nudge)
                    self._show_continuation_nudge(
                        "token_limit",
                        self.continuation.count,
                        self.continuation.max_continuations,
                    )
                    self._append_nudge(nudge, "token_limit")
                    return True  # Continue
                return False

            # ── No tool calls + not truncated = normal conversation → NEVER auto-continue ──
            return False  # Done

        # ── There were tool calls - execute them ──
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc.get("arguments", ""),
                    },
                }
                for tc in tool_calls if "name" in tc
            ],
        }
        if full_reasoning:
            msg["reasoning_content"] = full_reasoning
        self.messages.append(msg)

        if not has_started_output:
            console.print()

        # Parse all tool calls
        parsed_calls = []
        for tc in tool_calls:
            if "name" not in tc:
                continue
            args_str = tc.get("arguments", "{}")
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            parsed_calls.append({
                "id": tc.get("id", ""),
                "name": tc["name"],
                "args": args,
            })

        # ── If finish_reason is "length" AND we have partial tool calls ──
        # This means the tool arguments were truncated. We still execute what we can,
        # but the nudge will tell the model to continue from where it left off.
        if finish_reason == "length" and parsed_calls:
            # Execute what we have (even if partial)
            for tc in parsed_calls:
                self._show_tool_call(tc["name"], tc["args"])
                try:
                    result = execute_tool(tc["name"], tc["args"])
                except Exception as e:
                    log_error_details(
                        logger,
                        f"Tool execution error in '{tc['name']}'",
                        exc_info=True,
                        extra={"args": tc["args"]},
                    )
                    result = (
                        f"Error executing tool '{tc['name']}': "
                        f"{type(e).__name__}: {e}"
                    )
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                tool_results.append(result)
                self._show_tool_result(result)
                # ── Track tool result tokens ──
                self.autonomous.budget.record_tool_result_tokens(
                    max(1, len(str(result)) // 3),
                    tool_name=tc["name"],
                )

            # Send continuation nudge with partial arguments
            if self.continuation.can_continue():
                nudge = generate_continuation_nudge(
                    reason="token_limit",
                    continuation_count=self.continuation.count,
                    max_continuations=self.continuation.max_continuations,
                    partial_content=full_content,
                    partial_arguments=partial_arguments,
                )
                self.continuation.record_continuation("token_limit", nudge)
                self._show_continuation_nudge(
                    "token_limit",
                    self.continuation.count,
                    self.continuation.max_continuations,
                )
                self._append_nudge(nudge, "token_limit")
                return True  # Continue

        if len(parsed_calls) == 1:
            tc = parsed_calls[0]
            self._show_tool_call(tc["name"], tc["args"])

            # ── Pre-tool hook: backup, permission check ──
            blocked = self._pre_tool_hook(tc["name"], tc["args"])
            if blocked:
                result = blocked
            else:
                try:
                    result = execute_tool(tc["name"], tc["args"])
                except TypeError as e:
                    log_error_details(
                        logger,
                        f"Tool execution TypeError in '{tc['name']}'",
                        exc_info=True,
                        extra={"args": tc["args"]},
                    )
                    result = (
                        f"Error executing tool '{tc['name']}': "
                        f"TypeError ({type(e).__name__}: {e}). "
                        "This usually means a required parameter was missing."
                    )
                # ── Post-tool hook: decision log, notifications ──
                self._post_tool_hook(tc["name"], tc["args"], result)
            self.messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
            tool_results.append(result)
            self._show_tool_result(result)
        else:
            console.print(
                f"\n  [bold cyan]⚡⚡⚡ Parallel execution: {len(parsed_calls)} tools[/bold cyan]"
            )
            for tc in parsed_calls:
                self._show_tool_call(tc["name"], tc["args"])

            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=len(parsed_calls)) as executor:
                futures = []
                for tc in parsed_calls:
                    fut = loop.run_in_executor(
                        executor,
                        self._execute_tool_safe,
                        tc["name"],
                        tc["args"],
                    )
                    futures.append(fut)

                results = await asyncio.gather(*futures, return_exceptions=True)

            for i, (tc, result) in enumerate(zip(parsed_calls, results)):
                if isinstance(result, Exception):
                    result = (
                        f"Error executing tool '{tc['name']}': "
                        f"{type(result).__name__}: {result}"
                    )
                # ── Pre-tool hook for parallel (backup only, can't block) ──
                self._pre_tool_hook(tc["name"], tc["args"])
                # ── Post-tool hook ──
                self._post_tool_hook(tc["name"], tc["args"], result)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                tool_results.append(result)
                self._show_tool_result(result)
                # ── Track tool result tokens ──
                self.autonomous.budget.record_tool_result_tokens(
                    max(1, len(str(result)) // 3),
                    tool_name=tc["name"],
                )

        # ── Check for cancel/stop BEFORE auto-continuation ──
        if self.cancel_requested:
            self.cancel_requested = False
            console.print(
                "\n  [bold yellow]⏸ Paused by user after tool execution. Type a new message to redirect, or /continue to resume.[/bold yellow]\n"
            )
            return False  # Stop the loop

        # ── Autonomous Controller Hook (Phase 1 + Phase 5) ──
        # After tool execution, check for auto-advance, self-eval, budget,
        # health monitoring, self-healing, and auto-rollback
        if self.autonomous.enabled and parsed_calls:
            for i, tc in enumerate(parsed_calls):
                result_text = tool_results[i] if i < len(tool_results) else ""
                auto_nudge = self.autonomous.on_tool_executed(
                    tool_name=tc["name"],
                    tool_params=tc["args"],
                    tool_result=result_text,
                    message_count=len(self.messages),
                )
                if auto_nudge:
                    self.messages.append({"role": "user", "content": auto_nudge})
                    console.print(
                        f"\n  [bold magenta]🤖 Auto-nudge: {auto_nudge[:80]}...[/bold magenta]\n"
                    )
                    return True  # Continue with nudge

        # ── Auto-continuation check after tool execution ──
        if (
            not self._is_nudge_message(full_content)
            and self.continuation.can_continue()
        ):
            # ── Detect GENUINE tool errors only ──
            # A real tool failure is returned by the dispatcher as a string that
            # *starts* with the error prefix. We must NOT use substring matching
            # here, otherwise normal tool output that merely contains the phrase
            # (e.g. `read`/`grep` returning source code with "Error executing
            # tool" in it) would trigger a false-positive continuation nudge.
            has_tool_error = any(
                r.lstrip().lower().startswith("error executing tool")
                for r in tool_results
            )
            work_incomplete = detect_incomplete_work(full_content, tool_results)

            if has_tool_error and self.continuation.continue_on_tool_error:
                nudge = generate_continuation_nudge(
                    reason="tool_error",
                    continuation_count=self.continuation.count,
                    max_continuations=self.continuation.max_continuations,
                )
                self.continuation.record_continuation("tool_error", nudge)
                self._show_continuation_nudge(
                    "tool_error",
                    self.continuation.count,
                    self.continuation.max_continuations,
                )
                self._append_nudge(nudge, "tool_error")
                return True  # Continue

            elif work_incomplete and self.continuation.continue_on_incomplete_work:
                nudge = generate_continuation_nudge(
                    reason="incomplete_work",
                    continuation_count=self.continuation.count,
                    max_continuations=self.continuation.max_continuations,
                )
                self.continuation.record_continuation("incomplete_work", nudge)
                self._show_continuation_nudge(
                    "incomplete_work",
                    self.continuation.count,
                    self.continuation.max_continuations,
                )
                self._append_nudge(nudge, "incomplete_work")
                return True  # Continue

        # ── Check if work is clearly done before auto-continuing ──
        if not self._is_nudge_message(full_content) and detect_completion(full_content):
            return False  # Work is done, stop

        return True  # Continue to next turn naturally (no nudge needed)

    async def chat(self, user_message: str) -> None:
        """Send a user message and handle streaming + tool calls."""
        # Reset for new user message
        self.continuation.reset(user_message)
        self._turn_count = 0
        # ── Start autonomous session tracking ──
        self.autonomous.on_session_start(goal=user_message)

        self.messages.append({"role": "user", "content": user_message})
        # ── Track user input tokens ──
        user_tokens = max(1, len(user_message) // 3)
        self.autonomous.budget.record_input_message(user_tokens, "User message")

        while self._turn_count < MAX_TURNS:
            # ── Stop before starting a new turn if user requested it ──
            if self.cancel_requested:
                self.cancel_requested = False
                console.print(
                    "\n  [bold yellow]⏸ Paused by user. Type a new message to "
                    "redirect, or /continue to resume.[/bold yellow]\n"
                )
                return

            # ── Phase 5: Context Compaction for long sessions ──
            if self.autonomous.enabled and self.autonomous.should_compact_context(self.messages):
                console.print(
                    "\n  [bold cyan]🧠 Context compaction: trimming old messages "
                    "to keep session running smoothly...[/bold cyan]"
                )
                self.messages = self.autonomous.compact_context(self.messages)
                console.print(
                    f"  [dim green]✓ Compacted to {len(self.messages)} messages[/dim green]"
                )

            self._turn_count += 1
            should_continue = await self._process_turn(turn_number=self._turn_count)
            if not should_continue:
                self.autonomous.on_session_end()
                self._save_session_memory()
                self.notifications.notify_session_complete(
                    goal=self.autonomous.session.original_goal,
                    completed=len(self.autonomous.session.completed_tasks),
                    total=len(self.autonomous.session.completed_tasks) + (
                        1 if self.autonomous.session.current_task_id else 0
                    ),
                )
                return

        console.print(
            f"\n  [bold yellow]Reached max conversation turns ({MAX_TURNS}). "
            "Stopping to prevent infinite loop.[/bold yellow]\n"
        )
        self.autonomous.on_session_end()
        self._save_session_memory()
