"""Computer Use tools - screen capture, mouse control, keyboard, and app launching.

This module gives Jude Code the ability to:
1. Take screenshots and analyze them with a Vision model (Qwen)
2. Control mouse (move, click, drag)
3. Type text and use keyboard shortcuts
4. Launch applications
5. Get screen/window information

Requires: pyautogui, pillow
Vision requires: Qwen 3.5 or any vision-capable model running in Ollama
"""

import base64
import json
import os
import subprocess
import shutil
import platform
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import pyautogui

from judecode.config import BASE_URL, API_KEY, VISION_MODEL

# Configure PyAutoGUI safety
pyautogui.FAILSAFE = True  # Move mouse to top-left to abort
pyautogui.PAUSE = 0.5      # 0.5 second pause between actions


def get_screen_size() -> str:
    """Get the screen resolution."""
    w, h = pyautogui.size()
    return f"Screen size: {w}x{h} pixels"


def screenshot(
    vision_model: Optional[str] = None,
    task_description: Optional[str] = None,
    save_path: Optional[str] = None,
) -> str:
    """Take a screenshot and optionally analyze it with a vision model.

    Args:
        vision_model: Optional model name (e.g. 'qwen3.5:397b-cloud').
                      If provided, the screenshot will be analyzed.
        task_description: Optional context for the vision model to focus on.
        save_path: Optional path to save the screenshot file.

    Returns:
        If vision_model is provided: Description of the screen contents
        If not: Confirmation that screenshot was taken
    """
    # Take screenshot using mss (cross-platform, fast, no special permissions needed)
    try:
        import mss
        import mss.tools

        with mss.mss() as sct:
            # Get the primary monitor
            monitor = sct.monitors[1]  # 1 = primary monitor
            # Grab the screenshot
            sct_img = sct.grab(monitor)
            # Convert to PIL Image
            from PIL import Image as PILImage
            screenshot_img = PILImage.frombytes(
                "RGB", sct_img.size, sct_img.rgb
            )

    except ImportError as e:
        return f"Error: Missing dependency: {e}. Install: pip install mss pillow"
    except Exception as e:
        return f"Error taking screenshot: {type(e).__name__}: {e}"

    # Save to temp file
    if save_path:
        img_path = Path(save_path)
        img_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        img_path = Path(tempfile.gettempdir()) / "judecode_screenshot.png"

    if isinstance(screenshot_img, Path):
        # Already saved
        pass
    else:
        screenshot_img.save(str(img_path))
    file_size = img_path.stat().st_size

    # If no vision model, just return basic info
    if not vision_model:
        w, h = pyautogui.size()
        return (
            f"Screenshot saved to: {img_path} ({file_size / 1024:.1f} KB)\n"
            f"Screen resolution: {w}x{h}\n"
            f"(No vision model specified - use vision_model parameter to analyze)"
        )

    # Analyze with vision model via synchronous HTTP call
    try:
        description = _analyze_screenshot_sync(str(img_path), vision_model, task_description)
        result = (
            f"📸 Screenshot taken ({file_size / 1024:.1f} KB)\n"
            f"   Saved to: {img_path}\n\n"
            f"🔍 Vision Analysis (using {vision_model}):\n{description}"
        )
        return result
    except Exception as e:
        return (
            f"📸 Screenshot taken ({file_size / 1024:.1f} KB)\n"
            f"   Saved to: {img_path}\n\n"
            f"⚠️ Vision analysis failed: {type(e).__name__}: {e}\n"
            f"   Make sure the vision model '{vision_model}' is running."
        )


def _analyze_screenshot_sync(image_path: str, model: str, task_description: Optional[str] = None) -> str:
    """Send screenshot to vision model synchronously using urllib."""
    # Encode image to base64
    with open(image_path, "rb") as f:
        base64_image = base64.b64encode(f.read()).decode("utf-8")

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

    payload = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        ],
        "max_tokens": 2048,
        "temperature": 0.1,
        "stream": False,
    }).encode("utf-8")

    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
            content = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return content or "(Vision model returned empty response)"
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vision API error {e.code}: {error_body}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Cannot connect to vision model at {BASE_URL}. "
            f"Make sure '{model}' is running. Error: {e.reason}"
        )


