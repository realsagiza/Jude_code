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
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
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

        async with self._client.stream(
            "POST", url, json=payload, headers=headers
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise RuntimeError(
                    f"API error {response.status_code}: {error_body.decode()}"
                )

            if stream:
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data_str)
                        yield chunk
                    except json.JSONDecodeError:
                        continue
            else:
                body = await response.aread()
                yield json.loads(body)

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
