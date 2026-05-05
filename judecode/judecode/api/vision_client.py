"""
Vision API Client — (DEPRECATED)

Vision/screenshot analysis is now handled directly by utils/computer_tools.py.
That module has its own synchronous _analyze_screenshot_sync() function using urllib.

This file is kept as a placeholder only. All imports should use computer_tools.screenshot() directly.

VISION:  ใช้ Ollama local (Qwen) สำหรับดูรูปภาพเท่านั้น
MAIN:   ใช้ DeepSeek API โดยตรง (ไม่ผ่าน Ollama)

หมายเหตุ: DeepSeek Cloud API ยังไม่รองรับ vision/multimodal mode
ดังนั้น vision ต้องพึ่ง Ollama (Qwen) ต่อไปจนกว่า DeepSeek จะเพิ่ม vision support
"""

from judecode.utils.computer_tools import (
    screenshot,
    get_screen_size,
    get_mouse_position,
    get_active_window_info,
)

__all__ = ["screenshot", "get_screen_size", "get_mouse_position", "get_active_window_info"]