def mouse_move(x: int, y: int, duration: float = 0.5) -> str:
    """Move the mouse cursor to absolute screen coordinates (x, y).

    Args:
        x: X coordinate (0 = left edge of screen)
        y: Y coordinate (0 = top edge of screen)
        duration: Seconds to animate the movement (default: 0.5)

    Returns:
        Confirmation
    """
    w, h = pyautogui.size()
    if x < 0 or x > w or y < 0 or y > h:
        return (
            f"Error: Coordinates ({x}, {y}) are outside screen bounds "
            f"(0-{w}, 0-{h}). No action taken."
        )
    try:
        pyautogui.moveTo(x, y, duration=duration)
        return f"Mouse moved to ({x}, {y})"
    except pyautogui.FailSafeException:
        return "Mouse movement aborted by failsafe (mouse at corner)."
    except Exception as e:
        return f"Error moving mouse: {type(e).__name__}: {e}"


def mouse_click(button: str = "left", x: Optional[int] = None, y: Optional[int] = None) -> str:
    """Click the mouse at current position or specified coordinates.

    Args:
        button: 'left', 'right', or 'middle' (default: 'left')
        x: Optional X coordinate to move to before clicking
        y: Optional Y coordinate to move to before clicking

    Returns:
        Confirmation
    """
    if button not in ("left", "right", "middle"):
        return f"Error: Invalid button '{button}'. Use 'left', 'right', or 'middle'."

    try:
        if x is not None and y is not None:
            pyautogui.click(x, y, button=button)
            return f"Clicked {button} button at ({x}, {y})"
        else:
            pos = pyautogui.position()
            pyautogui.click(button=button)
            return f"Clicked {button} button at current position ({pos.x}, {pos.y})"
    except pyautogui.FailSafeException:
        return "Click aborted by failsafe."
    except Exception as e:
        return f"Error clicking: {type(e).__name__}: {e}"


def mouse_double_click(x: Optional[int] = None, y: Optional[int] = None) -> str:
    """Double-click at current position or specified coordinates.

    Args:
        x: Optional X coordinate
        y: Optional Y coordinate

    Returns:
        Confirmation
    """
    try:
        if x is not None and y is not None:
            pyautogui.doubleClick(x, y)
            return f"Double-clicked at ({x}, {y})"
        else:
            pos = pyautogui.position()
            pyautogui.doubleClick()
            return f"Double-clicked at ({pos.x}, {pos.y})"
    except Exception as e:
        return f"Error double-clicking: {type(e).__name__}: {e}"


def mouse_drag(start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.5) -> str:
    """Drag the mouse from one position to another (click and hold).

    Args:
        start_x: Starting X coordinate
        start_y: Starting Y coordinate
        end_x: Ending X coordinate
        end_y: Ending Y coordinate
        duration: Duration of the drag in seconds

    Returns:
        Confirmation
    """
    try:
        pyautogui.moveTo(start_x, start_y, duration=0.2)
        pyautogui.drag(end_x - start_x, end_y - start_y, duration=duration)
        return f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})"
    except Exception as e:
        return f"Error dragging: {type(e).__name__}: {e}"


def keyboard_type(text: str, interval: float = 0.05) -> str:
    """Type text at the current cursor position.

    Args:
        text: The text to type
        interval: Seconds between each key press (default: 0.05)

    Returns:
        Confirmation
    """
    if not text:
        return "No text provided to type."
    try:
        pyautogui.typewrite(text, interval=interval)
        return f"Typed '{text[:50]}{'...' if len(text) > 50 else ''}' ({len(text)} characters)"
    except Exception as e:
        return f"Error typing: {type(e).__name__}: {e}"


def keyboard_press(key: str) -> str:
    """Press a single key (e.g., 'enter', 'tab', 'escape', 'space').

    Args:
        key: Key name (see pyautogui.KEYBOARD_KEYS for full list)

    Returns:
        Confirmation
    """
    try:
        pyautogui.press(key)
        return f"Pressed key: {key}"
    except Exception as e:
        return f"Error pressing key: {type(e).__name__}: {e}"


def keyboard_hotkey(*keys: str) -> str:
    """Press a keyboard shortcut combination.

    Examples:
        keyboard_hotkey('command', 'c') -> Copy (Cmd+C)
        keyboard_hotkey('command', 'v') -> Paste (Cmd+V)
        keyboard_hotkey('alt', 'tab') -> Switch app
        keyboard_hotkey('ctrl', 'shift', 'esc') -> Task Manager

    Args:
        *keys: Key names to press simultaneously

    Returns:
        Confirmation
    """
    if not keys:
        return "No keys provided."
    try:
        # Normalize modifier keys for cross-platform
        system = platform.system().lower()
        normalized = []
        for k in keys:
            kl = k.lower()
            if kl in ("cmd", "command", "win", "windows", "super"):
                normalized.append("command" if system == "darwin" else "win")
            elif kl in ("alt", "option"):
                normalized.append("option" if system == "darwin" else "alt")
            else:
                normalized.append(k)

        pyautogui.hotkey(*normalized)
        keys_str = ", ".join(normalized)
        return f"Pressed hotkey: {keys_str}"
    except Exception as e:
        return f"Error pressing hotkey: {type(e).__name__}: {e}"


def scroll(clicks: int, x: Optional[int] = None, y: Optional[int] = None) -> str:
    """Scroll the mouse wheel.

    Args:
        clicks: Number of scroll clicks. Positive = scroll up, Negative = scroll down.
        x: Optional X position to scroll at
        y: Optional Y position to scroll at

    Returns:
        Confirmation
    """
    try:
        if x is not None and y is not None:
            pyautogui.scroll(clicks, x, y)
        else:
            pyautogui.scroll(clicks)
        direction = "up" if clicks > 0 else "down"
        return f"Scrolled {direction} ({abs(clicks)} clicks)"
    except Exception as e:
        return f"Error scrolling: {type(e).__name__}: {e}"


def open_app(app_name: str) -> str:
    """Open an application by name.

    On macOS: Uses 'open -a'
    On Windows: Uses 'start'
    On Linux: Uses 'xdg-open' or application name

    Args:
        app_name: Application name (e.g., 'Safari', 'Chrome', 'Terminal', 'Finder')

    Returns:
        Confirmation
    """
    system = platform.system().lower()
    try:
        if system == "darwin":  # macOS
            # Check if app exists
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                # Try lowercase, try without .app
                alt_name = app_name
                if not alt_name.endswith(".app"):
                    alt_name = f"{alt_name}.app"
                result = subprocess.run(
                    ["open", "-a", alt_name],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode != 0:
                    return (
                        f"Could not open '{app_name}'. "
                        f"Error: {result.stderr.strip() or 'Application not found'}. "
                        f"Try using the full app name (e.g., 'Google Chrome' instead of 'Chrome')."
                    )
            return f"Opened application: {app_name}"

        elif system == "windows":
            subprocess.run(
                ["start", app_name],
                shell=True,
                timeout=10,
            )
            return f"Opened application: {app_name}"

        elif system == "linux":
            subprocess.run(
                [app_name],
                capture_output=True,
                timeout=10,
            )
            return f"Opened application: {app_name}"

        else:
            return f"Unsupported OS: {system}"

    except subprocess.TimeoutExpired:
        return f"Timed out trying to open '{app_name}'"
    except FileNotFoundError:
        return f"Command not found. Could not open '{app_name}'."
    except Exception as e:
        return f"Error opening app: {type(e).__name__}: {e}"


def get_mouse_position() -> str:
    """Get the current mouse cursor position."""
    try:
        pos = pyautogui.position()
        return f"Mouse position: ({pos.x}, {pos.y})"
    except Exception as e:
        return f"Error getting position: {type(e).__name__}: {e}"


def get_active_window_info() -> str:
    """Get information about the currently active window.

    Returns:
        Window title and position/size if available
    """
    system = platform.system().lower()
    try:
        if system == "darwin":
            # macOS: use AppleScript with shorter timeout
            script = (
                'tell application "System Events" to get {name, position, size} of '
                'first application process whose frontmost is true'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=3,  # shorter timeout
            )
            if result.returncode == 0 and result.stdout.strip():
                return f"Active window: {result.stdout.strip()}"
            return "Could not determine active window info (osascript may need Accessibility permissions in System Settings > Privacy & Security)."
        elif system == "windows":
            try:
                import pygetwindow as gw
                active = gw.getActiveWindow()
                if active:
                    return f"Active window: '{active.title}' at {active.left},{active.top} ({active.width}x{active.height})"
                return "No active window found."
            except ImportError:
                return "pygetwindow not installed. Install with: pip install pygetwindow"
        else:
            # Linux: try xdotool
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return f"Active window: {result.stdout.strip()}"
            return "Could not determine active window."
    except Exception as e:
        return f"Error getting window info: {type(e).__name__}: {e}"


def list_running_apps() -> str:
    """List running applications (macOS only with osascript, else basic)."""
    system = platform.system().lower()
    try:
        if system == "darwin":
            script = (
                'tell application "System Events" to get name of '
                'every application process whose visible is true'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode == 0:
                apps = [a.strip() for a in result.stdout.strip().split(", ") if a.strip()]
                if apps:
                    return "Running applications:\n  " + "\n  ".join(apps)
                return "No visible applications found."
        return "List running apps is only fully supported on macOS."
    except Exception as e:
        return f"Error listing apps: {type(e).__name__}: {e}"
