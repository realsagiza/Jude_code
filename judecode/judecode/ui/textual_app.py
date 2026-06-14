"""Textual TUI for Jude Code — fixed-layout terminal UI.

Layout (dock-based, reliable):
  ┌─ Header ──────────────────────────────────────┐
  │  Jude Code | provider | model                  │
  ├────────────────────────────────────────────────┤
  │ ┌─ AI Output ────────────────────────────────┐ │
  │ │  (RichLog - fills all remaining space)     │ │
  │ │  auto-scrolls to bottom                    │ │
  │ └────────────────────────────────────────────┘ │
  ├─ Status ──────────────────────────────────────┤
  │  ⏳ AI: "..." │ 📋 2 queued: "..." → "..."    │
  ├─ Input ───────────────────────────────────────┤
  │  ⏵ [type /help for commands]                 │
  └──────────────────────────────────────────────┘
"""

import asyncio
import io
import sys
from typing import Optional

from rich.console import Console as RichConsole
from rich.text import Text

from textual.app import App, ComposeResult
from textual.widgets import Header, RichLog, Input, Static
from textual.containers import Container, VerticalScroll
from textual.reactive import reactive
from textual import work

from judecode.config import (
    SYSTEM_PROMPT, MODEL, BASE_URL, MAX_CONTINUATIONS, PROVIDER,
)
from judecode.api import create_api_client
from judecode.agent.engine import AgentEngine


# ── Console Proxy ───────────────────────────────────────────────────────────

class RichLogProxy:
    """Proxies console.print() calls to a Textual RichLog widget."""

    def __init__(self):
        self._widget: Optional[RichLog] = None
        self._buf = io.StringIO()
        self._temp_console = RichConsole(
            file=self._buf,
            force_terminal=False,
            color_system="truecolor",
            width=120,
            no_color=False,
        )

    def set_widget(self, widget: RichLog) -> None:
        self._widget = widget

    def print(self, *args, **kwargs) -> None:
        """Forward print to RichLog widget."""
        if self._widget is None:
            return

        try:
            # Reset buffer
            self._buf.truncate(0)
            self._buf.seek(0)

            # Capture Rich output
            self._temp_console.file = self._buf
            self._temp_console.print(*args, **kwargs)

            text = self._buf.getvalue()
            if text:
                # Write each non-empty line to RichLog
                for line in text.split("\n"):
                    stripped = line.rstrip()
                    if stripped:
                        self._widget.write(stripped, scroll_end=True)
                    else:
                        self._widget.write("", scroll_end=True)
        except Exception:
            pass

    def rule(self, *args, **kwargs) -> None:
        """Print a horizontal rule."""
        self.print("─" * 60, style="dim")

    def __getattr__(self, name):
        return getattr(self._temp_console, name)


# Global proxy - must be created before widget exists
console_proxy = RichLogProxy()


# ── Status Bar ─────────────────────────────────────────────────────────────

