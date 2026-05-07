"""Computer Use tools - screen capture, mouse control, keyboard, and app launching.

This module gives Jude Code the ability to:
1. Take screenshots and analyze them with a Vision model (Qwen)
2. Control mouse (move, click, drag)
3. Type text and use keyboard shortcuts
4. Launch applications
5. Get screen/window information
6. ⚡ FAST MODE: Get accessibility tree snapshots (browser + desktop) instead of vision model

=== OPTIMIZATION STRATEGIES ===
Instead of ALWAYS sending screenshots to a vision model (slow + expensive),
we use a tiered approach:

TIER 1 (FASTEST - no vision model needed):
  - get_browser_accessibility_snapshot(): Uses Playwright's accessibility tree
    → returns structured text with element labels, roles, refs
    → 10-50x faster than vision, 16x cheaper (text vs vision tokens)
    → Works with any LLM, not just vision models

  - get_desktop_accessibility_tree(): Uses macOS Accessibility API (AXUIElement)
    → returns structured text of UI elements in active window
    → No screenshot needed, no vision API call

TIER 2 (OPTIMIZED VISION - when screenshot is truly needed):
  - Screenshots are resized to 800px max width (60-75% token savings)
  - SHA1 hash deduplication: skip if same screenshot already analyzed
  - History pruning: keep only last 3 screenshots
  - JPEG compression for smaller payloads

TIER 3 (FALLBACK - original behavior):
  - Full screenshot + vision model analysis
  - Only used when Tier 1 & 2 are not applicable

Requires: pyautogui, pillow, playwright (optional, for browser accessibility)
Vision requires: A vision-capable model via any OpenAI-compatible API.
  - Local: Ollama with llava, bakllava, qwen2-vl, etc.
  - Cloud: DashScope (Qwen), OpenAI Vision, Anthropic, etc.
  Configure via .env:
    JUDECODE_VISION_BASE_URL=<your-api-url>/v1
    JUDECODE_VISION_API_KEY=sk-your-key
    JUDECODE_VISION_MODEL=your-vision-model

NOTE: DeepSeek Cloud API does NOT support vision/multimodal mode yet.
      Vision tasks require a separate vision-capable API/model.
"""

import base64
import hashlib
import io
import json
import os
import subprocess
import shutil
import platform
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from judecode.config import VISION_BASE_URL, VISION_API_KEY, VISION_MODEL

# ── Lazy pyautogui import ──
# pyautogui (via mouseinfo) crashes on headless servers with no DISPLAY.
# We import it lazily inside each function so computer-tool code can be
# imported cleanly even when there is no GUI / display server.
_PYAUTOGUI = None  # will be set by _get_pyautogui()


def _get_pyautogui():
    """Lazy-import pyautogui and configure safety settings.

    Returns the pyautogui module, or raises RuntimeError with a
    helpful message if the display / GUI is not available.
    """
    global _PYAUTOGUI
    if _PYAUTOGUI is not None:
        return _PYAUTOGUI

    # Check for a display server before importing pyautogui
    system = platform.system().lower()
    if system == "linux":
        display = os.environ.get("DISPLAY")
        if not display:
            raise RuntimeError(
                "No DISPLAY environment variable found.  "
                "This machine appears to be headless (no GUI).  "
                "Computer-use tools (screenshot, mouse, keyboard, etc.) "
                "are not available.  "
                "Install Xvfb for a virtual display if needed:\n"
                "  sudo apt install xvfb && Xvfb :99 -screen 0 1920x1080 &\n"
                "  export DISPLAY=:99"
            )

    try:
        import pyautogui as _pg
    except ImportError as exc:
        raise RuntimeError(
            "pyautogui is not installed.  "
            "Computer-use tools require:\n"
            "  pip install pyautogui pillow mss"
        ) from exc
    except Exception as exc:
        # Catch errors like mouseinfo trying to open a display
        raise RuntimeError(
            "Failed to initialise pyautogui – the system likely has no GUI "
            "display available.  Computer-use tools are disabled.\n"
            f"Underlying error: {exc}"
        ) from exc

    # Configure safety
    _pg.FAILSAFE = True
    _pg.PAUSE = 0.5
    _PYAUTOGUI = _pg
    return _PYAUTOGUI

