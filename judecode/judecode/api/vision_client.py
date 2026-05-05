"""Vision API Client for analyzing screenshots using a vision-capable model (e.g. Qwen).

VISION: ใช้ Ollama local (Qwen) สำหรับดูรูปภาพเท่านั้น
MAIN:   ใช้ DeepSeek API โดยตรง (ไม่ผ่าน Ollama)

หมายเหตุ: DeepSeek Cloud API ยังไม่รองรับ vision/multimodal mode
ดังนั้น vision ต้องพึ่ง Ollama (Qwen) ต่อไปจนกว่า DeepSeek จะเพิ่ม vision support
"""

import base64
import json
from pathlib import Path
from typing import Optional

import httpx

from judecode.config import VISION_BASE_URL, VISION_API_KEY, VISION_MODEL


class VisionClient:
    """Client for vision-capable models (Qwen 3.5, LLaVA, etc.) via Ollama-compatible API.

    This client runs SEPARATELY from the main model.
    It only handles image analysis - the main model decides what actions to take.

    Vision uses Ollama local (Qwen) - NOT DeepSeek API.
    """

    def __init__(
        self,
        base_url: str = VISION_BASE_URL,
        api_key: str = VISION_API_KEY,
        model: str = VISION_MODEL,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))

    def _build_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _encode_image(image_path: str) -> str:
        """Read an image file and return base64-encoded string."""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    async def describe_screen(
        self,
        image_path: str,
        task_description: Optional[str] = None,
    ) -> str:
        """Send a screenshot to the vision model and get a description of what's on screen.

        Args:
            image_path: Path to the screenshot image
            task_description: Optional context about what the user wants to do

        Returns:
            Text description of the screen contents
        """
        base64_image = self._encode_image(image_path)
        image_data_url = f"data:image/png;base64,{base64_image}"

        prompt = (
            "You are a computer vision assistant. Analyze this screenshot carefully.\n"
            "Describe in detail:\n"
            "1. What application windows are visible\n"
            "2. What buttons, text fields, menus, or UI elements you see\n"
            "3. Their approximate positions (e.g., 'top-left', 'center', 'bottom-right')\n"
            "4. Any text, numbers, or data displayed on screen\n"
        )
        if task_description:
            prompt += (
                f"\nThe user wants to: {task_description}\n"
                "Focus your description on elements relevant to this task.\n"
            )

        prompt += (
            "\nFormat your response as a clear, structured description "
            "that can help an AI agent decide what to click or type."
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url},
                    },
                ],
            }
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.1,
            "stream": False,
        }

        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()

        try:
            async with self._client.stream(
                "POST", url, json=payload, headers=headers
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise RuntimeError(
                        f"Vision API error {response.status_code}: {error_body.decode()}"
                    )

                # Non-streaming: read full response
                body = await response.aread()
                result = json.loads(body)
                content = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                if not content:
                    return "(Vision model returned empty response)"
                return content

        except httpx.ConnectError as e:
            return (
                f"[Vision Error] Cannot connect to vision model at {self.base_url}\n"
                f"Make sure the vision model '{self.model}' is running.\n"
                f"Error: {type(e).__name__}: {e}"
            )
        except Exception as e:
            return (
                f"[Vision Error] {type(e).__name__}: {e}\n"
                f"Vision model '{self.model}' may not be available."
            )

    async def find_element(
        self,
        image_path: str,
        element_description: str,
    ) -> str:
        """Ask the vision model to locate a specific UI element on screen.

        Args:
            image_path: Path to the screenshot
            element_description: What to find (e.g., 'the search bar', 'the submit button')

        Returns:
            Description of where the element is located
        """
        base64_image = self._encode_image(image_path)
        image_data_url = f"data:image/png;base64,{base64_image}"

        prompt = (
            f"Look at this screenshot carefully. Find the following element: '{element_description}'.\n\n"
            "Describe exactly where it is located using one of these formats:\n"
            "- 'At coordinates (X, Y)' if you can estimate pixel positions\n"
            "- 'In the [top-left/center/bottom-right] of the screen, near [landmark]'\n"
            "- 'Not found on screen' if the element is not visible\n\n"
            "Also describe what the element looks like (color, shape, text on it)."
        )

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url},
                    },
                ],
            }
        ]

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.1,
            "stream": False,
        }

        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()

        try:
            async with self._client.stream(
                "POST", url, json=payload, headers=headers
            ) as response:
                body = await response.aread()
                result = json.loads(body)
                content = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                return content or "(Element not found)"
        except Exception as e:
            return f"[Vision Error] {type(e).__name__}: {e}"

    async def close(self):
        await self._client.aclose()
