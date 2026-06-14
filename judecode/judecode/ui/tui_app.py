"""Beautiful Textual TUI for Jude Code.

A clean, full-screen terminal UI with clearly separated zones:

    ┌──────────────────────────── Header ────────────────────────────┐
    │  Jude Code                                       ● status        │
    ├───────────────────────────────────────────┬─────────────────────┤
    │                                            │   STATUS            │
    │            OUTPUT  (conversation)          │   ─────────         │
    │            scrolling RichLog               │   QUEUE             │
    │                                            │   #1 ...            │
    ├───────────────────────────────────────────┴─────────────────────┤
    │  ⏵  type your message here ...                                   │
    ├──────────────────────────── Footer ────────────────────────────┤

Design goals (per request):
- ส่วนพิมพ์ข้อความ (input)         → fixed box at the bottom
- ส่วนแสดงผล (output)             → big scrolling pane (left/main)
- ส่วนคิว prompt + สถานะ (queue)  → sidebar on the right
- layout ชัดเจน สวยงาม ใช้ง่าย
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from rich.console import Group
from rich.text import Text
from rich.markup import MarkupError

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, RichLog, Static

from judecode.config import (
    SYSTEM_PROMPT, MODEL, BASE_URL, MAX_CONTINUATIONS, PROVIDER,
)
from judecode.api import create_api_client
from judecode.agent.engine import AgentEngine
from judecode.ui.console import console, set_console_sink


WELCOME = """\
[bold cyan]██╗   ██╗██████╗ ███████╗   ██████╗ ██████╗ ██████╗ ███████╗[/bold cyan]
[bold cyan]██║   ██║██╔══██╗██╔════╝  ██╔════╝██╔═══██╗██╔══██╗██╔════╝[/bold cyan]
[bold cyan]██║   ██║██║  ██║█████╗    ██║     ██║   ██║██║  ██║█████╗  [/bold cyan]
[bold cyan]██║   ██║██║  ██║██╔══╝    ██║     ██║   ██║██║  ██║██╔══╝  [/bold cyan]
[bold cyan]╚██████╔╝██████╔╝███████╗██╗╚██████╗╚██████╔╝██████╔╝███████╗[/bold cyan]
[bold cyan] ╚═════╝ ╚═════╝ ╚══════╝╚═╝ ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝[/bold cyan]

[white]Welcome to[/white] [bold cyan]Jude Code[/bold cyan] [white]— your terminal AI coding assistant[/white]