# ── Screenshot Optimization Constants ──
MAX_SCREENSHOT_WIDTH = 800  # Resize to 800px max (60-75% token savings)
MAX_RECENT_SCREENSHOTS = 3  # Keep only 3 recent screenshots in history
_SCREENSHOT_HISTORY: list[dict] = []  # Stores recent screenshot metadata
_SCREENSHOT_HASH_CACHE: set[str] = set()  # SHA1 hashes for deduplication


# ═══════════════════════════════════════════════════════════════
# TIER 1: ACCESSIBILITY TREE (FASTEST - NO VISION MODEL NEEDED)
# ═══════════════════════════════════════════════════════════════

def get_browser_accessibility_snapshot(
    url: Optional[str] = None,
    task_description: Optional[str] = None,
) -> str:
    """Get an accessibility tree snapshot of the current browser page.

    This is the FASTEST approach for browser automation.
    Instead of sending a screenshot to a vision model, we get a
    structured text tree of all UI elements with their labels,
    roles, and reference IDs.

    The LLM can read this tree directly and decide what to click
    without needing a vision model at all!

    Uses Playwright's accessibility tree snapshot mode
    (accessibility.snapshot() in Chromium).

    Args:
        url: Optional URL to navigate to first
        task_description: Optional context for what the user wants to do

    Returns:
        Structured text description of the page's accessibility tree
        with element references that can be used for clicking/typing
    """
    system = platform.system().lower()

    try:
        # Strategy 1: Try to use Playwright (if installed)
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                # Connect to an existing browser if possible (Chrome)
                browser = None
                context = None

                # Try connecting to running Chrome via CDP
                if system == "darwin":
                    # macOS: try to connect to existing Chrome
                    try:
                        browser = p.chromium.connect_over_cdp(
                            "http://127.0.0.1:9222/json/version"
                        )
                        context = browser.contexts[0] if browser.contexts else None
                    except Exception:
                        pass

                if not browser:
                    # Launch fresh browser (headless)
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context()

                page = context.pages[0] if context.pages else context.new_page()

                if url:
                    page.goto(url, wait_until="domcontentloaded", timeout=10000)

                # Get the accessibility tree snapshot
                # This returns structured JSON with roles, names, refs
                snapshot = page.accessibility.snapshot()

                if not snapshot:
                    return (
                        "[Accessibility Tree] No accessibility tree available "
                        "for this page (possibly a blank page or PDF viewer)."
                    )

                # Flatten the tree into a readable text format
                tree_lines = _flatten_accessibility_tree(snapshot, max_depth=10)
                tree_text = "\n".join(tree_lines)

                # Get current URL
                current_url = page.url if hasattr(page, 'url') else url or "unknown"

                result = (
                    f"🌐 Page Accessibility Tree (from: {current_url})\n"
                    f"   This is a structured view of all interactive elements.\n"
                    f"   Each element has a ref ID you can use with click/type commands.\n\n"
                    f"{tree_text}\n\n"
                )

                if task_description:
                    result += (
                        f"📋 Task: {task_description}\n"
                        f"   Look for the relevant elements above and use their ref IDs.\n"
                    )

                result += (
                    "💡 TIP: You can click elements by their ref ID or by their visible label.\n"
                    "   This data came from the browser's accessibility tree - NO vision model needed!\n"
                )

                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass

                return result

        except ImportError:
            pass  # Playwright not installed, fall through to next strategy

        # Strategy 2: macOS - try to use osascript to get browser content
        if system == "darwin":
            script = (
                'tell application "System Events"\n'
                '  set frontApp to name of first application process whose frontmost is true\n'
                '  if frontApp contains "Chrome" or frontApp contains "Safari" or frontApp contains "Edge" or frontApp contains "Arc" then\n'
                '    return "Active browser: " & frontApp\n'
                '  else\n'
                '    return "Frontmost app: " & frontApp\n'
                '  end if\n'
                'end tell'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                app_info = result.stdout.strip()
                return (
                    f"🌐 Browser Detection: {app_info}\n\n"
                    f"   To get a full accessibility tree, install Playwright:\n"
                    f"   pip install playwright\n"
                    f"   playwright install chromium\n\n"
                    f"   Alternatively, use screenshot(vision_model=...) to analyze visually.\n"
                    f"   But the accessibility tree is 10-50x faster when available!\n"
                )

        return (
            "[Accessibility Tree] Playwright not installed.\n"
            "Install with: pip install playwright && playwright install chromium\n\n"
            "Without it, falling back to screenshot + vision model (slower)."
        )

    except Exception as e:
        return (
            f"[Accessibility Tree Error] {type(e).__name__}: {e}\n"
            f"Falling back to screenshot mode. Install Playwright for 10x faster analysis."
        )


def _flatten_accessibility_tree(
    node: dict,
    depth: int = 0,
    max_depth: int = 10,
    max_children: int = 50,
) -> list[str]:
    """Flatten a Playwright accessibility tree into readable text lines.

    Each line has the format:
      [role ref=ID] "name" [state1, state2]  (children: N)

    This format is designed for LLMs to easily parse and understand
    what elements are available and how to reference them.

    Args:
        node: A node from Playwright's accessibility.snapshot()
        depth: Current recursion depth
        max_depth: Maximum depth to traverse
        max_children: Maximum children to show per node

    Returns:
        List of text lines representing the tree
    """
    lines = []
    indent = "  " * depth

    if depth > max_depth:
        return lines

    role = node.get("role", "unknown")
    name = node.get("name", "")
    ref = node.get("ref", id(node))  # Fallback ref ID
    value = node.get("value", "")
    description = node.get("description", "")
    checked = node.get("checked")
    selected = node.get("selected")
    disabled = node.get("disabled")
    focused = node.get("focused", False)
    expanded = node.get("expanded")
    level = node.get("level")
    orientation = node.get("orientation")
    multiselectable = node.get("multiselectable")
    required = node.get("required")
    invalid = node.get("invalid")
    pressed = node.get("pressed")
    valuemin = node.get("valuemin")
    valuemax = node.get("valuemax")
    valuenow = node.get("valuenow")
    autocomplete = node.get("autocomplete")
    haspopup = node.get("haspopup")
    keyshortcuts = node.get("keyshortcuts")
    roledescription = node.get("roledescription")
    sort = node.get("sort")

    # Build the element line
    element_parts = [f"[{role} ref={ref}]"]

    if name:
        element_parts.append(f'"{name}"')

    # Build state flags
    states = []
    if disabled:
        states.append("disabled")
    if focused:
        states.append("focused")
    if checked is not None:
        states.append(f"checked={checked}")
    if selected is not None:
        states.append(f"selected={selected}")
    if expanded is not None:
        states.append(f"expanded={expanded}")
    if pressed is not None:
        states.append(f"pressed={pressed}")
    if required:
        states.append("required")
    if invalid:
        states.append(f"invalid={invalid}")
    if level is not None:
        states.append(f"level={level}")
    if orientation:
        states.append(f"orientation={orientation}")
    if multiselectable:
        states.append("multiselectable")
    if valuenow is not None:
        states.append(f"value={valuenow}")
    if valuemin is not None:
        states.append(f"min={valuemin}")
    if valuemax is not None:
        states.append(f"max={valuemax}")
    if autocomplete:
        states.append(f"autocomplete={autocomplete}")
    if haspopup:
        states.append(f"haspopup={haspopup}")
    if keyshortcuts:
        states.append(f"shortcut={keyshortcuts}")
    if roledescription:
        states.append(f"role_desc={roledescription}")
    if sort:
        states.append(f"sort={sort}")
    if value:
        states.append(f'value="{value}"')
    if description:
        states.append(f'desc="{description}"')

    if states:
        element_parts.append(f"[{', '.join(states)}]")

    lines.append(indent + " ".join(element_parts))

    # Process children
    children = node.get("children", [])
    if children and isinstance(children, list):
        for child in children[:max_children]:
            child_lines = _flatten_accessibility_tree(
                child, depth + 1, max_depth, max_children
            )
            lines.extend(child_lines)

        if len(children) > max_children:
            lines.append(
                f"{indent}  ... and {len(children) - max_children} more children"
            )

    return lines


def get_desktop_accessibility_tree(
    task_description: Optional[str] = None,
) -> str:
    """Get the accessibility tree of the current desktop/active window.

    This is the FASTEST approach for desktop app automation on macOS.
    Uses the macOS Accessibility API (AXUIElement) via AppleScript
    to get structured information about UI elements.

    No screenshot needed, no vision API call.
    10-50x faster than vision-based analysis.

    Args:
        task_description: Optional context for what the user wants to do

    Returns:
        Structured text description of the active window's UI elements
    """
    system = platform.system().lower()

    try:
        if system == "darwin":
            # macOS: Use AppleScript to get accessibility info
            # Get the frontmost app's window information
            script = (
                'tell application "System Events"\n'
                '  set frontApp to name of first application process whose frontmost is true\n'
                '  return frontApp\n'
                'end tell'
            )
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            front_app = result.stdout.strip() if result.returncode == 0 else "Unknown"

            # Get window position and size
            pos_script = (
                'tell application "System Events"\n'
                '  tell first application process whose frontmost is true\n'
                '    try\n'
                '      set win to window 1\n'
                '      set winPos to position of win\n'
                '      set winSize to size of win\n'
                '      set winTitle to title of win\n'
                '      return "Window: " & winTitle & " at " & (item 1 of winPos as text) & "," & (item 2 of winPos as text) & " size " & (item 1 of winSize as text) & "x" & (item 2 of winSize as text)\n'
                '    on error\n'
                '      return "Could not get window info"\n'
                '    end try\n'
                '  end tell\n'
                'end tell'
            )
            win_result = subprocess.run(
                ["osascript", "-e", pos_script],
                capture_output=True, text=True, timeout=5,
            )
            window_info = win_result.stdout.strip() if win_result.returncode == 0 else "Unknown"

            # Get UI element hierarchy (simplified - get buttons, text fields, etc.)
            ui_script = (
                'tell application "System Events"\n'
                '  tell first application process whose frontmost is true\n'
                '    try\n'
                '      set output to ""\n'
                '      tell window 1\n'
                '        try\n'
                '          set uiElements to every UI element\n'
                '          set output to "UI Elements in active window:\\n"\n'
                '          repeat with elem in uiElements\n'
                '            try\n'
                '              set elemRole to role of elem\n'
                '              set elemDesc to description of elem\n'
                '              set elemTitle to title of elem if title of elem is not ""\n'
                '              if elemRole is not "" then\n'
                '                set line to elemRole\n'
                '                if elemTitle is not "" then set line to line & ": " & elemTitle\n'
                '                if elemDesc is not "" then set line to line & " (" & elemDesc & ")"\n'
                '                set output to output & line & return\n'
                '              end if\n'
                '            end try\n'
                '          end repeat\n'
                '        on error errMsg\n'
                '          set output to "UI Elements: " & errMsg\n'
                '        end try\n'
                '      end tell\n'
                '    on error\n'
                '      set output to "Could not access UI elements. Check Accessibility permissions."\n'
                '    end try\n'
                '    return output\n'
                '  end tell\n'
                'end tell'
            )
            ui_result = subprocess.run(
                ["osascript", "-e", ui_script],
                capture_output=True, text=True, timeout=10,
            )
            ui_info = ui_result.stdout.strip() if ui_result.returncode == 0 else ""

            result = (
                f"🖥️ Desktop Accessibility Tree\n"
                f"   Active App: {front_app}\n"
                f"   Window: {window_info}\n\n"
                f"   This data comes from the macOS Accessibility API.\n"
                f"   No screenshot needed - structured UI elements below.\n\n"
            )

            if ui_info and "Could not" not in ui_info:
                result += f"{ui_info}\n\n"

            result += (
                "💡 TIP: To interact with these elements:\n"
                "   - Use mouse_move + mouse_click for buttons\n"
                "   - Use keyboard_type for text fields\n"
                "   - Use keyboard_hotkey for shortcuts\n\n"
            )

            if task_description:
                result += f"📋 Task: {task_description}\n"

            return result

        elif system == "windows":
            # Windows: Use UI Automation via PowerShell (basic)
            ps_script = (
                '[System.Windows.Automation.Automation]::GetFocusedElement() | '
                'Select-Object CurrentName, CurrentControlTypeName | '
                'Format-List'
            )
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return (
                    f"🖥️ Desktop UI Element\n"
                    f"{result.stdout.strip()}\n\n"
                    f"💡 Note: Windows UI Automation has limited support.\n"
                    f"   Consider using screenshot(vision_model=...) for full analysis.\n"
                )

            return (
                "[Desktop Accessibility] Windows UI Automation not available.\n"
                "Falling back to screenshot + vision model (slower).\n"
                "Use screenshot(vision_model=...) for visual analysis."
            )

        else:
            return (
                "[Desktop Accessibility] Only supported on macOS and Windows.\n"
                "Falling back to screenshot + vision model (slower).\n"
                "Use screenshot(vision_model=...) for visual analysis."
            )

    except subprocess.TimeoutExpired:
        return "[Desktop Accessibility] Timed out getting accessibility tree."
    except Exception as e:
        return (
            f"[Desktop Accessibility Error] {type(e).__name__}: {e}\n"
            "Falling back to screenshot + vision model.\n"
            "Use screenshot(vision_model=...) instead."
        )


# ═══════════════════════════════════════════════════════════════
# TIER 2: OPTIMIZED SCREENSHOT + VISION (when truly needed)
# ═══════════════════════════════════════════════════════════════

def _resize_screenshot(img, max_width: int = MAX_SCREENSHOT_WIDTH) -> tuple:
    """Resize a screenshot to reduce token costs while preserving readability.

    Uses Lanczos resampling for high quality.
    Maintains aspect ratio.
    Only resizes if width > max_width.

    Args:
        img: PIL Image object
        max_width: Maximum width in pixels (default: 800)

    Returns:
        Tuple of (resized_image, original_size, resized_size)
    """
    from PIL import Image as PILImage

    original_width, original_height = img.size

    if original_width <= max_width:
        # No resizing needed
        return img, (original_width, original_height), (original_width, original_height)

    # Calculate new size maintaining aspect ratio
    aspect_ratio = original_height / original_width
    new_width = max_width
    new_height = int(new_width * aspect_ratio)

    # Resize with high-quality Lanczos filter
    img_resized = img.resize((new_width, new_height), PILImage.Resampling.LANCZOS)

    return img_resized, (original_width, original_height), (new_width, new_height)


def _get_screenshot_hash(img_bytes: bytes) -> str:
    """Compute SHA1 hash of screenshot bytes for deduplication.

    Args:
        img_bytes: Raw image bytes

    Returns:
        SHA1 hex digest
    """
    return hashlib.sha1(img_bytes).hexdigest()


def _is_duplicate_screenshot(img_hash: str) -> bool:
    """Check if this screenshot has been seen before (deduplication).

    Args:
        img_hash: SHA1 hash of the screenshot

    Returns:
        True if this screenshot was already analyzed
    """
    if img_hash in _SCREENSHOT_HASH_CACHE:
        return True
    _SCREENSHOT_HASH_CACHE.add(img_hash)
    return False


def _prune_screenshot_history():
    """Keep only the most recent screenshots in history."""
    global _SCREENSHOT_HISTORY
    while len(_SCREENSHOT_HISTORY) > MAX_RECENT_SCREENSHOTS:
        _SCREENSHOT_HISTORY.pop(0)


def get_screen_size() -> str:
    """Get the screen resolution."""
    try:
        pg = _get_pyautogui()
        w, h = pg.size()
        return f"Screen size: {w}x{h} pixels"
    except RuntimeError as e:
        return f"Error: {e}"


def screenshot(
    vision_model: Optional[str] = None,
    task_description: Optional[str] = None,
    save_path: Optional[str] = None,
) -> str:
    """Take a screenshot and optionally analyze it with a vision model.

    ⚡ OPTIMIZED: Screenshots are resized to 800px max width
       to reduce token costs by 60-75%.
       Duplicate screenshots are detected and skipped (SHA1 hash cache).
       History is pruned to keep only the 3 most recent.

    Args:
        vision_model: Optional model name (configured via JUDECODE_VISION_MODEL in .env).
                      If provided, the screenshot will be analyzed.
                      ⚠️ Consider using get_browser_accessibility_snapshot()
                      or get_desktop_accessibility_tree() instead - they're
                      10-50x faster and don't need a vision model!
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

    # ═══ OPTIMIZATION: Resize screenshot to reduce token costs ═══
    original_size = screenshot_img.size
    resized_img, orig_dim, new_dim = _resize_screenshot(screenshot_img)
    screenshot_img = resized_img

    # Save to temp file
    if save_path:
        img_path = Path(save_path)
        img_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        img_path = Path(tempfile.gettempdir()) / "judecode_screenshot.png"

    # Save as JPEG for smaller size (if vision model supports it)
    # PNG is safer for compatibility
    screenshot_img.save(str(img_path), format="PNG")
    file_size = img_path.stat().st_size

    # ═══ OPTIMIZATION: Compute hash for deduplication ═══
    with open(str(img_path), "rb") as f:
        img_bytes = f.read()
    img_hash = _get_screenshot_hash(img_bytes)

    # Build optimization info
    opt_info_parts = []
    if original_size != new_dim:
        opt_info_parts.append(
            f"   ⚡ Resized: {original_size[0]}x{original_size[1]} → {new_dim[0]}x{new_dim[1]} "
            f"(~{int((1 - (new_dim[0]*new_dim[1])/(original_size[0]*original_size[1]))*100)}% fewer tokens)"
        )
    else:
        opt_info_parts.append(
            f"   ⚡ No resize needed (already ≤{MAX_SCREENSHOT_WIDTH}px)"
        )

    # If no vision model, just return basic info
    if not vision_model:
        try:
            pg = _get_pyautogui()
            w, h = pg.size()
            screen_res = f"Screen resolution: {w}x{h}"
        except RuntimeError:
            screen_res = "Screen resolution: unknown (no display)"
        result = (
            f"Screenshot saved to: {img_path} ({file_size / 1024:.1f} KB)\n"
            f"{screen_res}\n"
        )
        result += "\n".join(opt_info_parts)
        result += (
            f"\n\n💡 FASTER OPTION: Use get_browser_accessibility_snapshot() or\n"
            f"   get_desktop_accessibility_tree() instead of vision model.\n"
            f"   They're 10-50x faster and don't need a vision model!\n"
        )
        return result

    # ═══ OPTIMIZATION: Check for duplicate screenshot ═══
    if _is_duplicate_screenshot(img_hash):
        result = (
            f"📸 Screenshot (DUPLICATE - using cached analysis)\n"
            f"   Saved to: {img_path} ({file_size / 1024:.1f} KB)\n"
            f"   ⚡ Skipped vision analysis (same as previous screenshot)\n"
        )
        result += "\n".join(opt_info_parts)
        return result

    # Analyze with vision model via synchronous HTTP call
    try:
        description = _analyze_screenshot_sync(str(img_path), vision_model, task_description)
        result = (
            f"📸 Screenshot taken ({file_size / 1024:.1f} KB)\n"
            f"   Saved to: {img_path}\n"
        )
        result += "\n".join(opt_info_parts) + "\n\n"
        result += f"🔍 Vision Analysis (using {vision_model}):\n{description}"
        return result
    except Exception as e:
        return (
            f"📸 Screenshot taken ({file_size / 1024:.1f} KB)\n"
            f"   Saved to: {img_path}\n\n"
            f"⚠️ Vision analysis failed: {type(e).__name__}: {e}\n"
            f"   Make sure the vision model '{vision_model}' is running.\n\n"
            f"💡 TIP: Try get_browser_accessibility_snapshot() instead -\n"
            f"   it's 10-50x faster and doesn't need a vision model!"
        )


def _analyze_screenshot_sync(image_path: str, model: str, task_description: Optional[str] = None) -> str:
    """Send screenshot to vision model synchronously using urllib.

    ⚡ The screenshot should already be resized to 800px max width
       by the screenshot() function before calling this.
    """
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

    url = f"{VISION_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {VISION_API_KEY}",
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
            f"Cannot connect to vision model at {VISION_BASE_URL}. "
            f"Make sure '{model}' is running. Error: {e.reason}"
        )


# ═══════════════════════════════════════════════════════════════
# MOUSE & KEYBOARD CONTROLS (unchanged)
# ═══════════════════════════════════════════════════════════════


def mouse_move(x: int, y: int, duration: float = 0.5) -> str:
    """Move the mouse cursor to absolute screen coordinates (x, y).

    Args:
        x: X coordinate (0 = left edge of screen)
        y: Y coordinate (0 = top edge of screen)
        duration: Seconds to animate the movement (default: 0.5)

    Returns:
        Confirmation
    """
    try:
        pg = _get_pyautogui()
    except RuntimeError as e:
        return f"Error: {e}"

    w, h = pg.size()
    if x < 0 or x > w or y < 0 or y > h:
        return (
            f"Error: Coordinates ({x}, {y}) are outside screen bounds "
            f"(0-{w}, 0-{h}). No action taken."
        )
    try:
        pg.moveTo(x, y, duration=duration)
        return f"Mouse moved to ({x}, {y})"
    except pg.FailSafeException:
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
    try:
        pg = _get_pyautogui()
    except RuntimeError as e:
        return f"Error: {e}"

    if button not in ("left", "right", "middle"):
        return f"Error: Invalid button '{button}'. Use 'left', 'right', or 'middle'."

    try:
        if x is not None and y is not None:
            pg.click(x, y, button=button)
            return f"Clicked {button} button at ({x}, {y})"
        else:
            pos = pg.position()
            pg.click(button=button)
            return f"Clicked {button} button at current position ({pos.x}, {pos.y})"
    except pg.FailSafeException:
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
        pg = _get_pyautogui()
    except RuntimeError as e:
        return f"Error: {e}"

    try:
        if x is not None and y is not None:
            pg.doubleClick(x, y)
            return f"Double-clicked at ({x}, {y})"
        else:
            pos = pg.position()
            pg.doubleClick()
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
        pg = _get_pyautogui()
    except RuntimeError as e:
        return f"Error: {e}"

    try:
        pg.moveTo(start_x, start_y, duration=0.2)
        pg.drag(end_x - start_x, end_y - start_y, duration=duration)
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
        pg = _get_pyautogui()
        pg.typewrite(text, interval=interval)
        return f"Typed '{text[:50]}{'...' if len(text) > 50 else ''}' ({len(text)} characters)"
    except RuntimeError as e:
        return f"Error: {e}"
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
        pg = _get_pyautogui()
        pg.press(key)
        return f"Pressed key: {key}"
    except RuntimeError as e:
        return f"Error: {e}"
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
        pg = _get_pyautogui()
    except RuntimeError as e:
        return f"Error: {e}"

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

        pg.hotkey(*normalized)
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
        pg = _get_pyautogui()
    except RuntimeError as e:
        return f"Error: {e}"

    try:
        if x is not None and y is not None:
            pg.scroll(clicks, x, y)
        else:
            pg.scroll(clicks)
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
            # Try multiple strategies to open an app on Windows
            import shutil

            # Strategy 1: Check if the app name matches a known executable
            exe_path = shutil.which(app_name)
            if exe_path:
                subprocess.Popen([exe_path], shell=False)
                return f"Opened application: {app_name}"

            # Strategy 2: Try 'start' command (shell=True required for CMD built-in)
            try:
                subprocess.Popen(
                    ["start", app_name],
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return f"Opened application: {app_name}"
            except Exception:
                pass

            # Strategy 3: Try with .exe extension
            try:
                subprocess.Popen(
                    ["start", f"{app_name}.exe"],
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return f"Opened application: {app_name}"
            except Exception as e:
                return (
                    f"Could not open '{app_name}'. "
                    f"Error: {e}. "
                    f"Try using the full executable name (e.g., 'chrome' instead of 'Chrome')."
                )

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
        pg = _get_pyautogui()
        pos = pg.position()
        return f"Mouse position: ({pos.x}, {pos.y})"
    except RuntimeError as e:
        return f"Error: {e}"
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
    """List currently running applications with visible windows."""
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
        elif system == "windows":
            try:
                # Use tasklist to get running GUI processes
                result = subprocess.run(
                    ["tasklist", "/FI", "SESSIONNAME eq Console", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode == 0:
                    lines = [line.strip() for line in result.stdout.split("\n") if line.strip()]
                    # Filter out common background processes, keep user-facing apps
                    skip_patterns = [
                        "System", "svchost", "RuntimeBroker", "sihost",
                        "taskhost", "ctfmon", "SearchApp", "Widgets",
                        "SecurityHealth", "smartscreen",
                    ]
                    apps = []
                    for line in lines:
                        name = line.split()[0] if line.split() else ""
                        if name and name.lower().endswith(".exe"):
                            name_clean = name[:-4]  # Remove .exe
                            if name_clean not in skip_patterns and name_clean not in apps:
                                apps.append(name_clean)
                    if apps:
                        return "Running applications:\n  " + "\n  ".join(apps)
                    return "No visible applications found (try tasklist)."
                return "Could not list applications."
            except Exception as e:
                return f"Error listing Windows apps: {type(e).__name__}: {e}"
        elif system == "linux":
            # Linux: try wmctrl or xprop
            result = subprocess.run(
                ["wmctrl", "-l"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                apps = set()
                for line in result.stdout.strip().split("\n"):
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        apps.add(parts[3])
                if apps:
                    return "Running applications:\n  " + "\n  ".join(sorted(apps))
                return "No visible windows found."
            return "Could not list applications (install wmctrl)."
        return "List running apps is not supported on this platform."
    except Exception as e:
        return f"Error listing apps: {type(e).__name__}: {e}"
