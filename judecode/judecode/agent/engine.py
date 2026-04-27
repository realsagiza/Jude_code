"""Jude Code Agent Engine - handles the conversation loop and tool execution."""

import json
from typing import Any

from judecode.api.client import ApiClient
from judecode.agent.tools import TOOL_DEFINITIONS, execute_tool


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

        while True:
            full_content = ""
            tool_calls: list[dict[str, Any]] = []

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
                    print(content_piece, end="", flush=True)

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

            print()  # newline after streaming

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

            # Execute each tool
            for tc in tool_calls:
                if "name" not in tc:
                    continue
                tool_name = tc["name"]
                args_str = tc.get("arguments", "{}")
                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {}

                print(f"  > [tool: {tool_name}]")
                result = execute_tool(tool_name, args)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })
