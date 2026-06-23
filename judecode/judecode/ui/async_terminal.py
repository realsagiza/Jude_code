"""Async Terminal UI for Jude Code — input/output แยก thread กัน.

หลักการ: เรียบง่าย ไม่ใช้ ANSI cursor tricks
- AI output → console.print() ปกติ
- Input → input() ปกติ  
- การแยกพื้นที่เกิดจากธรรมชาติของ terminal: output จะอยู่เหนือ input line เสมอ
- สถานะ AI + คิวแสดงใน prompt เวลา AI ทำงาน
- ไม่มี background refresh, ไม่มี cursor save/restore → ไม่มี race condition
"""

import asyncio
import os
import signal
import sys
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


# ── Agent Runner ────────────────────────────────────────────────────────────

class AsyncAgentRunner:
    """แยก input/output เป็นคนละ asyncio task.

    หลักการสำคัญ:
    - ตอน AI ว่าง → แสดง separator + "⏵ " prompt ปกติ
    - ตอน AI ทำงาน → แสดงสถานะสั้นๆ + เว้น prompt ว่าง (input(""))
    - AI output ปรากฏเหนือ input line โดยธรรมชาติของ terminal
    - ไม่มีการแย่ง cursor → ไม่มี race condition
    """

    _PREVIEW_LEN = 30

    def __init__(self):
        self.api_client = create_api_client()
        self.agent = AgentEngine(SYSTEM_PROMPT, self.api_client)

        # Shared state
        self.input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.agent_busy = False
        self.running = True
        self._quit_requested = False

        # Queue tracking
        self._current_message: str = ""
        self._queued_previews: list[str] = []

        # Tasks
        self._agent_task: Optional[asyncio.Task] = None
        self._input_task: Optional[asyncio.Task] = None

    # ── Helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _preview(text: str, max_len: int = 30) -> str:
        """Truncate message for compact display."""
        text = text.replace("\n", " ").strip()
        if len(text) <= max_len:
            return text
        return text[:max_len - 2] + "…"

    def _print_divider(self, style: str = "dim cyan") -> None:
        """Print a horizontal divider."""
        console.print()
        console.print(f"  [{style}]" + "─" * 68 + f"[/{style}]")

    def _print_status_line(self) -> None:
        """Print compact status: AI state + queue preview + token usage."""
        parts = []

        if self.agent_busy:
            curr = self._preview(self._current_message, self._PREVIEW_LEN)
            parts.append(f"[bold yellow]⏳ AI:[/bold yellow] [bold white]\"{curr}\"[/bold white]")
        else:
            parts.append("[bold green]● READY[/bold green]")

        qp = self._queued_previews
        if qp:
            q_text = f"[dim]📋 {len(qp)}:[/dim] "
            for i, p in enumerate(qp[:3]):
                if i > 0:
                    q_text += " [dim]→[/dim] "
                q_text += f'[dim]"{self._preview(p, 16)}"[/dim]'
            if len(qp) > 3:
                q_text += f" [dim]+{len(qp) - 3}[/dim]"
            parts.append(q_text)

        # ── Realtime token counter ──
        total = self.agent.autonomous.budget.total_input_tokens + self.agent.autonomous.budget.total_output_tokens
        cost = self.agent.autonomous.budget.total_cost
        turns = self.agent.autonomous.budget.turn_count
        max_tok = self.agent.autonomous.budget.max_tokens
        tok_pct = (total / max_tok * 100) if max_tok > 0 else 0
        parts.append(
            f"[dim cyan]💰 ${cost:.2f}[/dim cyan] [dim]|[/dim] "
            f"[dim cyan]📊 {total:,}[/dim cyan][dim]/[dim]{max_tok:,}[/dim] "
            f"[dim]({tok_pct:.0f}%)[/dim] [dim]|[/dim] 🔄[dim]{turns}[/dim]"
        )

        line = "  " + "  │  ".join(parts)
        console.print(line)

    def _print_end_output(self) -> None:
        """Print marker when AI finishes output + budget summary."""
        console.print()
        console.print(f"  [dim cyan]" + "▬" * 68 + f"[/dim cyan]")
        # ── Budget summary ──
        b = self.agent.autonomous.budget
        total = b.total_input_tokens + b.total_output_tokens
        cost = b.total_cost
        turns = b.turn_count
        max_tok = b.max_tokens
        tok_pct = (total / max_tok * 100) if max_tok > 0 else 0
        # Top 3 burn categories this session
        cat_bars = []
        for cat, cnt in sorted(b.tokens.items(), key=lambda x: x[1], reverse=True)[:3]:
            if cnt > 0:
                info = b.CATEGORIES.get(cat, {})
                cat_bars.append(f"{info.get('icon','')} {cnt:,}")
        cat_str = "  ".join(cat_bars) if cat_bars else "-"
        console.print(
            f"  [dim cyan]▐[/dim cyan] [dim]END OUTPUT[/dim]  [dim]│[/dim]  "
            f"[bold yellow]💰 ${cost:.2f}[/bold yellow]  [dim]│[/dim]  "
            f"[bold cyan]📊 {total:,}[/bold cyan][dim]/{max_tok:,} tokens ({tok_pct:.0f}%)[/dim]  [dim]│[/dim]  "
            f"🔄[dim]{turns} turns[/dim]\n"
            f"  [dim cyan]▐[/dim cyan] [dim]Top burn:[/dim] {cat_str}"
        )

    # ── Input loop ──────────────────────────────────────────────────────

    async def _input_loop(self) -> None:
        """Read user input continuously, queue messages."""
        loop = asyncio.get_event_loop()

        while self.running:
            # Show appropriate prompt based on AI state
            if self.agent_busy:
                # AI working: minimal prompt
                self._print_divider("dim")
                self._print_status_line()
                prompt_text = ""  # empty = just cursor
            else:
                # AI idle: full prompt
                self._print_divider("dim cyan")
                self._print_status_line()
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
        """Process queued messages."""
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

            try:
                self.agent.reset_stop()
                await self.agent.chat(message)
            except Exception as e:
                console.print(f"\n  [bold red]Error:[/bold red] {e}\n")
            finally:
                self.agent_busy = False
                self._current_message = ""
                self.input_queue.task_done()
                self._print_end_output()

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
  /budget    Token budget breakdown
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
            self._queued_previews.clear()
            console.print("\n  [dim]Conversation cleared[/dim]")
            return

        if cmd_lower == "/model":
            console.print(f"\n  Provider: {PROVIDER.upper()}  |  Model: {MODEL}  |  API: {BASE_URL}")
            return

        if cmd_lower == "/queue":
            qsize = self.input_queue.qsize()
            console.print(f"\n  [bold]Queue:[/bold] {qsize} pending  |  Agent: {'🔵 Busy' if self.agent_busy else '🟢 Idle'}")
            console.print(f"  {self.agent.autonomous.budget.get_compact_status()}")
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
            console.print(f"  {self.agent.autonomous.budget.get_compact_status()}")
            return

        if cmd_lower == "/budget":
            console.print(f"\n  [bold yellow]💰 Token Budget Report:[/bold yellow]")
            console.print(self.agent.autonomous.budget.get_status())
            return

        console.print(f"\n  [dim]Unknown: {cmd}. Type /help[/dim]")

    # ── Main runner ─────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main entry point."""
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

        try:
            # Start both tasks
            self._agent_task = asyncio.create_task(self._agent_loop())
            self._input_task = asyncio.create_task(self._input_loop())

            await self._input_task

        finally:
            self.running = False
            signal.signal(signal.SIGINT, original_sigint)

            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
                try:
                    await self._agent_task
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
            print("Default: Async mode - type while AI works!")
            return
        if arg == "--legacy":
            from judecode.ui.terminal import main_cli as legacy_main
            legacy_main()
            return

    runner = AsyncAgentRunner()
    asyncio.run(runner.run())


if __name__ == "__main__":
    main_cli()
