"""Textual TUI for Jude Code — fixed-layout terminal UI.

ใช้ Textual framework สร้าง UI แบบ fixed-layout บน terminal:
  ┌─ Header: Jude Code | model | status ────────┐
  │ ┌─ AI Output (scrollable RichLog) ─────────┐ │
  │ │ ... thinking ...                         │ │
  │ │ ... tool calls ...                       │ │
  │ │ ... responses ...                        │ │
  │ └──────────────────────────────────────────┘ │
  ├─ Status: ⏳ AI: "..." │ 📋 2 queued ────────┤
  ├─ Input: ⏵ [type here] ──────────────────────┤
  └──────────────────────────────────────────────┘

ทุกส่วนมีตำแหน่ง fixed — ไม่มีทางปนกัน
"""

import asyncio
import io
import signal
import sys
from typing import Optional

from rich.console import Console as RichConsole
from rich.text import Text
from rich.panel import Panel
from rich.table import Table

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, RichLog, Input, Static
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual import work

from judecode.config import (
    SYSTEM_PROMPT, MODEL, BASE_URL, MAX_CONTINUATIONS, PROVIDER,
)
from judecode.api import create_api_client
from judecode.agent.engine import AgentEngine


# ── Console Proxy ───────────────────────────────────────────────────────────
# Redirects agent's console.print() calls to the RichLog widget

class RichLogProxy:
    """Proxies console.print() calls to a Textual RichLog widget."""

    def __init__(self):
        self._widget: Optional[RichLog] = None
        self._temp_console = RichConsole(
            file=io.StringIO(),
            force_terminal=False,
            color_system="truecolor",
            width=120,
        )

    def set_widget(self, widget: RichLog) -> None:
        self._widget = widget

    def print(self, *args, **kwargs) -> None:
        """Forward print to RichLog widget."""
        if self._widget is None:
            # Fallback: print to real stdout
            import builtins
            builtins.print(*args, **kwargs)
            return

        # Capture Rich output to string
        buf = io.StringIO()
        self._temp_console.file = buf
        self._temp_console.print(*args, **kwargs)
        text = buf.getvalue()

        # Write to RichLog (strip trailing newline to avoid extra blank lines)
        if text:
            self._widget.write(text.rstrip("\n"))

    def __getattr__(self, name):
        """Delegate unknown attributes to the real console."""
        return getattr(self._temp_console, name)


# Global proxy instance
console_proxy = RichLogProxy()


# ── Status Bar Widget ──────────────────────────────────────────────────────

class StatusBar(Static):
    """Custom status bar showing AI state + queue preview."""

    agent_busy: reactive[bool] = reactive(False)
    current_message: reactive[str] = reactive("")
    queue_count: reactive[int] = reactive(0)
    queue_previews: reactive[list[str]] = reactive([])

    def watch_agent_busy(self, busy: bool) -> None:
        self.refresh()

    def watch_current_message(self, msg: str) -> None:
        self.refresh()

    def watch_queue_count(self, count: int) -> None:
        self.refresh()

    def watch_queue_previews(self, previews: list[str]) -> None:
        self.refresh()

    def render(self) -> Text:
        """Render the status bar."""
        text = Text()

        if self.agent_busy:
            preview = self._truncate(self.current_message, 35)
            text.append(" ⏳ ", style="bold yellow")
            text.append(f'AI: "{preview}"', style="bold white")
        else:
            text.append(" ● ", style="bold green")
            text.append("READY", style="bold green")

        if self.queue_previews:
            text.append("  │  ", style="dim")
            text.append(f"📋 {len(self.queue_previews)}: ", style="bold cyan")
            for i, p in enumerate(self.queue_previews[:3]):
                if i > 0:
                    text.append(" → ", style="dim")
                text.append(f'"{self._truncate(p, 15)}"', style="dim")
            if len(self.queue_previews) > 3:
                text.append(f" +{len(self.queue_previews) - 3}", style="dim")

        return text

    @staticmethod
    def _truncate(s: str, max_len: int) -> str:
        s = s.replace("\n", " ").strip()
        if len(s) <= max_len:
            return s
        return s[:max_len - 2] + "…"


