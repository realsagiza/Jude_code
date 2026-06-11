"""Anthropic (Claude) API Client for Jude Code.

Translates Anthropic's native Messages API format ↔ OpenAI-compatible 
chunks so the existing AgentEngine works without modification.

Anthropic API docs: https://docs.anthropic.com/en/api/messages
"""

import json
import logging
from typing import AsyncGenerator, Optional, Any

import httpx

from judecode.config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, MAX_TOKENS, TEMPERATURE
from judecode.utils.logger import get_logger, log_error_details

logger = get_logger("judecode.api.anthropic")

# ── Anthropic API constants ──
ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicClient:
    """Client for Anthropic (Claude) Messages API.

    Converts OpenAI-format messages to Anthropic format on request,
    and Anthropic SSE streaming responses to OpenAI-compatible chunks.
    """

    def __init__(
        self,
        api_key: str = ANTHROPIC_API_KEY,
        model: str = ANTHROPIC_MODEL,
        base_url: str = ANTHROPIC_BASE_URL,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries

        if not self.api_key:
            logger.warning(
                "ANTHROPIC_API_KEY is not set. "
                "Set it via JUDECODE_ANTHROPIC_API_KEY env var or .env file."
            )

        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
        )
        # Track tool_use blocks being built during streaming
        self._tool_use_blocks: list[dict[str, Any]] = []
        self._current_content_index: int = -1
        self._content_blocks: list[dict[str, Any]] = []

    # ────────────────────────────────────────────────────────
    # Public API (same interface as ApiClient)
    # ────────────────────────────────────────────────────────

    async def chat_completion(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream: bool = True,
    ) -> AsyncGenerator[dict, None]:
        """Send chat completion request and yield OpenAI-compatible chunks.

        Translates messages to Anthropic format, streams the response,
        and yields chunks in OpenAI format that the AgentEngine expects.
        """
        # ── Build Anthropic request ──
        system_prompt, anthropic_messages, tools_anthropic = self._build_request(
            messages, tools
        )

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": anthropic_messages,
            "stream": stream,
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools_anthropic:
            payload["tools"] = tools_anthropic
        # Anthropic deprecated temperature for Claude models.
        # We intentionally omit it — Claude manages output diversity internally.

        url = f"{self.base_url}/messages"

        # ── Reset streaming state ──
        self._tool_use_blocks = []
        self._current_content_index = -1
        self._content_blocks = []

        last_error: Optional[BaseException] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                async with self._client.stream(
                    "POST", url, json=payload
                ) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        error_msg = (
                            f"Anthropic API error {response.status_code}: "
                            f"{error_body.decode()}"
                        )
                        log_error_details(
                            logger,
                            error_msg,
                            exc_info=False,
                            extra={
                                "model": self.model,
                                "url": url,
                                "status_code": response.status_code,
                                "attempt": attempt,
                            },
                        )
                        raise RuntimeError(error_msg)

                    if stream:
                        full_content = ""
                        try:
                            async for line in response.aiter_lines():
                                if not line:
                                    continue
                                # Anthropic uses SSE: "event: <type>" then "data: <json>"
                                if line.startswith("event: "):
                                    continue  # We handle events by data type
                                if not line.startswith("data: "):
                                    continue
                                data_str = line[6:].strip()
                                if not data_str:
                                    continue

                                try:
                                    event = json.loads(data_str)
                                except json.JSONDecodeError:
                                    continue

                                chunk = self._translate_event(event)
                                if chunk:
                                    # Accumulate content for error recovery
                                    content = self._extract_content(chunk)
                                    if content:
                                        full_content += content
                                    yield chunk

                        except (httpx.StreamError, httpx.RemoteProtocolError, TypeError) as e:
                            last_error = e
                            log_error_details(
                                logger,
                                f"Stream interrupted (attempt {attempt}/{self.max_retries})",
                                exc_info=False,
                                extra={
                                    "error_type": type(e).__name__,
                                    "error": str(e),
                                    "attempt": attempt,
                                    "content_length": len(full_content),
                                },
                            )
                            if attempt >= self.max_retries:
                                yield self._error_chunk(
                                    f"Stream interrupted after receiving partial content.\n"
                                    f"Partial content so far:\n{full_content}\n\n"
                                    f"Error: {type(e).__name__}: {e}\n\n"
                                    f"Please summarize the partial content and ask the user "
                                    f"if they want to retry or continue."
                                )
                                return
                            continue  # Retry
                    else:
                        body = await response.aread()
                        event = json.loads(body)
                        chunk = self._translate_non_streaming(event)
                        yield chunk
                    return  # Success

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                log_error_details(
                    logger,
                    f"Connection error (attempt {attempt}/{self.max_retries})",
                    exc_info=False,
                    extra={
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "attempt": attempt,
                    },
                )
                if attempt >= self.max_retries:
                    yield self._error_chunk(
                        f"Connection failed after {self.max_retries} attempts.\n"
                        f"Error: {type(e).__name__}: {e}\n"
                        f"Please check your network connection and try again."
                    )
                    return
            except RuntimeError:
                # Already handled, re-raise to stop retrying on 4xx errors
                raise
            except httpx.HTTPError as e:
                last_error = e
                log_error_details(
                    logger,
                    f"HTTP error (attempt {attempt}/{self.max_retries})",
                    exc_info=False,
                    extra={
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "attempt": attempt,
                    },
                )
                if attempt >= self.max_retries:
                    yield self._error_chunk(
                        f"HTTP error after {self.max_retries} attempts.\n"
                        f"Error: {type(e).__name__}: {e}\n"
                        f"Please check your network connection and try again."
                    )
                    return
            except Exception as e:
                # Catch-all for unexpected errors (e.g., KeyError in event translation)
                last_error = e
                log_error_details(
                    logger,
                    f"Unexpected error in Anthropic streaming (attempt {attempt}/{self.max_retries})",
                    exc_info=True,
                    extra={
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "attempt": attempt,
                    },
                )
                if attempt >= self.max_retries:
                    yield self._error_chunk(
                        f"Unexpected error after {self.max_retries} attempts.\n"
                        f"Error: {type(e).__name__}: {e}\n"
                        f"Please report this issue or try again."
                    )
                    return

        if last_error:
            yield self._error_chunk(str(last_error))

    @staticmethod
    def _extract_content(chunk: dict) -> str:
        """Best-effort extract text content from an OpenAI-format chunk."""
        choices = chunk.get("choices", [])
        if not choices:
            return ""
        delta = choices[0].get("delta", {})
        return delta.get("content") or ""

    @staticmethod
    def _extract_reasoning(chunk: dict) -> str:
        """Extract reasoning/thinking content from chunk.

        For Anthropic, reasoning comes from thinking blocks mapped to reasoning_content.
        """
        choices = chunk.get("choices", [])
        if not choices:
            return ""
        delta = choices[0].get("delta", {})
        return (
            delta.get("reasoning_content")
            or delta.get("reasoning")
            or delta.get("thinking")
            or ""
        )

    @staticmethod
    def _error_chunk(content: str) -> dict:
        """Build a synthetic assistant chunk with error message (OpenAI format)."""
        return {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": content,
                    },
                    "finish_reason": "stop",
                    "index": 0,
                }
            ],
            "object": "chat.completion.chunk",
        }

    async def close(self):
        await self._client.aclose()

    # ────────────────────────────────────────────────────────
    # Message Translation: OpenAI → Anthropic
    # ────────────────────────────────────────────────────────

    def _build_request(
        self, messages: list[dict], tools: Optional[list[dict]]
    ) -> tuple[str, list[dict], Optional[list[dict]]]:
        """Convert OpenAI-format messages + tools to Anthropic format.

        Returns: (system_prompt, anthropic_messages, anthropic_tools)
        """
        system_prompt = ""
        # Extract system prompt from messages
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
                break

        # Convert remaining messages (skip system), merging consecutive same-role messages
        anthropic_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                continue
            converted = self._convert_message(msg)
            if converted is None:
                continue
            
            if anthropic_messages and converted["role"] == anthropic_messages[-1]["role"]:
                # Merge consecutive same-role messages (Anthropic requires alternating)
                last = anthropic_messages[-1]
                last_content = last["content"]
                new_content = converted["content"]
                
                # Both are lists → extend
                if isinstance(last_content, list) and isinstance(new_content, list):
                    last_content.extend(new_content)
                # Both are strings → concatenate with newline
                elif isinstance(last_content, str) and isinstance(new_content, str):
                    last["content"] = last_content + "\n" + new_content
                # One is list, one is string → convert string to text block and append
                elif isinstance(last_content, list):
                    if isinstance(new_content, str):
                        last_content.append({"type": "text", "text": new_content})
                    else:
                        last_content.extend(new_content if isinstance(new_content, list) else [new_content])
                else:
                    # last is string, new is list → convert string to text block
                    last["content"] = [{"type": "text", "text": last_content}]
                    if isinstance(new_content, list):
                        last["content"].extend(new_content)
                    else:
                        last["content"].append(new_content)
            else:
                anthropic_messages.append(converted)

        # Convert tools
        tools_anthropic = None
        if tools:
            tools_anthropic = [self._convert_tool(t) for t in tools]

        return system_prompt, anthropic_messages, tools_anthropic

    def _convert_message(self, msg: dict) -> Optional[dict]:
        """Convert a single OpenAI-format message to Anthropic format."""
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "user":
            # Simple text user message
            if isinstance(content, str):
                return {"role": "user", "content": content}
            # Already structured content
            return {"role": "user", "content": content}

        elif role == "assistant":
            return self._convert_assistant_message(msg)

        elif role == "tool":
            # OpenAI tool result → Anthropic tool_result (wrapped in user message)
            tool_call_id = msg.get("tool_call_id", "")
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": content if isinstance(content, str) else str(content),
                    }
                ],
            }

        return None

    def _convert_assistant_message(self, msg: dict) -> dict:
        """Convert an assistant message, handling tool_calls."""
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls", [])

        if not tool_calls:
            # Plain text response
            return {
                "role": "assistant",
                "content": content if content else "",
            }

        # Message with tool_calls → Anthropic content blocks
        blocks = []
        if content and content.strip():
            blocks.append({"type": "text", "text": content})

        for tc in tool_calls:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}

            blocks.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": tool_name,
                "input": args,
            })

        return {
            "role": "assistant",
            "content": blocks,
        }

    @staticmethod
    def _convert_tool(tool: dict) -> dict:
        """Convert OpenAI tool definition to Anthropic format."""
        fn = tool.get("function", {})
        return {
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        }

    # ────────────────────────────────────────────────────────
    # Response Translation: Anthropic SSE → OpenAI chunks
    # ────────────────────────────────────────────────────────

    def _translate_event(self, event: dict) -> Optional[dict]:
        """Translate a single Anthropic SSE event to OpenAI chunk format.

        Returns None for events that don't produce visible output 
        (message_start, ping, etc.)
        """
        event_type = event.get("type", "")

        if event_type == "message_start":
            return None  # No visible output

        elif event_type == "content_block_start":
            return self._handle_content_block_start(event)

        elif event_type == "content_block_delta":
            return self._handle_content_block_delta(event)

        elif event_type == "content_block_stop":
            return None  # No visible output (index tracking done internally)

        elif event_type == "message_delta":
            return self._handle_message_delta(event)

        elif event_type == "message_stop":
            return None  # End of stream

        return None

    def _handle_content_block_start(self, event: dict) -> Optional[dict]:
        """Handle the start of a content block."""
        index = event.get("index", 0)
        content_block = event.get("content_block", {})

        # Ensure _content_blocks is large enough
        while len(self._content_blocks) <= index:
            self._content_blocks.append({})

        self._content_blocks[index] = {
            "type": content_block.get("type"),
            "index": index,
        }

        if content_block.get("type") == "tool_use":
            # Start of a tool use block — emit id and name
            tool_info = {
                "id": content_block.get("id", ""),
                "name": content_block.get("name", ""),
                "input_json": "",  # Will be accumulated from deltas
            }
            self._content_blocks[index]["tool_use"] = tool_info

            # Emit chunk with tool id + name (arguments come from deltas)
            return {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": tool_info["id"],
                                    "function": {
                                        "name": tool_info["name"],
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                        "index": 0,
                    }
                ],
                "object": "chat.completion.chunk",
            }

        elif content_block.get("type") == "thinking":
            # Thinking block → reasoning_content
            self._content_blocks[index]["thinking"] = content_block.get("thinking", "")

        return None  # Content comes in deltas

    def _handle_content_block_delta(self, event: dict) -> Optional[dict]:
        """Handle content block deltas (text, input_json, thinking)."""
        index = event.get("index", 0)
        delta = event.get("delta", {})
        delta_type = delta.get("type", "")

        if delta_type == "text_delta":
            text = delta.get("text", "")
            return {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "content": text,
                        },
                        "finish_reason": None,
                        "index": 0,
                    }
                ],
                "object": "chat.completion.chunk",
            }

        elif delta_type == "input_json_delta":
            # Partial tool arguments
            partial_json = delta.get("partial_json", "")
            return {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": index,
                                    "function": {
                                        "arguments": partial_json,
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                        "index": 0,
                    }
                ],
                "object": "chat.completion.chunk",
            }

        elif delta_type == "thinking_delta":
            thinking_text = delta.get("thinking", "")
            return {
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "reasoning_content": thinking_text,
                        },
                        "finish_reason": None,
                        "index": 0,
                    }
                ],
                "object": "chat.completion.chunk",
            }

        elif delta_type == "signature_delta":
            # Signature delta - internal, skip
            return None

        return None

    def _handle_message_delta(self, event: dict) -> Optional[dict]:
        """Handle message_delta event (contains stop_reason)."""
        delta = event.get("delta", {})
        stop_reason = delta.get("stop_reason", "")

        # Map Anthropic stop_reason to OpenAI finish_reason
        finish_reason_map = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }
        finish_reason = finish_reason_map.get(stop_reason, "stop")

        return {
            "choices": [
                {
                    "delta": {},
                    "finish_reason": finish_reason,
                    "index": 0,
                }
            ],
            "object": "chat.completion.chunk",
        }

    def _translate_non_streaming(self, response: dict) -> dict:
        """Translate a non-streaming Anthropic response to OpenAI format."""
        content_blocks = response.get("content", [])
        stop_reason = response.get("stop_reason", "end_turn")

        text_content = ""
        tool_calls = []
        reasoning = ""

        for i, block in enumerate(content_blocks):
            block_type = block.get("type")
            if block_type == "text":
                text_content += block.get("text", "")
            elif block_type == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })
            elif block_type == "thinking":
                reasoning += block.get("thinking", "")

        finish_reason_map = {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }
        finish_reason = finish_reason_map.get(stop_reason, "stop")

        message = {
            "role": "assistant",
            "content": text_content,
        }
        if tool_calls:
            message["tool_calls"] = tool_calls
        if reasoning:
            message["reasoning_content"] = reasoning

        return {
            "choices": [
                {
                    "message": message,
                    "finish_reason": finish_reason,
                    "index": 0,
                }
            ],
            "object": "chat.completion",
        }
