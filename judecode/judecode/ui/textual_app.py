"""Textual TUI for Jude Code — fixed-layout terminal UI.
"""

import asyncio
import sys
from typing import Optional

from rich.text import Text

from textual.app import App, ComposeResult
from textual.widgets import Header, RichLog, Input, Static
from textual.containers import Container
from textual.reactive import reactive
from textual import work

from judecode.config import (
    SYSTEM_PROMPT, MODEL, BASE_URL, MAX_CONTINUATIONS, PROVIDER,
)
from judecode.api import create_api_client


# ═══════════════════════════════════════════════════════════════════════
# 1. Console Proxy — must be created BEFORE AgentEngine import
# ═══════════════════════════════════════════════════════════════════════

class RichLogProxy:
    """Proxies console.print() calls to a Textual RichLog widget.

    Intercepts at the renderable level — converts Rich markup strings
    directly to RichLog.write() calls, preserving styling.
    """

    def __init__(self):
        self._widget: Optional[RichLog] = None

    def set_widget(self, widget: RichLog) -> None:
        self._widget = widget

    def print(self, *args, **kwargs) -> None:
        if self._widget is None:
            return
        try:
            # Build a single string from all args (preserving Rich markup)
            parts = []
            for arg in args:
                parts.append(str(arg))
            text = " ".join(parts)
            if text.strip():
                self._widget.write(text, scroll_end=True)
        except Exception:
            import traceback
            traceback.print_exc()

    def __getattr__(self, name):
        # Return a no-op for anything else
        return lambda *a, **kw: None


# Create the global proxy instance
console_proxy = RichLogProxy()

# Replace console NOW — before AgentEngine imports it
import judecode.ui.console as _console_mod
_console_mod.console = console_proxy

# NOW safe to import engine — it will get the proxy
from judecode.agent.engine import AgentEngine


# ═══════════════════════════════════════════════════════════════════════
# 2. Status Bar Widget
# ═══════════════════════════════════════════════════════════════════════

class StatusBar(Static):
    """Custom status bar showing AI state + queue preview."""

    agent_busy: reactive[bool] = reactive(False, repaint=True)
    current_message: reactive[str] = reactive("", repaint=True)
    queue_previews: reactive[list[str]] = reactive([], repaint=True)

    def render(self) -> Text:
        text = Text(no_wrap=True)

        if self.agent_busy:
            preview = self._trunc(self.current_message, 35)
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
                text.append(f'"{self._trunc(p, 15)}"', style="dim")
            if len(self.queue_previews) > 3:
                text.append(f" +{len(self.queue_previews) - 3}", style="dim")

        return text

    @staticmethod
    def _trunc(s: str, max_len: int) -> str:
        s = s.replace("\n", " ").strip()
        if len(s) <= max_len:
            return s
        return s[:max_len - 2] + "…"


# ═══════════════════════════════════════════════════════════════════════
# 3. Main TUI App
# ═══════════════════════════════════════════════════════════════════════