# ── Main TUI App ───────────────────────────────────────────────────────────

class JudeCodeTUI(App):
    """Jude Code Textual TUI application."""

    CSS = """
    Screen {
        layout: grid;
        grid-size: 1;
        grid-rows: auto 1fr auto auto;
    }

    #output-container {
        height: 1fr;
        border: solid $primary-darken-2;
        background: $surface;
    }

    #output {
        height: 1fr;
        overflow-y: scroll;
    }

    #status-bar {
        height: 1;
        background: $primary-darken-3;
        color: $text;
        padding: 0 1;
    }

    #input-container {
        height: auto;
        border: solid $primary;
        background: $surface-darken-1;
    }

    #input {
        border: none;
        background: $surface-darken-1;
    }

    #input > .input--placeholder {
        color: $text-disabled;
    }

    Header {
        background: $primary-darken-2;
        color: $text;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit_app", "Quit"),
        ("escape", "focus_input", "Focus Input"),
    ]

    def __init__(self):
        super().__init__()
        self.api_client = create_api_client()
        self.agent = AgentEngine(SYSTEM_PROMPT, self.api_client)

        # Message queue
        self.input_queue: asyncio.Queue[str] = asyncio.Queue()

        # State
        self._agent_busy = False
        self._current_message = ""
        self._queued_previews: list[str] = []
        self._quit_requested = False

    # ── Compose ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="output-container"):
            yield RichLog(id="output", markup=True, wrap=True, highlight=True)
        yield StatusBar(id="status-bar")
        with Container(id="input-container"):
            yield Input(
                id="input",
                placeholder="⏵ Type your message... (Enter to send, /commands available)",
            )

    # ── Mount ──────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        """Initialize after widgets are mounted."""
        # Set the console proxy to write to our RichLog
        output = self.query_one("#output", RichLog)
        console_proxy.set_widget(output)

        # Replace the global console with our proxy
        import judecode.ui.console as console_mod
        console_mod.console = console_proxy

        # Set header info
        header = self.query_one(Header)
        header.sub_title = f"[dim]{PROVIDER.upper()} | {MODEL}[/dim]"

        # Write welcome message
        output.write("[bold cyan]Jude Code[/bold cyan] — Terminal AI Coding Assistant")
        output.write(f"[dim]Provider: {PROVIDER.upper()} | Model: {MODEL}[/dim]")
        output.write("[dim]Split TUI — AI output (top) + your input (bottom)[/dim]")
        output.write("")

        # Focus input
        self.query_one("#input", Input).focus()

        # Start agent worker
        self.agent_worker()

    # ── Input Handler ──────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        line = event.value.strip()
        if not line:
            return

        # Clear input for next message
        event.input.value = ""

        # Handle commands
        if line.startswith("/") or line.lower() in ("quit", "exit", ":q"):
            await self._handle_command(line)
            return

        # Reset quit flag on normal message
        self._quit_requested = False

        # Queue the message
        self._queued_previews.append(line)
        await self.input_queue.put(line)

        # Update status bar
        self._update_status_bar()

    # ── Worker: Agent Loop ─────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def agent_worker(self) -> None:
        """Background worker: process messages from the queue."""
        while self.is_running:
            try:
                message = await asyncio.wait_for(
                    self.input_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            self._agent_busy = True
            self._current_message = message
            if self._queued_previews:
                self._queued_previews.pop(0)
            self._update_status_bar()

            try:
                self.agent.reset_stop()
                await self.agent.chat(message)
            except Exception as e:
                console_proxy.print(f"\n[bold red]Error:[/bold red] {e}\n")
            finally:
                self._agent_busy = False
                self._current_message = ""
                self.input_queue.task_done()
                self._update_status_bar()
                # Print divider
                console_proxy.print("[dim]" + "─" * 60 + "[/dim]")

    # ── Status Bar Update ──────────────────────────────────────────────

    def _update_status_bar(self) -> None:
        """Update the status bar widget from the main thread."""
        try:
            bar = self.query_one("#status-bar", StatusBar)
            bar.agent_busy = self._agent_busy
            bar.current_message = self._current_message
            bar.queue_previews = list(self._queued_previews)
            bar.queue_count = len(self._queued_previews)
        except Exception:
            pass

    # ── Command Handler ────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> None:
        """Handle slash commands."""
        cmd_lower = cmd.lower().strip()

        if cmd_lower not in ("/quit", "/exit", ":q", "quit", "exit"):
            self._quit_requested = False

        # ── Quit ──
        if cmd_lower in ("/quit", "/exit", ":q", "quit", "exit"):
            if self._agent_busy and not self._quit_requested:
                self._quit_requested = True
                console_proxy.print("[bold yellow]⚠ Agent is busy. Type /quit again to force quit.[/bold yellow]")
                return
            await self.action_quit_app()
            return

        # ── Help ──
        if cmd_lower == "/help":
            console_proxy.print("""
