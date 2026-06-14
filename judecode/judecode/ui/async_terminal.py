"""Async Terminal UI for Jude Code — แยก input/output เป็นคนละ thread.

ใช้ asyncio tasks แยกกัน + ANSI cursor positioning เพื่อให้:
- AI output area (top) = แสดงผล AI, scroll ตามธรรมชาติ
- Status bar (bottom-1) = สถานะ AI + preview คิว, อัพเดท real-time  
- Input line (bottom) = ผู้ใช้พิมพ์, อยู่ด้านล่างเสมอ

ทั้งสองส่วนไม่ปนกัน — AI output ไม่มี prompt แทรก, input อยู่ล่างสุดตลอด
"""

import asyncio
import os
import signal
import sys
import shutil
import threading
from typing import Optional

from rich.text import Text
from rich.align import Align
from rich.rule import Rule
from rich.panel import Panel
from rich.box import HEAVY_EDGE, DOUBLE_EDGE

from judecode.config import (
    SYSTEM_PROMPT, MODEL, BASE_URL, MAX_CONTINUATIONS, PROVIDER,
)
from judecode.api import create_api_client
from judecode.agent.engine import AgentEngine
from judecode.ui.console import console


# ── Safe input wrapper ──────────────────────────────────────────────────────

def safe_input(prompt: str = "") -> str:
    """Wrapper around input() that handles encoding errors gracefully."""
    try:
        return input(prompt)
    except UnicodeDecodeError:
        if prompt:
            sys.stdout.write(prompt)
            sys.stdout.flush()
        try:
            raw = sys.stdin.buffer.readline()
            if not raw:
                return ""
            return raw.decode("utf-8", errors="replace").rstrip("\n")
        except Exception:
            return ""


# ── Terminal helpers ────────────────────────────────────────────────────────

def _term_width() -> int:
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _term_height() -> int:
    try:
        return shutil.get_terminal_size().lines
    except Exception:
        return 24


def _ansi_save_cursor() -> None:
    sys.stdout.write("\033[s")
    sys.stdout.flush()


def _ansi_restore_cursor() -> None:
    sys.stdout.write("\033[u")
    sys.stdout.flush()


def _ansi_move_to(row: int, col: int = 1) -> None:
    sys.stdout.write(f"\033[{row};{col}H")
    sys.stdout.flush()


def _ansi_clear_line() -> None:
    sys.stdout.write("\033[2K")
    sys.stdout.flush()


def _ansi_clear_to_end() -> None:
    sys.stdout.write("\033[0K")
    sys.stdout.flush()


# ── Greeting / Goodbye ──────────────────────────────────────────────────────

def print_greeting() -> None:
    """Print a cool greeting."""
    greeting_lines = [
        "",
        "  ██╗   ██╗██████╗ ███████╗    ██████╗ ██████╗ ██████╗ ███████╗",
        "  ██║   ██║██╔══██╗██╔════╝   ██╔════╝██╔═══██╗██╔══██╗██╔════╝",
        "  ██║   ██║██║  ██║█████╗     ██║     ██║   ██║██║  ██║█████╗  ",
        "  ██║   ██║██║  ██║██╔══╝     ██║     ██║   ██║██║  ██║██╔══╝  ",
        "  ╚██████╔╝██████╔╝███████╗██╗╚██████╗╚██████╔╝██████╔╝███████╗",
        "   ╚═════╝ ╚═════╝ ╚══════╝╚═╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝",
        "",
    ]
    for line in greeting_lines:
        console.print(line, style="bold cyan")

    welcome_text = Text()
    welcome_text.append("Welcome to ", style="white")
    welcome_text.append("Jude Code", style="bold cyan")
    welcome_text.append(" — your terminal AI coding assistant", style="white")
    console.print(Align.center(welcome_text))
    console.print()

    info = Text()
    info.append(f"Provider: ", style="dim")
    info.append(f"{PROVIDER.upper()}\n", style="bold green")
    info.append(f"Model: ", style="dim")
    info.append(f"{MODEL}\n", style="bold cyan")
    info.append(f"API: ", style="dim")
    info.append(f"{BASE_URL}", style="dim blue")

    panel = Panel(info, title="[bold]Connection[/bold]", border_style="cyan", box=HEAVY_EDGE)
    console.print(panel)

    tips = Text()
    tips.append("/help", style="bold magenta")
    tips.append(" commands  ")
    tips.append("/quit", style="bold magenta")
    tips.append(" exit  ")
    tips.append("/stop", style="bold magenta")
    tips.append(" pause  ")
    tips.append("💡 Type while AI works!", style="bold green")

    tips_panel = Panel(tips, title="[bold]Tips[/bold]", border_style="dim", box=DOUBLE_EDGE)
    console.print(tips_panel)
    console.print()
    console.print(Rule(style="dim cyan"))
    console.print()


def print_goodbye() -> None:
    """Print exit message."""
    console.print()
    console.print("  ✨ [dim]See you next time.[/dim]", style="cyan")
    console.print()


# ── Split Layout Agent Runner ───────────────────────────────────────────────

