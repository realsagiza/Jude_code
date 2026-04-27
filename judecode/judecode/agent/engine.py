"""Jude Code Agent Engine - handles the conversation loop and tool execution."""

import json
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

    async def chat(self, user_message: str) -> None:
        """Send a user message and handle streaming + tool calls."""
        self.messages.append({"role": "user", "content": user_message})

        turn = 0
        while True:
            turn += 1
            full_content = ""
            tool_calls: list[dict[str, Any]] = []
            has_started_output = False

            # Stream the response
            async for chunk in self.api.chat_completion(
                self.messages, tools=TOOL_DEFINITIONS
            ):
                choices = chunk.get("choices", [])
                if not choices:
                    continue

                delta = choices[0].get("delta", {})
                content_piece = delta.get("content")
                if content_piece:
                    full_content += content_piece
                    if not has_started_output:
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

            if has_started_output:
                console.print()  # newline after streaming

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

            # Execute each tool with visible output
            for tc in tool_calls:
                if "name" not in tc:
                    continue
                tool_name = tc["name"]
                args_str = tc.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {}

                # Show tool call with parameters
                console.print()
                if args:
                    args_preview = json.dumps(args, ensure_ascii=False, indent=2)
                    # Limit preview length
                    if len(args_preview) > 200:
                        args_preview = args_preview[:197] + "..."
                    console.print(
                        f"  [bold yellow]\u26a1[/bold yellow] Using tool [bold white]{tool_name}[/bold white]"
                    )
                    console.print(
                        f"     [dim]Parameters:[/dim] [bright_white]{args_preview}[/bright_white]"
                    )
                else:
                    console.print(
                        f"  [bold yellow]\u26a1[/bold yellow] Using tool [bold white]{tool_name}[/bold white]"
                    )

                result = execute_tool(tool_name, args)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })
                # Show result snippet
                result_preview = result[:300] + "..." if len(result) > 300 else result
                console.print(
                    f"     [dim green]Result:[/dim green] [dim]{result_preview}[/dim]"
                )
