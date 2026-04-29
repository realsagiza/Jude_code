"""Shared Rich console instance.

Use this console for all Rich output so everything works consistently
across the terminal UI and the agent engine.

Handles Windows console properly by:
1. Enabling VT100 emulation for colors (Windows 10+)
2. Setting UTF-8 encoding for Unicode support
3. Using force_terminal for piped output detection
"""

import os
import platform
import sys


def _enable_windows_vt100() -> None:
    """Enable VT100 escape sequence processing on Windows 10+.

    This allows ANSI color codes to work in the legacy Windows console.
    Windows Terminal and modern terminals handle this automatically.
    """
    if platform.system().lower() != "windows":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle and handle != -1:
            # Get current console mode
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                # Add VT100 processing flag
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass  # Silently fail if we can't enable it


def _fix_windows_encoding() -> None:
    """Set UTF-8 encoding for stdout on Windows to support Unicode."""
    if platform.system().lower() != "windows":
        return
    try:
        # Python 3.7+ on Windows 10+ can use UTF-8 mode
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    except Exception:
        try:
            sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)  # type: ignore
        except Exception:
            pass


# Enable Windows console features before creating the Rich console
_enable_windows_vt100()
_fix_windows_encoding()

from rich.console import Console

console = Console(force_terminal=True, force_interactive=True)
