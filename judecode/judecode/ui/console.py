"""Shared Rich console instance.

Use this console for all Rich output so everything works consistently
across the terminal UI and the agent engine.

Handles Windows console properly by:
1. Enabling VT100 emulation for colors (Windows 10+)
2. Setting UTF-8 encoding for Unicode support
3. Using force_terminal for piped output detection

It also supports a swappable "sink": when the Textual TUI is running it
installs a sink so that every ``console.print(...)`` call is routed into the
TUI output pane instead of stdout. When no sink is installed, output goes to
the real terminal exactly like a normal Rich console (used by --legacy mode).
"""

import os
import platform
import sys
from typing import Any, Callable, Optional


def _enable_windows_vt100() -> None:
    """Enable VT100 escape sequence processing on Windows 10+."""
    if platform.system().lower() != "windows":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle and handle != -1:
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, new_mode)
    except Exception:
        pass


def _fix_windows_encoding() -> None:
    """Set UTF-8 encoding for stdout on Windows to support Unicode."""
    if platform.system().lower() != "windows":
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    except Exception:
        try:
            sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)  # type: ignore
        except Exception:
            pass


_enable_windows_vt100()
_fix_windows_encoding()

from rich.console import Console


# A sink receives the same *args/**kwargs that were passed to console.print().
# It is responsible for rendering them somewhere (e.g. a Textual RichLog).
PrintSink = Callable[..., None]


class SinkConsole(Console):
    """A Rich Console whose ``print`` can be redirected to a sink callback.

    When a sink is installed via :meth:`set_sink`, every ``print`` call is
    forwarded to the sink (the TUI). When no sink is set, it behaves like a
    normal Rich Console and writes to the terminal.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sink: Optional[PrintSink] = None

    def set_sink(self, sink: Optional[PrintSink]) -> None:
        self._sink = sink

    @property
    def has_sink(self) -> bool:
        return self._sink is not None

    def print(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        sink = self._sink
        if sink is not None:
            try:
                sink(*args, **kwargs)
                return
            except Exception:
                # If the sink blows up, fall back to normal printing so we
                # never lose output entirely.
                pass
        super().print(*args, **kwargs)


console = SinkConsole(force_terminal=True, force_interactive=True)


def set_console_sink(sink: Optional[PrintSink]) -> None:
    """Install (or clear) the global console sink used by the TUI."""
    console.set_sink(sink)
