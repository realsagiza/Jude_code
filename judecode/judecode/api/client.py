"""API Client for communicating with DeepSeek API (OpenAI-compatible).

ใช้ Cloud AI API โดยตรง (DeepSeek, Z.AI, Anthropic)
Endpoint: https://api.deepseek.com/chat/completions
"""

import json
import logging
from typing import AsyncGenerator, Optional

import httpx

from judecode.config import BASE_URL, API_KEY, MODEL, MAX_TOKENS, TEMPERATURE
from judecode.utils.logger import get_logger, log_error_details

logger = get_logger("judecode.api")


class ApiClient:
    """Client for DeepSeek API (OpenAI-compatible) chat completions."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        api_key: str = API_KEY,
        model: str = MODEL,
        fallback_model: str = "deepseek-chat",  # Non-thinking fallback
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
        max_retries: int = 3,  # Increased from 2 to 3 for better resilience
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.fallback_model = fallback_model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
        self._has_fallback = False  # Track if we already fell back

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat_completion(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream: bool = True,
    ) -> AsyncGenerator[dict, None]:
        """
        Send a chat completion request and yield response chunks.
        For non-streaming, yields a single final dict.
        Auto-retries on stream errors up to max_retries.
        If all retries exhausted, yields a synthetic error chunk
        so the agent can recover gracefully instead of crashing.
        """
        # ── Sanitize messages: ensure assistant messages always have content or tool_calls ──
        sanitized = []
        for msg in messages:
            if msg.get("role") == "assistant":
                has_content = bool(msg.get("content"))
                has_tool_calls = bool(msg.get("tool_calls"))
                if not has_content and not has_tool_calls:
                    # API rejects assistant messages with neither content nor tool_calls
                    msg = {**msg, "content": ""}  # Set empty string as fallback
            sanitized.append(msg)

        payload = {
            "model": self.model,
            "messages": sanitized,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()

        last_error: Optional[BaseException] = None
        attempt = 0

        while attempt < self.max_retries:
            attempt += 1
            try:
                async with self._client.stream(
                    "POST", url, json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        error_msg = f"API error {response.status_code}: {error_body.decode()}"
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
                        # ── Auto-fallback: reasoning_content error → switch to non-thinking model ──
                        if "reasoning_content" in error_msg and not self._has_fallback:
                            self._has_fallback = True
                            old_model = self.model
                            self.model = self.fallback_model
                            payload["model"] = self.model
                            logger.warning(
                                f"⚠️  API error 400: reasoning_content mismatch with '{old_model}'. "
                                f"Auto-falling back to '{self.fallback_model}' (non-thinking mode)."
                            )
                            attempt = 0  # Reset retry counter for fresh start
                            last_error = None
                            continue  # Retry immediately with fallback model

                        raise RuntimeError(error_msg)

                    remaining_content = ""
                    if stream:
                        try:
                            async for line in response.aiter_lines():
                                if not line.startswith("data: "):
                                    continue
                                data_str = line[6:].strip()
                                if not data_str or data_str == "[DONE]":
                                    continue
                                try:
                                    chunk = json.loads(data_str)
                                    remaining_content += self._extract_content(chunk)
                                    yield chunk
                                except json.JSONDecodeError:
                                    continue
                        except (httpx.StreamError, httpx.RemoteProtocolError, TypeError) as e:
                            # Connection interrupted mid-stream (or internal type error in Python 3.14):
                            # remember what we got and retry.  On the final attempt we yield
                            # the error as a synthetic chunk so the agent can decide how to
                            # proceed instead of crashing.
                            last_error = e
                            log_error_details(
                                logger,
                                f"Stream interrupted (attempt {attempt}/{self.max_retries})",
                                exc_info=False,
                                extra={
                                    "error_type": type(e).__name__,
                                    "error": str(e),
                                    "attempt": attempt,
                                    "remaining_content_length": len(remaining_content),
                                },
                            )
                            if attempt >= self.max_retries:
                                yield self._error_chunk(
                                    f"Stream interrupted after receiving partial content.\n"
                                    f"Partial content so far:\n{remaining_content}\n\n"
                                    f"Error: {type(e).__name__}: {e}\n\n"
                                    f"Please summarize the partial content and ask the user "
                                    f"if they want to retry or continue."
                                )
                                return
                            # Otherwise: retry
                            continue
                    else:
                        body = await response.aread()
                        yield json.loads(body)
                    return  # Successful completion
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
            except RuntimeError as e:
                error_str = str(e)
                log_error_details(
                    logger,
                    f"API request failed",
                    exc_info=False,
                    extra={
                        "error": error_str,
                    },
                )
                yield self._error_chunk(
                    f"API request failed.\n"
                    f"Error: {type(e).__name__}: {error_str}\n"
                    f"Please check API endpoint, model, and network settings."
                )
                return
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
        # Should not reach here, but guard just in case
        if last_error:
            yield self._error_chunk(str(last_error))

    @staticmethod
    def _extract_content(chunk: dict) -> str:
        """Best-effort extract text content from a chunk."""
        choices = chunk.get("choices", [])
        if not choices:
            return ""
        delta = choices[0].get("delta", {})
        return delta.get("content") or ""

    @staticmethod
    def _extract_reasoning(chunk: dict) -> str:
        """Extract reasoning/thinking content from a chunk if present.

        Different models use different field names:
        - DeepSeek R1: delta.reasoning
        - Qwen: delta.reasoning_content
        - Some models: delta.thinking
        """
        choices = chunk.get("choices", [])
        if not choices:
            return ""
        delta = choices[0].get("delta", {})
        reasoning = (
            delta.get("reasoning")
            or delta.get("reasoning_content")
            or delta.get("thinking")
            or ""
        )
        return reasoning if isinstance(reasoning, str) else ""

    @staticmethod
    def _error_chunk(content: str) -> dict:
        """Build a synthetic assistant chunk that carries an error message."""
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

    async def chat_completion_sync(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> dict:
        """Non-streaming completion. Returns the final response dict."""
        # ── Sanitize messages: ensure assistant messages always have content or tool_calls ──
        sanitized = []
        for msg in messages:
            if msg.get("role") == "assistant":
                has_content = bool(msg.get("content"))
                has_tool_calls = bool(msg.get("tool_calls"))
                if not has_content and not has_tool_calls:
                    msg = {**msg, "content": ""}
            sanitized.append(msg)

        payload = {
            "model": self.model,
            "messages": sanitized,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()

        response = await self._client.post(url, json=payload, headers=headers)
        if response.status_code != 200:
            error_msg = f"API error {response.status_code}: {response.text}"

            # ── Auto-fallback: reasoning_content error → switch to non-thinking model ──
            if "reasoning_content" in error_msg and not self._has_fallback:
                self._has_fallback = True
                old_model = self.model
                self.model = self.fallback_model
                logger.warning(
                    f"⚠️  API error 400: reasoning_content mismatch with '{old_model}'. "
                    f"Auto-falling back to '{self.fallback_model}' (non-thinking mode)."
                )
                # Retry once with fallback model
                payload["model"] = self.model
                response = await self._client.post(url, json=payload, headers=headers)
                if response.status_code == 200:
                    return response.json()

            log_error_details(
                logger,
                error_msg,
                exc_info=False,
                extra={
                    "model": self.model,
                    "url": url,
                    "status_code": response.status_code,
                },
            )
            raise RuntimeError(error_msg)
        return response.json()

    async def close(self):
        await self._client.aclose()