class JudeCodeTUI(App):
    """Jude Code Textual TUI application."""

    TITLE = "Jude Code"

    CSS = """
    Screen { layout: vertical; }

    #output-container {
        height: 1fr;
        border-bottom: solid white;
    }
    #output {
        height: 100%;
        border: none;
        padding: 0 1;
    }
    #bottom-area {
        height: auto;
        border-top: solid white;
    }
    #status-bar {
        height: 1;
        padding: 0 1;
        color: white;
    }
    #input {
        border: none;
        padding: 0 1;
        height: auto;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit_app", "Quit"),
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

    # ── Compose ────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="output-container"):
            yield RichLog(id="output", markup=True, wrap=True, auto_scroll=True)
        with Container(id="bottom-area"):
            yield StatusBar(id="status-bar")
            yield Input(id="input", placeholder="⏵ Type /help for commands...")

    # ── Mount ──────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        output = self.query_one("#output", RichLog)
        console_proxy.set_widget(output)
        console_proxy.print("[bold cyan]Jude Code[/bold cyan] — Terminal AI Coding Assistant")
        console_proxy.print(f"[dim]{PROVIDER.upper()} | {MODEL}[/dim]")
        console_proxy.print("[dim]Commands: /help /quit /stop /queue /clear[/dim]")
        console_proxy.print("")

        self.query_one("#input", Input).focus()
        self.agent_worker()

    # ── Input ──────────────────────────────────────────────────────

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
        self._refresh_status()

    # ── Agent Worker ───────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def agent_worker(self) -> None:
        while self.is_running:
            try:
                msg = await asyncio.wait_for(self.input_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            self._agent_busy = True
            self._current_message = msg
            if self._queued_previews:
                self._queued_previews.pop(0)
            self._refresh_status()

            try:
                self.agent.reset_stop()
                await self.agent.chat(msg)
            except Exception as e:
                console_proxy.print(f"[bold red]Error:[/bold red] {e}")
            finally:
                self._agent_busy = False
                self._current_message = ""
                self.input_queue.task_done()
                self._refresh_status()
                console_proxy.print("[dim]" + "─" * 50 + "[/dim]")

    # ── Status ─────────────────────────────────────────────────────

    def _refresh_status(self) -> None:
        try:
            bar = self.query_one("#status-bar", StatusBar)
            bar.agent_busy = self._agent_busy
            bar.current_message = self._current_message
            bar.queue_previews = list(self._queued_previews)
        except Exception:
            pass

    # ── Commands ───────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> None:
        c = cmd.lower().strip()
        if c not in ("/quit", "/exit", ":q", "quit", "exit"):
            self._quit_requested = False

        if c in ("/quit", "/exit", ":q", "quit", "exit"):
            if self._agent_busy and not self._quit_requested:
                self._quit_requested = True
                console_proxy.print("[bold yellow]⚠ AI busy. Type /quit again.[/bold yellow]")
                return
            await self.action_quit()

        elif c == "/help":
            console_proxy.print("[bold cyan]/help /quit /clear /stop /queue /continue /status[/bold cyan]")

        elif c == "/clear":
            if self._agent_busy:
                console_proxy.print("[yellow]⚠ Cannot clear while AI busy.[/yellow]"); return
            self.agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            self.agent.reset_stop()
            self.agent._turn_count = 0
            self._queued_previews.clear()
            try: self.query_one("#output", RichLog).clear()
            except: pass
            console_proxy.print("[dim]Conversation cleared[/dim]")
            self._refresh_status()

        elif c == "/queue":
            qs = self.input_queue.qsize()
            console_proxy.print(f"Queue: {qs}  |  Agent: {'🔵 Busy' if self._agent_busy else '🟢 Idle'}")
            for i, p in enumerate(self._queued_previews[:10]):
                console_proxy.print(f"  #{i+1}: {self._trunc(p, 50)}")

        elif c in ("/stop", "/pause"):
            if self._agent_busy:
                self.agent.request_stop()
                console_proxy.print("[yellow]⏸ Stop requested...[/yellow]")
            else:
                console_proxy.print("[dim]Agent is not running.[/dim]")

        elif c == "/continue":
            if self._agent_busy:
                console_proxy.print("[dim]Agent is already running.[/dim]")
            elif self.agent.continuation.can_continue():
                self._agent_busy = True
                self._current_message = "(manual)"
                self._refresh_status()
                try: await self.agent.continue_task()
                except Exception as e: console_proxy.print(f"[red]Error: {e}[/red]")
                finally:
                    self._agent_busy = False
                    self._current_message = ""
                    self._refresh_status()
            else:
                console_proxy.print("[red]Max continuations reached.[/red]")

        elif c == "/status":
            ct = self.agent.continuation
            console_proxy.print(f"Continuations: {ct.count}/{ct.max_continuations}")

        elif c == "/model":
            console_proxy.print(f"{PROVIDER.upper()} | {MODEL} | {BASE_URL}")

        else:
            console_proxy.print(f"[dim]Unknown: {cmd}[/dim]")

    @staticmethod
    def _trunc(s: str, n: int) -> str:
        s = s.replace("\n", " ").strip()
        return s if len(s) <= n else s[:n-2] + "…"

    # ── Actions ────────────────────────────────────────────────────

    async def action_quit(self) -> None:
        if self._agent_busy:
            self.agent.request_stop()
        self.running = False
        await self.api_client.close()
        self.exit()

    async def on_unmount(self) -> None:
        await self.api_client.close()


# ═══════════════════════════════════════════════════════════════════════
# 4. CLI Entry Point
# ═══════════════════════════════════════════════════════════════════════

def main_cli() -> None:
    import sys
    if len(sys.argv) > 1:
        a = sys.argv[1]
        if a in ("--version", "-v"):
            from judecode import __version__
            print(f"judecode v{__version__}"); return
        if a in ("--help", "-h"):
            print("Usage: judecode [--legacy|--simple]"); return
        if a == "--legacy":
            from judecode.ui.terminal import main_cli as m; m(); return
        if a == "--simple":
            from judecode.ui.async_terminal import main_cli as m; m(); return
    try:
        JudeCodeTUI().run()
    except Exception as e:
        import traceback
        print(f"\nFatal error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main_cli()