class SplitLayoutRunner:
    """แยก AI output (บน) และ input (ล่าง) ออกจากกันอย่างอิสระ.

    - AI output: แสดงผลตามปกติ อยู่ด้านบน scroll ได้
    - Status bar: fixed บรรทัดล่างสุด-1 แสดงสถานะ AI + preview คิว
    - Input: fixed บรรทัดล่างสุด รับ input จากผู้ใช้

    ทั้งสองส่วนอัพเดทแยกจากกัน ไม่ปนกัน
    """

    _PREVIEW_LEN = 28
    _STATUS_ROW_OFFSET = 1   # status bar = bottom - 1
    _INPUT_ROW_OFFSET = 0    # input = bottom

    def __init__(self):
        self.api_client = create_api_client()
        self.agent = AgentEngine(SYSTEM_PROMPT, self.api_client)

        # Shared state
        self.input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.agent_busy = False
        self.running = True
        self._quit_requested = False

        # Thread-safe lock for stdout (prevents race between
        # status refresh in asyncio and input() in executor thread)
        self._stdout_lock = threading.Lock()

        # Queue tracking for display
        self._current_message: str = ""
        self._queued_previews: list[str] = []

        # Tasks
        self._agent_task: Optional[asyncio.Task] = None
        self._input_task: Optional[asyncio.Task] = None
        self._status_task: Optional[asyncio.Task] = None

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _preview(text: str, max_len: int = 28) -> str:
        """Truncate message for compact display."""
        text = text.replace("\n", " ").strip()
        if len(text) <= max_len:
            return text
        return text[:max_len - 2] + "…"

    def _render_status_line(self) -> str:
        """Build plain-text status line for ANSI output."""
        w = _term_width() - 4  # 2-space padding each side

        if self.agent_busy:
            curr = self._preview(self._current_message, self._PREVIEW_LEN)
            left = f"⏳ AI: \"{curr}\""
        else:
            left = "● READY"

        qp = self._queued_previews
        if qp:
            right = f"📋 {len(qp)}: "
            for i, p in enumerate(qp[:2]):
                if i > 0:
                    right += " → "
                right += f'"{self._preview(p, 14)}"'
            if len(qp) > 2:
                right += f" +{len(qp) - 2}"
        else:
            right = ""

        # Combine: left ... right
        bar = f"  {left}"
        if right:
            combined = f"{bar}  │  {right}"
        else:
            combined = bar

        if len(combined) > w:
            combined = combined[:w - 3] + "…"
        else:
            combined = combined.ljust(w)

        return combined

    def _redraw_bottom(self) -> None:
        """Redraw status bar + input prompt at bottom of terminal."""
        with self._stdout_lock:
            h = _term_height()

            # Save cursor position
            _ansi_save_cursor()

            # Draw status bar (bottom - 1)
            status = self._render_status_line()
            _ansi_move_to(h - self._STATUS_ROW_OFFSET, 1)
            _ansi_clear_line()
            sys.stdout.write(status)
            sys.stdout.flush()

            # Draw input line (bottom)
            _ansi_move_to(h, 1)
            _ansi_clear_line()
            prompt = "  ⏵ "
            sys.stdout.write(prompt)
            sys.stdout.flush()

            # Restore cursor
            _ansi_restore_cursor()

    def _print_ai_divider(self) -> None:
        """Print divider after AI finishes."""
        console.print()
        console.print("  [dim cyan]" + "─" * min(_term_width() - 4, 70) + "[/dim cyan]")

    # ── Status refresh loop ─────────────────────────────────────────────

    async def _status_refresh_loop(self) -> None:
        """Periodically refresh the bottom status bar."""
        while self.running:
            try:
                self._redraw_bottom()
            except Exception:
                pass
            await asyncio.sleep(0.2)

    # ── Input loop ──────────────────────────────────────────────────────

    async def _input_loop(self) -> None:
        """Read user input continuously."""
        loop = asyncio.get_event_loop()

        while self.running:
            # Input prompt
            if self.agent_busy:
                prompt_text = ""  # Minimal: no prompt while AI works
            else:
                prompt_text = "  ⏵ "

            try:
                line = await loop.run_in_executor(None, safe_input, prompt_text)
            except (EOFError, KeyboardInterrupt):
                continue

            if not line:
                continue

            line = line.strip()
            if not line:
                continue

            # Handle commands immediately
            if line.startswith("/") or line.lower() in ("quit", "exit", ":q"):
                await self._handle_command(line)
                continue

            # Normal message → queue
            self._quit_requested = False
            self._queued_previews.append(line)
            await self.input_queue.put(line)

    # ── Agent loop ──────────────────────────────────────────────────────

    async def _agent_loop(self) -> None:
        """Process queued messages one at a time."""
        while self.running:
            try:
                message = await asyncio.wait_for(
                    self.input_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            self.agent_busy = True
            self._current_message = message
            if self._queued_previews:
                self._queued_previews.pop(0)

            self._redraw_bottom()

            try:
                self.agent.reset_stop()
                await self.agent.chat(message)
            except Exception as e:
                console.print(f"\n  [bold red]Error:[/bold red] {e}\n")
            finally:
                self.agent_busy = False
                self._current_message = ""
                self.input_queue.task_done()
                self._print_ai_divider()
                self._redraw_bottom()

    # ── Command handler ─────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> None:
        """Handle slash commands."""
        cmd_lower = cmd.lower().strip()

        if cmd_lower not in ("/quit", "/exit", ":q", "quit", "exit"):
            self._quit_requested = False

        if cmd_lower in ("/quit", "/exit", ":q", "quit", "exit"):
            if self.agent_busy and not self._quit_requested:
                self._quit_requested = True
                console.print("\n  [bold yellow]⚠ Agent is busy. Type /quit again to force quit.[/bold yellow]")
                return
            self.running = False
            if self.agent_busy:
                self.agent.request_stop()
            return

        if cmd_lower == "/help":
            console.print("""
[bold cyan]Commands:[/bold cyan]
  /help      Show help
  /quit      Exit (twice when AI busy)
  /clear     Clear conversation
  /stop      Pause agent
  /queue     Show pending queue
  /continue  Trigger continuation
  /status    Continuation status
  Ctrl+C     Pause agent / Exit when idle
""")
            return

        if cmd_lower == "/clear":
            if self.agent_busy:
                console.print("\n  [yellow]⚠ Cannot clear while agent is busy.[/yellow]")
                return
            self.agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            self.agent.reset_stop()
            self.agent._turn_count = 0
            console.print("\n  [dim]Conversation cleared[/dim]")
            return

        if cmd_lower == "/model":
            console.print(f"\n  Provider: {PROVIDER.upper()}  |  Model: {MODEL}  |  API: {BASE_URL}")
            return

        if cmd_lower == "/queue":
            qsize = self.input_queue.qsize()
            console.print(f"\n  [bold]Queue:[/bold] {qsize} pending  |  Agent: {'🔵 Busy' if self.agent_busy else '🟢 Idle'}")
            for i, p in enumerate(self._queued_previews[:10]):
                console.print(f"    #{i+1}: {self._preview(p, 60)}")
            console.print()
            return

        if cmd_lower in ("/stop", "/pause"):
            if self.agent_busy:
                self.agent.request_stop()
                console.print("\n  [yellow]⏸ Stop requested...[/yellow]")
            else:
                console.print("\n  [dim]Agent is not running.[/dim]")
            return

        if cmd_lower == "/continue":
            if self.agent_busy:
                console.print("\n  [dim]Agent is already running.[/dim]")
            elif self.agent.continuation.can_continue():
                self.agent_busy = True
                self._current_message = "(manual continuation)"
                try:
                    await self.agent.continue_task()
                except Exception as e:
                    console.print(f"\n  [red]Error: {e}[/red]")
                finally:
                    self.agent_busy = False
                    self._current_message = ""
            else:
                console.print("\n  [red]Max continuations reached.[/red]")
            return

        if cmd_lower == "/status":
            ct = self.agent.continuation
            console.print(f"\n  Max continuations: {ct.max_continuations}  |  Used: {ct.count}")
            return

        console.print(f"\n  [dim]Unknown: {cmd}. Type /help[/dim]")

    # ── Main runner ─────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main entry point — start all concurrent tasks."""
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

        # Signal handling
        def handle_sigint(sig, frame):
            if self.agent_busy:
                self.agent.request_stop()
                console.print("\n  [bold yellow](Press Ctrl+C again to exit)[/bold yellow]")
            else:
                console.print()
                self.running = False

        original_sigint = signal.signal(signal.SIGINT, handle_sigint)

        print_greeting()

        # Draw initial bottom bar
        self._redraw_bottom()

        try:
            # Start all tasks
            self._agent_task = asyncio.create_task(self._agent_loop())
            self._status_task = asyncio.create_task(self._status_refresh_loop())
            self._input_task = asyncio.create_task(self._input_loop())

            await self._input_task

        finally:
            self.running = False
            signal.signal(signal.SIGINT, original_sigint)

            for task in [self._agent_task, self._status_task]:
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            await self.api_client.close()
            print_goodbye()


# ── CLI Entry Points ────────────────────────────────────────────────────────

def main_cli() -> None:
    """Entry point for `judecode` command."""
    import sys

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("--version", "-v"):
            from judecode import __version__
            print(f"judecode v{__version__}")
            return
        if arg in ("--help", "-h"):
            print("Jude Code - Your terminal AI coding assistant")
            print()
            print("Usage: judecode [OPTIONS]")
            print()
            print("Options:")
            print("  --version, -v     Show version and exit")
            print("  --help, -h        Show this help message and exit")
            print("  --legacy          Use legacy single-thread mode")
            print()
            print("Default: Split layout (AI output top + input bottom)")
            return
        if arg == "--legacy":
            from judecode.ui.terminal import main_cli as legacy_main
            legacy_main()
            return

    runner = SplitLayoutRunner()
    asyncio.run(runner.run())


if __name__ == "__main__":
    main_cli()
