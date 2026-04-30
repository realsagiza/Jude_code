"""Jude Code Agent Engine - handles the conversation loop and tool execution."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from judecode.api.client import ApiClient
from judecode.agent.tools import TOOL_DEFINITIONS, execute_tool
from judecode.ui.console import console


class AgentEngine:
    """Main agent logic: stream completions, handle tool calls, iterate."""

    def __init__(self, system_prompt: str, api_client: ApiClient):
        self.system_prompt = system_prompt
        self.api = api_client
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

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

    def _execute_tool_safe(self, tool_name: str, args: dict) -> str:
        """Execute a tool safely, catching TypeError and other exceptions."""
        try:
            return execute_tool(tool_name, args)
        except TypeError as e:
            return (
                f"Error executing tool '{tool_name}': "
                f"TypeError ({type(e).__name__}: {e}). "
                "This usually means a required parameter was missing."
            )

    async def chat(self, user_message: str) -> None:
        """Send a user message and handle streaming + tool calls."""
        self.messages.append({"role": "user", "content": user_message})

        MAX_TURNS = 100
        turn = 0
        while True:
            turn += 1
            full_content = ""
            full_reasoning = ""
            tool_calls: list[dict[str, Any]] = []
            has_started_output = False
            has_shown_reasoning = False
            reasoning_completed = False

            if turn > MAX_TURNS:
                console.print(
                    f"\n  [bold yellow]Reached max conversation turns ({MAX_TURNS}). "
                    "Stopping to prevent infinite loop.[/bold yellow]\n"
                )
                return

            # Show thinking indicator before each model response
            self._show_thinking(turn)

            # Stream the response
            try:
                async for chunk in self.api.chat_completion(
                    self.messages, tools=TOOL_DEFINITIONS
                ):
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})

                    # ── Extract reasoning/thinking content ──
                    reasoning_piece = self.api._extract_reasoning(chunk)
                    if reasoning_piece:
                        full_reasoning += reasoning_piece
                        if not reasoning_completed and not has_started_output:
                            if not has_shown_reasoning:
                                # Clear the "Thinking..." line and show reasoning header
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
                            # If we were showing reasoning, close the reasoning section
                            if has_shown_reasoning:
                                reasoning_completed = True
                                console.print()
                                console.print(
                                    "  [dim]───────────────── End Reasoning ───────────────────[/dim]"
                                )
                                console.print()
                            # Clear the thinking line when output starts
                            console.print()
                            has_started_output = True
                        console.print(content_piece, end="")

                    # Handle tool calls
                    tool_call_pieces = delta.get("tool_calls", [])
                    for tc in tool_call_pieces:
                        index = tc.get("index", 0)
                        if index >= len(tool_calls):
                            tool_calls.extend([{} for _ in range(index - len(tool_calls) + 1)])
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
                console.print(
                    f"\n  [bold red]Stream error:[/bold red] {type(e).__name__}: {e}\n"
                )
                return

            if has_started_output:
                console.print()  # newline after streaming
            elif has_shown_reasoning and not reasoning_completed:
                # Reasoning was shown but no content came - close the section
                reasoning_completed = True
                console.print()
                console.print(
                    "  [dim]───────────────── End Reasoning ───────────────────[/dim]"
                )
                console.print()

            # If there were no tool calls, just store the assistant message and return
            if not any("name" in tc for tc in tool_calls):
                self.messages.append({
                    "role": "assistant",
                    "content": full_content,
                })
                return

            # There were tool calls - we need to execute them and continue the conversation
            self.messages.append({
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
            })

            if turn == 1 and not has_started_output:
                console.print()  # ensure newline before tool calls if no content was streamed

            # ── Parallel Tool Execution ──
            # Parse all tool calls first
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

            if len(parsed_calls) == 1:
                # Single tool call - execute inline (no overhead)
                tc = parsed_calls[0]
                self._show_tool_call(tc["name"], tc["args"])
                try:
                    result = execute_tool(tc["name"], tc["args"])
                except TypeError as e:
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
                self._show_tool_result(result)
            else:
                # Multiple tool calls - execute in parallel!
                console.print(
                    f"\n  [bold cyan]⚡⚡⚡ Parallel execution: {len(parsed_calls)} tools[/bold cyan]"
                )
                for tc in parsed_calls:
                    self._show_tool_call(tc["name"], tc["args"])

                # Run all tools concurrently using ThreadPoolExecutor
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

                    # Wait for all to complete
                    results = await asyncio.gather(*futures, return_exceptions=True)

                # Process results
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
                    self._show_tool_result(result)
