"""Jude Code Agent Engine - handles the conversation loop and tool execution."""

import asyncio
import json
import logging
import os
from pathlib import Path
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
    CONTEXT_TARGET_TOKENS,
    CONTEXT_RESULT_CHARS,
    CONTEXT_RECENT_RESULTS,
)
from judecode.agent.context import ContextManager, estimate_tokens
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
        self.context = ContextManager(
            CONTEXT_TARGET_TOKENS, CONTEXT_RESULT_CHARS, CONTEXT_RECENT_RESULTS
        )
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
        self._stop_outcome = None
        self._context_recovery_attempts = 0
        self._tool_failures = {}
        self._needs_verification = False
        self._verification = None
        self.sandbox.before_apply = self._snapshot_file
        self._resume_uncertain = False
        self._restored_session = False


    def save_transcript(self) -> None:
        """Persist before requests/actions and after results; fail closed on I/O errors."""
        from judecode.agent.transcripts import TranscriptStore
        session = self.autonomous.session
        state = {key: getattr(session, key) for key in (
            "original_goal", "completed_tasks", "current_task_id", "total_turns",
            "total_tool_calls", "status", "outcome_reason", "errors", "started_at")}
        budget = self.autonomous.budget
        TranscriptStore().save(session.session_id, {
            "cwd": str(Path.cwd().resolve()), "messages": self.messages,
            "state": state, "needs_verification": self._needs_verification,
            "failures": [[list(key), value] for key, value in self._tool_failures.items()],
            "uncertain": self._resume_uncertain,
            "staged": bool(self.sandbox.get_pending_changes()),
            "budget": {key: getattr(budget, key) for key in (
                "total_input_tokens", "total_output_tokens", "total_cost", "tokens")},
        })

    def sessions_summary(self) -> str:
        from judecode.agent.transcripts import TranscriptStore
        rows = TranscriptStore().list(Path.cwd())[:10]
        if not rows:
            return "No saved conversations for this project yet."
        return "Saved conversations (newest first):\n" + "\n".join(
            f"{row['session_id']} | {row['state']['status']} | "
            f"{str(row['state']['original_goal'])[:100]}" for row in rows
        ) + "\nUse /resume <id> or /resume latest."

    def resume_session(self, session_id: str) -> str:
        from judecode.agent.transcripts import TranscriptStore
        store = TranscriptStore()
        if self.sandbox.get_pending_changes():
            raise ValueError("Apply or resolve current staged changes before resuming")
        if session_id == "latest":
            rows = [row for row in store.list(Path.cwd())
                    if row['session_id'] != self.autonomous.session.session_id]
            if not rows:
                raise ValueError("No previous saved conversation for this project")
            session_id = rows[0]['session_id']
        data, pending = store.load(session_id, Path.cwd())
        if data.get('staged'):
            raise ValueError("This snapshot has staged changes; sandbox restoration is not supported")
        state = data.get('state')
        if not isinstance(state, dict) or not isinstance(data.get('budget'), dict):
            raise ValueError("Invalid saved session state")
        # Construct a fresh engine first: forks preserve the source snapshot and
        # avoid two processes writing the same session/checkpoint directory.
        restored = AgentEngine(self.system_prompt, self.api,
            max_continuations=self.continuation.max_continuations,
            continue_on_stream_error=self.continuation.continue_on_stream_error,
            continue_on_incomplete_work=self.continuation.continue_on_incomplete_work,
            continue_on_tool_error=self.continuation.continue_on_tool_error)
        restored.autonomous.enabled = self.autonomous.enabled
        for key in ('original_goal', 'completed_tasks', 'current_task_id',
                    'total_turns', 'total_tool_calls', 'errors', 'started_at'):
            setattr(restored.autonomous.session, key, state[key])
        for key in ('total_input_tokens', 'total_output_tokens', 'total_cost', 'tokens'):
            setattr(restored.autonomous.budget, key, data['budget'][key])
        restored._restored_session = True
        restored.messages = data['messages']
        restored._tool_failures = {tuple(key): value for key, value in data.get('failures', [])}
        restored._needs_verification = bool(data.get('needs_verification')) or bool(pending)
        restored._resume_uncertain = bool(pending) or bool(data.get('uncertain'))
        for identifier in sorted(pending):
            restored.messages.append({"role": "tool", "tool_call_id": identifier,
                "content": "Execution outcome unknown after interruption. Do not replay this action. Inspect current state before deciding what remains."})
        restored.autonomous.session.status = 'paused'
        restored.autonomous.session.outcome_reason = 'Restored from ' + session_id
        restored.continuation.reset(state['original_goal'])
        restored.save_transcript()
        if len(self.messages) > 1:
            self.save_transcript()
        self.__dict__.update(restored.__dict__)
        # Bound callback was constructed on the temporary engine.
        self.sandbox.before_apply = self._snapshot_file
        self.autonomous.session.save()
        note = ("Some tool outcomes are unknown. Inspect the project and send a new instruction; "
                "/continue is blocked until then." if self._resume_uncertain else
                "Use /continue or send a new instruction to proceed.")
        return f"Restored {session_id} as {self.autonomous.session.session_id}: {len(self.messages)} messages. No tools executed.\n{note}"

    def restored_preview(self) -> str:
        """A bounded display preview; the engine retains the entire saved history."""
        visible = [message for message in self.messages
                   if message['role'] in ('user', 'assistant') and message.get('content')]
        return "\n\n".join(
            f"{message['role'].capitalize()}: {str(message['content'])[:3000]}"
            for message in visible[-6:])

    def new_conversation(self) -> None:
        if self.sandbox.get_pending_changes():
            raise ValueError("Resolve staged changes before clearing the conversation")
        if len(self.messages) > 1:
            self.save_transcript()
        fresh = AgentEngine(self.system_prompt, self.api)
        fresh.autonomous.enabled = self.autonomous.enabled
        self.__dict__.update(fresh.__dict__)
        self.sandbox.before_apply = self._snapshot_file

    # ── Context Management (Token Optimization) ──

    def _prune_context(self) -> None:
        """Reduce old output only after saving a retrievable full copy."""
        self.messages = self.context.prune(self.messages, TOOL_DEFINITIONS)
        self.autonomous.budget.context_tokens_saved = self.context.saved_tokens
        self.autonomous.budget.context_input_tokens = self.context.last_input_tokens

    def _recover_context(self) -> bool:
        """Shrink bulky output after rejection, preserving instructions and tool pairs.

        Archive the full current history before replacing any content. Never
        alter tool arguments or replay executed tools. Bound retries even when
        the provider's actual request limit is unknown.
        """
        if self.cancel_requested or self._context_recovery_attempts >= 3:
            return False
        limit = 4000 // (4 ** self._context_recovery_attempts)
        reduced = []
        for message in self.messages:
            item = dict(message)
            if item.get("role") in ("tool", "assistant"):
                for field in ("content", "reasoning_content"):
                    value = item.get(field)
                    if isinstance(value, str) and len(value) > limit:
                        item[field] = (value[:limit // 2] +
                            "\n[Output shortened after API size rejection; full history archived locally.]\n" +
                            value[-limit // 2:])
            reduced.append(item)
        if reduced == self.messages:
            return False
        try:
            import tempfile
            archive_dir = Path.home() / ".judecode" / "context-recovery"
            archive_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                    prefix="history-", suffix=".json", dir=archive_dir,
                    delete=False) as archive:
                json.dump(self.messages, archive, ensure_ascii=False)
                archive_path = archive.name
        except OSError:
            logger.exception("Cannot archive history; preserving current context")
            return False
        self.messages = reduced
        self._context_recovery_attempts += 1
        console.print(Text(
            f"\n  ↻ Request too large: shortened output, retrying "
            f"({self._context_recovery_attempts}/3). History: {archive_path}\n",
            style="yellow"))
        return True

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
        label = "Failed" if result.lstrip().lower().startswith("error executing tool") else "Result"
        console.print(Text(f"     {label}: {result_preview}"))

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

    # Conservatively treat other tools as requiring outcome verification.
    READ_ONLY_TOOLS = {"read", "glob", "grep", "ls", "web_fetch", "web_search",
                       "codebase_search", "codebase_summary", "vault_read_note",
                       "vault_search", "task_list", "task_get", "task_summary"}

    def _snapshot_file(self, path: str, reason: str = "apply") -> None:
        if os.path.exists(path) and not self.backups.backup_file(path, reason=reason):
            raise RuntimeError(f"Backup failed: {path}; modification cancelled")
        checkpoint = self.checkpoint.create_checkpoint(
            file_paths=[path], reason=reason,
            task_id=self.autonomous.session.current_task_id)
        if any(f.get("error") for f in checkpoint["files"]):
            raise RuntimeError(f"Checkpoint failed: {path}; modification cancelled")
        if reason == "sandbox_apply":
            self._needs_verification = True
            self._verification = None
            self.autonomous.session.status = "unverified"
            self.autonomous.session.outcome_reason = "Sandbox changes applied; verification pending."
            self.autonomous.session.save()

    def _verify_work(self) -> dict:
        if self.sandbox.is_active and self.sandbox.get_pending_changes():
            return {"passed": False, "status": "unverified", "results": [],
                    "summary": "Changes are staged only; real project has not been verified."}
        if self._verification is None:
            try:
                self._verification = self.autonomous.evaluator.run_verification()
            except Exception as exc:
                self._verification = {"passed": False, "status": "failed", "results": [],
                                      "summary": f"Verification failed to run: {exc}"}
        return self._verification

    def _execute_tool_safe(self, tool_name: str, args: dict) -> str:
        """All execution paths share preflight, staging and outcome recording."""
        key = (tool_name, str(Path(args["path"]).resolve()) if "path" in args
               else json.dumps(args, sort_keys=True))
        try:
            blocked = self._pre_tool_hook(tool_name, args)
            if blocked:
                result = f"Error executing tool '{tool_name}': {blocked}"
            elif self.sandbox.is_active:
                result = self.sandbox.execute(tool_name, args)
            else:
                if tool_name not in self.READ_ONLY_TOOLS and tool_name != "task_complete":
                    self._needs_verification = True
                    self._verification = None
                result = execute_tool(tool_name, args)
            self._post_tool_hook(tool_name, args, result)
        except Exception as exc:
            result = f"Error executing tool '{tool_name}': {type(exc).__name__}: {exc}"
        if result.lstrip().lower().startswith("error executing tool") or result.startswith("❌"):
            self._tool_failures[key] = result
        else:
            self._tool_failures.pop(key, None)
        return result

    def _finish_session(self) -> None:
        if self._stop_outcome:
            status, reason = self._stop_outcome
        elif self._tool_failures:
            status, reason = "failed", "Unresolved tool failures remain."
        elif self.sandbox.get_pending_changes():
            status, reason = "unverified", "Changes are staged, not applied or verified."
        elif self._needs_verification:
            verification = self._verify_work()
            status = {"passed": "completed", "failed": "failed"}.get(
                verification["status"], "unverified")
            reason = verification["summary"]
        else:
            status, reason = "answered", "Response delivered; no implementation success claimed."
        self.autonomous.on_session_end(status, reason)
        self._save_session_memory()
        try:
            self.save_transcript()
        except (OSError, ValueError) as exc:
            console.print(Text(f"Session snapshot could not be saved: {exc}", style="red"))
        console.print(Text(f"\nSession outcome: {status} — {reason}"))
        if status == "completed":
            self.notifications.notify_session_complete(
                goal=self.autonomous.session.original_goal,
                completed=len(self.autonomous.session.completed_tasks),
                total=len(self.autonomous.session.completed_tasks))

    def _is_nudge_message(self, content: str) -> bool:
        """Check if a message is a system nudge (starts with [SYSTEM:)."""
        return content.strip().startswith("[SYSTEM:")

    def _append_nudge(self, content: str, reason: str = "") -> None:
        """Append a nudge; count its cost when it is actually sent."""
        self.messages.append({"role": "user", "content": content})

    def _record_request_tokens(self) -> None:
        """Count the whole outgoing request on every turn, including tool schemas."""
        budget = self.autonomous.budget
        budget.new_turn()
        budget.record_tool_request(estimate_tokens(TOOL_DEFINITIONS))
        for message in self.messages:
            count = estimate_tokens(message)
            role = message.get("role")
            if role == "system":
                budget.record_system_prompt(count)
            elif role == "tool":
                budget.record_tool_result_tokens(count)
            else:
                budget.record_input_message(count, "Estimated resent context")

    def _track_token_usage(self, content: str, reasoning: str = "", tool_calls=None) -> None:
        """Count generated text/reasoning/arguments exactly once, including partial streams."""
        count = estimate_tokens(content) + estimate_tokens(reasoning)
        if tool_calls:
            count += estimate_tokens(tool_calls)
        if count:
            self.autonomous.budget.record_output_message(count, "Estimated API output")

    def _save_session_memory(self) -> None:
        """Save session summary to cross-session memory on session end."""
        try:
            s = self.autonomous.session
            self.memory.save_session_summary(
                session_id=s.session_id,
                goal=s.original_goal,
                status=s.status,
                outcome_reason=s.outcome_reason,
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
                summary = f"[{s.status}] " + goal[:150]
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

        if self.sandbox.is_active:
            if tool_name not in {"read", "write", "edit", "delete"}:
                return "Sandbox supports only read/write/edit/delete; this tool is not staged."
            return None

        if tool_name == "task_complete":
            if any(name != "task_complete" for name, _ in self._tool_failures):
                return "Unresolved tool failures must be resolved before task completion"
            verification = self._verify_work()
            if not verification["passed"]:
                return f"Task completion not verified: {verification['summary']}"
            self._needs_verification = True

        if tool_name in {"write", "edit", "delete"}:
            path = tool_params.get("path")
            if not path:
                return "Missing file path"
            self._snapshot_file(path, tool_name)

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
        a 'context length exceeded' / 'too large' style error. Recovery must
        reduce the request before retrying, with a bounded retry count.
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
        if self._resume_uncertain:
            console.print(Text("Cannot continue: interrupted tool outcomes are unknown. Inspect state and send a new instruction.", style="yellow"))
            return
        self.reset_stop()
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
        self._stop_outcome = None
        self.autonomous.session.status = "active"
        self.autonomous.session.save()
        while self._turn_count < MAX_TURNS:
            self._turn_count += 1
            try:
                more = await self._process_turn(turn_number=self._turn_count)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self._stop_outcome = ("paused", "Execution interrupted")
                self._finish_session()
                raise
            except Exception as exc:
                self._stop_outcome = ("failed", str(exc))
                self._finish_session()
                raise
            if not more:
                self._finish_session()
                return
        self._stop_outcome = ("unverified", "Maximum turns reached")
        self._finish_session()

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

        # Track whether the stream was aborted mid-flight by Ctrl+C
        stream_aborted = False

        if self._needs_verification and self._verification is None:
            verification = await asyncio.to_thread(self._verify_work)
            self.messages.append({"role": "user", "content":
                "[SYSTEM: Verification status: " + verification["status"] + ". " +
                verification["summary"] +
                " Do not claim verified success unless checks passed; report limitations.]"})

        # Include fresh verification context before estimating/pruning the request.
        self._prune_context()
        # Estimates include resends; provider billing/cache discounts may differ.
        self.save_transcript()
        self._record_request_tokens()
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

                if chunk.get("error"):
                    if self._is_context_overflow_error(str(chunk["error"])):
                        raise RuntimeError(str(chunk["error"]))
                    self._stop_outcome = ("failed", str(chunk["error"]))
                    return False
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

            # Retry only after reducing context; never resend it unchanged.
            if self._is_context_overflow_error(error_msg):
                if self._recover_context():
                    return True
                # Preserve partial prose when recovery is exhausted.
                if full_content:
                    self.messages.append({
                        "role": "assistant",
                        "content": full_content,
                    })
                console.print(
                    "\n  [bold red]⛔ Context too large — the conversation has grown "
                    "beyond the model's limit.[/bold red]\n"
                    "  [yellow]Auto-continuation stopped to prevent an infinite loop.[/yellow]\n"
                    "  [dim]Your conversation and work are retained. Shorten oversized "
                    "input or change the model, then use /continue.[/dim]\n"
                )
                self._stop_outcome = ("failed", "Context overflow")
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
            self._stop_outcome = ("failed", error_msg)
            return False

        finally:
            self._track_token_usage(full_content, full_reasoning, tool_calls)
            self.autonomous.on_turn_complete()

        self._context_recovery_attempts = 0
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
            self.save_transcript()
            console.print(
                "\n  [bold yellow]⏸ Stopped mid-response by user. "
                "Type a new message to redirect, or /continue to resume.[/bold yellow]\n"
            )
            self._stop_outcome = ("paused", "Stopped by user")
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
            self.save_transcript()
            self._stop_outcome = ("paused", "Stopped by user")
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
            self.save_transcript()

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
                self._stop_outcome = ("unverified", "Response truncated; continuation limit reached")
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
        self.save_transcript()

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
                args = None
            if not isinstance(args, dict):
                self.messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                      "content": "Error executing tool: invalid or truncated JSON; retry complete arguments."})
                self._tool_failures[(tc["name"], "invalid_arguments")] = "Invalid tool arguments"
                continue
            self._tool_failures.pop((tc["name"], "invalid_arguments"), None)
            parsed_calls.append({
                "id": tc.get("id", ""),
                "name": tc["name"],
                "args": args,
            })

        # Serialize tool batches: dependencies and same-file writes must preserve order.
        # Complete calls received with finish_reason=length use this same preflight.
        for tc in parsed_calls:
            self._show_tool_call(tc["name"], tc["args"])
            if self.cancel_requested:
                result = "Error executing tool: skipped because user stopped the batch"
            else:
                self.save_transcript()
                result = await asyncio.to_thread(self._execute_tool_safe, tc["name"], tc["args"])
            self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            self.save_transcript()
            tool_results.append(result)
            self._show_tool_result(result)

        # ── Check for cancel/stop BEFORE auto-continuation ──
        if self.cancel_requested:
            self.cancel_requested = False
            console.print(
                "\n  [bold yellow]⏸ Paused by user after tool execution. Type a new message to redirect, or /continue to resume.[/bold yellow]\n"
            )
            self._stop_outcome = ("paused", "Stopped by user")
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
        if (not self._needs_verification and not self._tool_failures
                and not self._is_nudge_message(full_content) and detect_completion(full_content)):
            return False  # Work is done, stop

        return True  # Continue to next turn naturally (no nudge needed)

    async def chat(self, user_message: str) -> None:
        """Send a user message and handle streaming + tool calls."""
        # A new explicit instruction permits inspecting uncertain interrupted work.
        self._resume_uncertain = False
        # Reset for new user message
        self._stop_outcome = None
        if not self._restored_session:
            self._tool_failures = {}
            self._needs_verification = False
        self._restored_session = False
        self._verification = None
        self.continuation.reset(user_message)
        self._turn_count = 0
        # ── Start autonomous session tracking ──
        self.autonomous.on_session_start(goal=user_message)

        self.messages.append({"role": "user", "content": user_message})

        while self._turn_count < MAX_TURNS:
            # ── Stop before starting a new turn if user requested it ──
            if self.cancel_requested:
                self.cancel_requested = False
                console.print(
                    "\n  [bold yellow]⏸ Paused by user. Type a new message to "
                    "redirect, or /continue to resume.[/bold yellow]\n"
                )
                self._stop_outcome = ("paused", "Stopped by user")
                self._finish_session()
                return

            self._turn_count += 1
            try:
                should_continue = await self._process_turn(turn_number=self._turn_count)
            except (KeyboardInterrupt, asyncio.CancelledError):
                self._stop_outcome = ("paused", "Execution interrupted")
                self._finish_session()
                raise
            except Exception as exc:
                self._stop_outcome = ("failed", f"{type(exc).__name__}: {exc}")
                self._finish_session()
                raise
            if not should_continue:
                self._finish_session()
                return

        console.print(
            f"\n  [bold yellow]Reached max conversation turns ({MAX_TURNS}). "
            "Stopping to prevent infinite loop.[/bold yellow]\n"
        )
        self._stop_outcome = ("unverified", "Maximum turns reached")
        self._finish_session()