[bold cyan]Commands:[/bold cyan]
  /help      Show help
  /quit      Exit (twice when AI busy)
  /clear     Clear conversation
  /stop      Pause agent
  /queue     Show pending queue
  /continue  Trigger continuation
  /status    Continuation status
  Ctrl+C     Quit app
""")
            return

        # ── Clear ──
        if cmd_lower == "/clear":
            if self._agent_busy:
                console_proxy.print("[yellow]⚠ Cannot clear while agent is busy.[/yellow]")
                return
            self.agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            self.agent.reset_stop()
            self.agent._turn_count = 0
            self._queued_previews.clear()
            try:
                self.query_one("#output", RichLog).clear()
            except Exception:
                pass
            console_proxy.print("[dim]Conversation cleared[/dim]")
            self._update_status_bar()
            return

        # ── Queue ──
        if cmd_lower == "/queue":
            qsize = self.input_queue.qsize()
            console_proxy.print(f"\nQueue: {qsize} pending  |  Agent: {'🔵 Busy' if self._agent_busy else '🟢 Idle'}")
            for i, p in enumerate(self._queued_previews[:10]):
                preview = p.replace("\n", " ")[:60]
                console_proxy.print(f"  #{i+1}: {preview}")
            return

        # ── Stop ──
        if cmd_lower in ("/stop", "/pause"):
            if self._agent_busy:
                self.agent.request_stop()
                console_proxy.print("[yellow]⏸ Stop requested...[/yellow]")
            else:
                console_proxy.print("[dim]Agent is not running.[/dim]")
            return

        # ── Continue ──
        if cmd_lower == "/continue":
            if self._agent_busy:
                console_proxy.print("[dim]Agent is already running.[/dim]")
            elif self.agent.continuation.can_continue():
                self._agent_busy = True
                self._current_message = "(manual continuation)"
                self._update_status_bar()
                try:
                    await self.agent.continue_task()
                except Exception as e:
                    console_proxy.print(f"[red]Error: {e}[/red]")
                finally:
                    self._agent_busy = False
                    self._current_message = ""
                    self._update_status_bar()
            else:
                console_proxy.print("[red]Max continuations reached.[/red]")
            return

        # ── Status ──
        if cmd_lower == "/status":
            ct = self.agent.continuation
            console_proxy.print(f"Max continuations: {ct.max_continuations}  |  Used: {ct.count}")
            return

        # ── Model ──
        if cmd_lower == "/model":
            console_proxy.print(f"Provider: {PROVIDER.upper()}  |  Model: {MODEL}  |  API: {BASE_URL}")
            return

        console_proxy.print(f"[dim]Unknown: {cmd}. Type /help[/dim]")

    # ── Actions ────────────────────────────────────────────────────────

    async def action_quit_app(self) -> None:
        """Quit the application."""
        if self._agent_busy:
            self.agent.request_stop()
        self._agent_busy = False
        self.running = False
        await self.api_client.close()
        self.exit()

    async def action_focus_input(self) -> None:
        """Focus the input widget."""
        self.query_one("#input", Input).focus()

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def on_unmount(self) -> None:
        """Cleanup on exit."""
        await self.api_client.close()


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
            print("Default: Textual TUI with fixed split layout")
            return
        if arg == "--legacy":
            from judecode.ui.terminal import main_cli as legacy_main
            legacy_main()
            return

    app = JudeCodeTUI()
    app.run()


if __name__ == "__main__":
    main_cli()
