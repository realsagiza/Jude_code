"""API Client for communicating with Ollama-compatible endpoints."""

import json
from typing import AsyncGenerator, Optional

import httpx

from judecode.config import BASE_URL, API_KEY, MODEL, MAX_TOKENS, TEMPERATURE


class ApiClient:
    """Client for Ollama-compatible chat completion API."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        api_key: str = API_KEY,
        model: str = MODEL,
        max_tokens: int = MAX_TOKENS,
        temperature: float = TEMPERATURE,
        max_retries: int = 2,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))

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
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools

        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                async with self._client.stream(
                    "POST", url, json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        error_body = await response.aread()
                        raise RuntimeError(
                            f"API error {response.status_code}: {error_body.decode()}"
                        )

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
                        except (httpx.StreamError, httpx.RemoteProtocolError) as e:
                            # Connection interrupted mid-stream: remember what we got
                            # and retry.  On the final attempt we yield the error
                            # as a synthetic chunk so the agent can decide how to
                            # proceed instead of crashing.
                            last_error = e
                            if attempt == self.max_retries:
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
            except (httpx.ConnectError, httpx.Timeout) as e:
                last_error = e
                if attempt == self.max_retries:
                    yield self._error_chunk(
                        f"Connection failed after {self.max_retries} attempts.\n"
                        f"Error: {type(e).__name__}: {e}\n"
                        f"Please check your network connection and try again."
                    )
                    return
            except RuntimeError as e:
                yield self._error_chunk(
                    f"API request failed.\n"
                    f"Error: {type(e).__name__}: {e}\n"
                    f"Please check API endpoint, model, and network settings."
                )
                return
            except httpx.HTTPError as e:
                last_error = e
                if attempt == self.max_retries:
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
        payload = {
            "model": self.model,
            "messages": messages,
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
            raise RuntimeError(
                f"API error {response.status_code}: {response.text}"
            )
        return response.json()

    async def close(self):
        await self._client.aclose()
