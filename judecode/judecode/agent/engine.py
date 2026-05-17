"""Jude Code Agent Engine - handles the conversation loop and tool execution."""

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from judecode.api.client import ApiClient
from judecode.agent.tools import TOOL_DEFINITIONS, execute_tool
from judecode.agent.continuation import (
    ContinuationTracker,
    detect_stream_interruption,
    detect_incomplete_work,
    detect_completion,
    detect_token_limit_truncation,
    generate_continuation_nudge,
)
from judecode.config import (
    MAX_CONTINUATIONS,
    MAX_TURNS,
    CONTINUE_ON_STREAM_ERROR,
    CONTINUE_ON_INCOMPLETE_WORK,
    CONTINUE_ON_TOOL_ERROR,
)
from judecode.ui.console import console
from judecode.utils.logger import get_logger, log_error_details

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
        self.messages.append({"role": "user", "content": nudge})
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

        # Stream the response
        try:
            async for chunk in self.api.chat_completion(
                self.messages, tools=TOOL_DEFINITIONS
            ):
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
                        console.print(
                            f"[dim][italic]{reasoning_piece}[/italic][/dim]", end=""
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
                    console.print(content_piece, end="")

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
                self.messages.append({"role": "user", "content": nudge})
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
                "content": full_content or None,
            }
            if full_reasoning:
                msg["reasoning_content"] = full_reasoning
            self.messages.append(msg)
            return False  # Stop the loop

        # If no tool calls, store assistant message and check for continuation
        if not has_tool_calls:
            msg: dict[str, Any] = {
                "role": "assistant",
                "content": full_content or None,
            }
            if full_reasoning:
                msg["reasoning_content"] = full_reasoning
            self.messages.append(msg)

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
                    self.messages.append({"role": "user", "content": nudge})
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
                self.messages.append({"role": "user", "content": nudge})
                return True  # Continue

        if len(parsed_calls) == 1:
            tc = parsed_calls[0]
            self._show_tool_call(tc["name"], tc["args"])
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
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
                tool_results.append(result)
                self._show_tool_result(result)

        # ── Check for cancel/stop BEFORE auto-continuation ──
        if self.cancel_requested:
            self.cancel_requested = False
            console.print(
                "\n  [bold yellow]⏸ Paused by user after tool execution. Type a new message to redirect, or /continue to resume.[/bold yellow]\n"
            )
            return False  # Stop the loop

        # ── Auto-continuation check after tool execution ──
        if (
            not self._is_nudge_message(full_content)
            and self.continuation.can_continue()
        ):
            has_tool_error = any(
                detect_stream_interruption(r) for r in tool_results
            ) or any(
                "error executing tool" in r.lower() for r in tool_results
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
                self.messages.append({"role": "user", "content": nudge})
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
                self.messages.append({"role": "user", "content": nudge})
                return True  # Continue

        # ── Check if work is clearly done before auto-continuing ──
        if not self._is_nudge_message(full_content) and detect_completion(full_content):
            return False  # Work is done, stop

        return True  # Continue to next turn naturally (no nudge needed)

    async def chat(self, user_message: str) -> None:
        """Send a user message and handle streaming + tool calls."""
        # Reset continuation tracker for new user message
        self.continuation.reset(user_message)

        self.messages.append({"role": "user", "content": user_message})

        while self._turn_count < MAX_TURNS:
            self._turn_count += 1
            should_continue = await self._process_turn(turn_number=self._turn_count)
            if not should_continue:
                return

        console.print(
            f"\n  [bold yellow]Reached max conversation turns ({MAX_TURNS}). "
            "Stopping to prevent infinite loop.[/bold yellow]\n"
        )
