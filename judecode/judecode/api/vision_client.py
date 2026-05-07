"""
Vision API Client — (DEPRECATED)

Vision/screenshot analysis is now handled directly by utils/computer_tools.py.
That module has its own synchronous _analyze_screenshot_sync() function using urllib.

This file is kept as a placeholder only. All imports should use computer_tools.screenshot() directly.

VISION:  ใช้ Vision API (Ollama/Cloud) ผ่าน computer_tools.screenshot() ตามค่าตั้งค่าใน .env
MAIN:   ใช้ DeepSeek API โดยตรง (ไม่ผ่าน Ollama)

หมายเหตุ: DeepSeek Cloud API ยังไม่รองรับ vision/multimodal mode
ดังนั้น vision ต้องใช้ API อื่นตาม config เช่น DashScope (Qwen), OpenAI Vision, หรือ Ollama local
"""

from judecode.utils.computer_tools import (
    screenshot,
    get_screen_size,
    get_mouse_position,
    get_active_window_info,
)

__all__ = ["screenshot", "get_screen_size", "get_mouse_position", "get_active_window_info"]