class StatusBar(Static):
    """Custom status bar showing AI state + queue preview."""

    agent_busy: reactive[bool] = reactive(False, repaint=True)
    current_message: reactive[str] = reactive("", repaint=True)
    queue_previews: reactive[list[str]] = reactive([], repaint=True)

    def render(self) -> Text:
        text = Text(no_wrap=True)

        if self.agent_busy:
            preview = self._truncate(self.current_message, 35)
            text.append("⏳ ", style="bold yellow")
            text.append(f'AI: "{preview}"', style="bold white")
        else:
            text.append("● ", style="bold green")
            text.append("READY", style="bold green")

        if self.queue_previews:
            text.append("  │  ", style="")
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
        layout: dock;
        background: $surface;
    }

    #output-container {
        dock: top;
        height: 1fr;
        border-bottom: solid $primary;
    }

    #output {
        height: 100%;
        border: none;
        padding: 0 1;
        background: $surface;
    }

    #bottom-area {
        dock: bottom;
        height: auto;
        background: $panel;
        border-top: solid $primary;
    }

    #status-bar {
        height: 1;
        padding: 0 1;
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }

    #input {
        border: none;
        padding: 0 1;
        background: $panel;
        height: auto;
    }

    #input > .input--placeholder {
        color: $text-disabled;
    }

    Header {
        dock: top;
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

        self.input_queue: asyncio.Queue[str] = asyncio.Queue()
        self._agent_busy = False
        self._current_message = ""
        self._queued_previews: list[str] = []
        self._quit_requested = False

    # ── Compose ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="output-container"):
            yield RichLog(id="output", markup=True, wrap=True, highlight=True, auto_scroll=True)
        with Container(id="bottom-area"):
            yield StatusBar(id="status-bar")
            yield Input(
                id="input",
                placeholder="⏵ Type /help for commands...",
            )

    # ── Mount ──────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        """Initialize after widgets are mounted."""
        output = self.query_one("#output", RichLog)
        console_proxy.set_widget(output)

        # Replace the global console with our proxy
        import judecode.ui.console as console_mod
        console_mod.console = console_proxy

        # Header
        header = self.query_one(Header)
        header.sub_title = f"[dim]{PROVIDER.upper()} | {MODEL}[/dim]"

        # Welcome
        output.write("[bold cyan]Jude Code[/bold cyan] — Terminal AI Coding Assistant")
        output.write(f"[dim]Provider: {PROVIDER.upper()} | Model: {MODEL}[/dim]")
        output.write("[dim]Commands: /help /quit /stop /queue /clear[/dim]")
        output.write("")

        self.query_one("#input", Input).focus()
        self.agent_worker()

    # ── Input Handler ──────────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if not line:
            return

        event.input.value = ""

        if line.startswith("/") or line.lower() in ("quit", "exit", ":q"):
            await self._handle_command(line)
            return

        self._quit_requested = False
        self._queued_previews.append(line)
        await self.input_queue.put(line)
        self._update_status_bar()

    # ── Worker: Agent Loop ─────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def agent_worker(self) -> None:
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
                console_proxy.print(f"[bold red]Error:[/bold red] {e}")
            finally:
                self._agent_busy = False
                self._current_message = ""
                self.input_queue.task_done()
                self._update_status_bar()
                console_proxy.print("[dim]" + "─" * 50 + "[/dim]")

    # ── Status Bar Update ──────────────────────────────────────────────

    def _update_status_bar(self) -> None:
        try:
            bar = self.query_one("#status-bar", StatusBar)
            bar.agent_busy = self._agent_busy
            bar.current_message = self._current_message
            bar.queue_previews = list(self._queued_previews)
        except Exception:
            pass

    # ── Command Handler ────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> None:
        cmd_lower = cmd.lower().strip()

        if cmd_lower not in ("/quit", "/exit", ":q", "quit", "exit"):
            self._quit_requested = False

        if cmd_lower in ("/quit", "/exit", ":q", "quit", "exit"):
            if self._agent_busy and not self._quit_requested:
                self._quit_requested = True
                console_proxy.print("[bold yellow]⚠ Agent is busy. Type /quit again to force quit.[/bold yellow]")
                return
            await self.action_quit_app()
            return

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

        if cmd_lower == "/queue":
            qsize = self.input_queue.qsize()
            console_proxy.print(f"Queue: {qsize} pending  |  Agent: {'🔵 Busy' if self._agent_busy else '🟢 Idle'}")
            for i, p in enumerate(self._queued_previews[:10]):
                console_proxy.print(f"  #{i+1}: {self._truncate(p, 50)}")
            return

        if cmd_lower in ("/stop", "/pause"):
            if self._agent_busy:
                self.agent.request_stop()
                console_proxy.print("[yellow]⏸ Stop requested...[/yellow]")
            else:
                console_proxy.print("[dim]Agent is not running.[/dim]")
            return

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

        if cmd_lower == "/status":
            ct = self.agent.continuation
            console_proxy.print(f"Max continuations: {ct.max_continuations}  |  Used: {ct.count}")
            return

        if cmd_lower == "/model":
            console_proxy.print(f"Provider: {PROVIDER.upper()}  |  Model: {MODEL}  |  API: {BASE_URL}")
            return

        console_proxy.print(f"[dim]Unknown: {cmd}. Type /help[/dim]")

    @staticmethod
    def _truncate(s: str, max_len: int) -> str:
        s = s.replace("\n", " ").strip()
        if len(s) <= max_len:
            return s
        return s[:max_len - 2] + "…"

    # ── Actions ────────────────────────────────────────────────────────

    async def action_quit_app(self) -> None:
        if self._agent_busy:
            self.agent.request_stop()
        self._agent_busy = False
        self.running = False
        await self.api_client.close()
        self.exit()

    async def action_focus_input(self) -> None:
        self.query_one("#input", Input).focus()

    async def on_unmount(self) -> None:
        await self.api_client.close()


# ── CLI Entry Points ────────────────────────────────────────────────────────

def main_cli() -> None:
    import sys

    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg in ("--version", "-v"):
            from judecode import __version__
            print(f"judecode v{__version__}")
            return
        if arg in ("--help", "-h"):
            print("Jude Code - Your terminal AI coding assistant")
            print("Usage: judecode [OPTIONS]")
            print("  --legacy   Legacy single-thread mode")
            print("  --simple   Async mode without Textual TUI")
            return
        if arg == "--legacy":
            from judecode.ui.terminal import main_cli as legacy_main
            legacy_main()
            return
        if arg == "--simple":
            from judecode.ui.async_terminal import main_cli as async_main
            async_main()
            return

    app = JudeCodeTUI()
    app.run()


if __name__ == "__main__":
    main_cli()