[dim]Type your message below and press[/dim] [bold green]Enter[/bold green][dim] to send.[/dim]
[dim]You can keep typing while the AI works — messages are queued.[/dim]
"""


class OutputLog(RichLog):
    """The main scrolling output pane."""


class StatusPanel(Static):
    """Sidebar panel: AI status + prompt queue."""


class JudeCodeTUI(App):
    """The Jude Code Textual application."""

    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    #body {
        height: 1fr;
    }

    #output {
        width: 3fr;
        height: 100%;
        border: round $primary;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 0 1;
        background: $surface;
        scrollbar-color: $primary;
    }

    #sidebar {
        width: 32;
        height: 100%;
        border: round $accent 50%;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 1;
        background: $panel;
    }

    #input-row {
        height: auto;
        padding: 0 0;
    }

    #prompt {
        width: auto;
        content-align: center middle;
        color: $accent;
        text-style: bold;
        padding: 0 1;
    }

    #message {
        border: tall $primary;
        background: $boost;
    }
    #message:focus {
        border: tall $accent;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "stop_or_quit", "Stop / Quit", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear_conv", "Clear", priority=True),
        Binding("escape", "stop_agent", "Stop", show=False),
    ]

    # ── reactive state shown in the sidebar ──
    ai_busy: reactive[bool] = reactive(False)
    current_msg: reactive[str] = reactive("")

    def __init__(self) -> None:
        super().__init__()
        self.api_client = create_api_client()
        self.agent = AgentEngine(SYSTEM_PROMPT, self.api_client)

        self.input_queue: "asyncio.Queue[str]" = asyncio.Queue()
        self.queued_previews: list[str] = []
        self._agent_loop_task: Optional[asyncio.Task] = None
        self._stream_buffer: str = ""
        self._quit_armed = False

    # ── Layout ──────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            log = OutputLog(id="output", wrap=True, markup=True, highlight=True)
            log.border_title = "💬  Output"
            yield log
            sb = StatusPanel(id="sidebar")
            sb.border_title = "📋  Status & Queue"
            yield sb
        with Horizontal(id="input-row"):
            yield Static("⏵", id="prompt")
            yield Input(
                placeholder="Type a message…  (Enter = send, /help for commands)",
                id="message",
            )
        yield Footer()

    # ── Lifecycle ───────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.title = "Jude Code"
        self.sub_title = f"{PROVIDER.upper()} · {MODEL}"

        # Route every console.print(...) from the engine into our output log.
        set_console_sink(self._console_sink)

        log = self.query_one("#output", OutputLog)
        log.write(Text.from_markup(WELCOME))

        self._render_sidebar()
        self.query_one("#message", Input).focus()

        # Background task that consumes the queue and runs the agent.
        self._agent_loop_task = asyncio.create_task(self._agent_loop())

    async def on_unmount(self) -> None:
        set_console_sink(None)
        if self._agent_loop_task and not self._agent_loop_task.done():
            self._agent_loop_task.cancel()
        try:
            await self.api_client.close()
        except Exception:
            pass

    # ── Console sink: engine output → RichLog ───────────────────────────

    def _console_sink(self, *args: Any, **kwargs: Any) -> None:
        """Receive console.print(...) calls from the agent engine.

        Thread-safe: if a tool prints from a worker thread, marshal the call
        back onto Textual's event loop so widget updates stay on the UI thread.
        """
        import threading

        if threading.current_thread() is not threading.main_thread():
            try:
                self.call_from_thread(self._console_sink_ui, *args, **kwargs)
            except Exception:
                pass
            return
        self._console_sink_ui(*args, **kwargs)

    def _console_sink_ui(self, *args: Any, **kwargs: Any) -> None:
        """Render console.print(...) on the UI thread.

        Handles streamed output (``end=""``) by buffering until a newline so
        that partial tokens render smoothly inside the RichLog.
        """
        end = kwargs.get("end", "\n")

        renderables: list[Any] = []
        text_only = True
        for a in args:
            if not isinstance(a, str):
                text_only = False
                break

        if text_only:
            # Join string args the way Console.print would (space separated).
            piece = " ".join(str(a) for a in args)
            self._stream_buffer += piece
            if end != "":
                self._stream_buffer += "\n"

            # Flush any complete lines to the log.
            if "\n" in self._stream_buffer:
                *lines, rest = self._stream_buffer.split("\n")
                self._stream_buffer = rest
                for ln in lines:
                    self._write_markup(ln)
            return

        # Non-string renderable (Panel, Markdown, Group, …): flush buffer first.
        self._flush_stream_buffer()
        log = self.query_one("#output", OutputLog)
        if len(args) == 1:
            log.write(args[0])
        else:
            log.write(Group(*args))

    def _flush_stream_buffer(self) -> None:
        if self._stream_buffer:
            buf = self._stream_buffer
            self._stream_buffer = ""
            self._write_markup(buf)

    def _write_markup(self, line: str) -> None:
        log = self.query_one("#output", OutputLog)
        try:
            log.write(Text.from_markup(line))
        except MarkupError:
            log.write(Text(line))

    # ── Sidebar rendering ───────────────────────────────────────────────

    def watch_ai_busy(self, _old: bool, _new: bool) -> None:
        self._render_sidebar()

    def watch_current_msg(self, _old: str, _new: str) -> None:
        self._render_sidebar()

    @staticmethod
    def _trunc(text: str, n: int) -> str:
        text = text.replace("\n", " ").strip()
        return text if len(text) <= n else text[: n - 1] + "…"

    def _render_sidebar(self) -> None:
        try:
            sb = self.query_one("#sidebar", StatusPanel)
        except Exception:
            return

        t = Text()
        # ── AI state ──
        t.append("AI STATUS\n", style="bold cyan")
        if self.ai_busy:
            t.append("  ● ", style="bold yellow")
            t.append("WORKING\n", style="bold yellow")
            if self.current_msg:
                t.append(f"  “{self._trunc(self.current_msg, 24)}”\n", style="italic dim")
        else:
            t.append("  ● ", style="bold green")
            t.append("READY\n", style="bold green")

        t.append("\n")
        # ── Queue ──
        n = len(self.queued_previews)
        t.append("PROMPT QUEUE", style="bold cyan")
        t.append(f"  ({n})\n", style="dim")
        if n == 0:
            t.append("  — empty —\n", style="dim italic")
        else:
            for i, p in enumerate(self.queued_previews[:8], 1):
                t.append(f"  {i}. ", style="bold magenta")
                t.append(f"{self._trunc(p, 22)}\n", style="white")
            if n > 8:
                t.append(f"  +{n - 8} more…\n", style="dim")

        t.append("\n")
        # ── Connection ──
        t.append("CONNECTION\n", style="bold cyan")
        t.append("  Provider ", style="dim")
        t.append(f"{PROVIDER.upper()}\n", style="green")
        t.append("  Model ", style="dim")
        t.append(f"{self._trunc(MODEL, 20)}\n", style="cyan")

        t.append("\n")
        # ── Hints ──
        t.append("SHORTCUTS\n", style="bold cyan")
        t.append("  /help  ", style="magenta")
        t.append("commands\n", style="dim")
        t.append("  ^L  ", style="magenta")
        t.append("clear   ", style="dim")
        t.append("^C  ", style="magenta")
        t.append("stop\n", style="dim")

        sb.update(t)

    # ── Input handling ──────────────────────────────────────────────────

    @on(Input.Submitted, "#message")
    async def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        inp = self.query_one("#message", Input)
        inp.value = ""
        if not text:
            return

        if text.startswith("/") or text.lower() in ("quit", "exit", ":q"):
            await self._handle_command(text)
            return

        # Echo the user message into the output pane.
        self._echo_user(text)

        self.queued_previews.append(text)
        await self.input_queue.put(text)
        self._render_sidebar()

    def _echo_user(self, text: str) -> None:
        log = self.query_one("#output", OutputLog)
        block = Text()
        block.append("\n⏵ You\n", style="bold green")
        block.append(text + "\n", style="white")
        log.write(block)

    # ── Agent loop ──────────────────────────────────────────────────────

    async def _agent_loop(self) -> None:
        while True:
            try:
                message = await self.input_queue.get()
            except asyncio.CancelledError:
                break

            self.ai_busy = True
            self.current_msg = message
            if self.queued_previews:
                self.queued_previews.pop(0)
            self._render_sidebar()

            try:
                self.agent.reset_stop()
                await self.agent.chat(message)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                self._console_sink(f"\n  [bold red]Error:[/bold red] {e}\n")
            finally:
                self._flush_stream_buffer()
                self.ai_busy = False
                self.current_msg = ""
                self.input_queue.task_done()
                self._render_sidebar()

    # ── Commands ────────────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> None:
        c = cmd.lower().strip()

        if c in ("/quit", "/exit", ":q", "quit", "exit"):
            self.exit()
            return

        if c == "/help":
            self._console_sink(
                "\n[bold cyan]Commands[/bold cyan]\n"
                "  [magenta]/help[/magenta]      show this help\n"
                "  [magenta]/quit[/magenta]      exit Jude Code\n"
                "  [magenta]/clear[/magenta]     clear conversation (or Ctrl+L)\n"
                "  [magenta]/stop[/magenta]      pause the agent (or Ctrl+C / Esc)\n"
                "  [magenta]/queue[/magenta]     show pending prompt queue\n"
                "  [magenta]/continue[/magenta]  trigger a continuation\n"
                "  [magenta]/status[/magenta]    continuation status\n"
                "  [magenta]/model[/magenta]     show model info\n"
            )
            return

        if c == "/clear":
            self.action_clear_conv()
            return

        if c == "/model":
            self._console_sink(
                f"\n  Provider: [green]{PROVIDER.upper()}[/green]  "
                f"Model: [cyan]{MODEL}[/cyan]  API: [dim]{BASE_URL}[/dim]"
            )
            return

        if c == "/queue":
            n = len(self.queued_previews)
            self._console_sink(f"\n  [bold]Queue:[/bold] {n} pending")
            for i, p in enumerate(self.queued_previews[:10], 1):
                self._console_sink(f"    {i}. {self._trunc(p, 60)}")
            return

        if c in ("/stop", "/pause"):
            self.action_stop_agent()
            return

        if c == "/continue":
            if self.ai_busy:
                self._console_sink("\n  [dim]Agent is already running.[/dim]")
            elif self.agent.continuation.can_continue():
                self.ai_busy = True
                self.current_msg = "(manual continuation)"
                self._render_sidebar()
                try:
                    await self.agent.continue_task()
                finally:
                    self.ai_busy = False
                    self.current_msg = ""
                    self._flush_stream_buffer()
                    self._render_sidebar()
            else:
                self._console_sink("\n  [red]Max continuations reached.[/red]")
            return

        if c == "/status":
            ct = self.agent.continuation
            self._console_sink(
                f"\n  Max continuations: {ct.max_continuations}  |  Used: {ct.count}"
            )
            return

        self._console_sink(f"\n  [dim]Unknown command: {cmd}  (try /help)[/dim]")

    # ── Actions ─────────────────────────────────────────────────────────

    def action_stop_agent(self) -> None:
        if self.ai_busy:
            self.agent.request_stop()
            self._console_sink("\n  [yellow]⏸ Stop requested…[/yellow]")
        else:
            self._console_sink("\n  [dim]Agent is not running.[/dim]")

    def action_stop_or_quit(self) -> None:
        """Ctrl+C: stop the agent if busy, otherwise quit."""
        if self.ai_busy:
            self.agent.request_stop()
            self._console_sink("\n  [yellow]⏸ Stop requested (Ctrl+C again or Ctrl+Q to quit)…[/yellow]")
        else:
            self.exit()

    def action_clear_conv(self) -> None:
        if self.ai_busy:
            self._console_sink("\n  [yellow]⚠ Cannot clear while the agent is busy.[/yellow]")
            return
        self.agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.agent.reset_stop()
        self.agent._turn_count = 0
        self.queued_previews.clear()
        log = self.query_one("#output", OutputLog)
        log.clear()
        log.write(Text.from_markup(WELCOME))
        self._render_sidebar()


def run_tui() -> None:
    """Launch the Textual TUI."""
    JudeCodeTUI().run()


if __name__ == "__main__":
    run_tui()
