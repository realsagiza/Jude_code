"""Async Terminal UI for Jude Code — concurrent input/output.

แยก input และ output ออกเป็นคนละ asyncio task ทำให้ผู้ใช้สามารถพิมพ์ต่อไปได้
ระหว่างที่ AI กำลังทำงาน โดยข้อความจะถูกเก็บในคิวและประมวลผลตามลำดับ

Architecture:
  ┌──────────────────────┐     ┌──────────────────────┐
  │   Input Task         │     │   Agent Task          │
  │                      │     │                       │
  │  while running:      │     │  while running:       │
  │    msg = read_input()│────▶│    msg = queue.get()  │
  │    queue.put(msg)    │     │    agent.chat(msg)    │
  │                      │     │                       │
  └──────────────────────┘     └──────────────────────┘
           │                            │
           └────────────┬───────────────┘
                        │
                 ┌──────▼──────┐
                 │  Console     │
                 │  (Rich UI)   │
                 └──────────────┘

Features:
- พิมพ์ข้อความใหม่ได้ขณะ AI ยังทำงานอยู่ (ใส่คิว)
- แสดงจำนวนข้อความที่รอในคิว
- Ctrl+C หยุด agent ชั่วคราว, Ctrl+C อีกครั้งออกจากโปรแกรม
- /stop, /pause หยุด agent ชั่วคราว
- /queue แสดงสถานะคิว
- /continue ให้ agent ทำงานต่อ
"""

import asyncio
import os
import signal
import sys

from rich.panel import Panel
from rich.text import Text
from rich.align import Align
from rich.rule import Rule
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
    """Print a cool greeting similar to Claude Code."""
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

    # Info panel
    info = Text()
    info.append(f"Provider: ", style="dim")
    info.append(f"{PROVIDER.upper()}\n", style="bold green")
    info.append(f"Model: ", style="dim")
    info.append(f"{MODEL}\n", style="bold cyan")
    info.append(f"API: ", style="dim")
    info.append(f"{BASE_URL}\n", style="dim blue")
    info.append(f"Continuation: ", style="dim")
    info.append(f"✓ Enabled", style="bold green")
    info.append(f" (max {MAX_CONTINUATIONS} nudges)", style="dim")

    panel = Panel(
        info,
        title="[bold]Connection[/bold]",
        border_style="cyan",
        box=HEAVY_EDGE,
    )
    console.print(panel)

    # Tips panel
    tips = Text()
    tips.append("Type ", style="white")
    tips.append("/help", style="bold magenta")
    tips.append(" for available commands\n", style="white")
    tips.append("Type ", style="white")
    tips.append("/quit", style="bold magenta")
    tips.append(" or Ctrl+C when idle to exit\n", style="white")
    tips.append("Type ", style="white")
    tips.append("/clear", style="bold magenta")
    tips.append(" to clear conversation\n", style="white")
    tips.append("Type ", style="white")
    tips.append("/stop", style="bold magenta")
    tips.append(" or Ctrl+C while running to pause agent\n", style="white")
    tips.append("Type ", style="white")
    tips.append("/queue", style="bold magenta")
    tips.append(" to see pending messages\n", style="white")
    tips.append("💡 ", style="yellow")
    tips.append("You can type while AI is working — messages will queue!", style="bold green")

    tips_panel = Panel(
        tips,
        title="[bold]Commands & Tips[/bold]",
        border_style="dim",
        box=DOUBLE_EDGE,
    )
    console.print(tips_panel)
    console.print()
    console.print(Rule(style="dim cyan"))
    console.print()


def print_goodbye() -> None:
    """Print exit message."""
    console.print()
    console.print("  ✨ [dim]See you next time.[/dim]", style="cyan")
    console.print()


# ── Concurrent Agent Runner ─────────────────────────────────────────────────

class AsyncAgentRunner:
    """Runs the agent loop concurrently with the input loop.

    Uses asyncio.Queue to pass messages from the input task to the agent task.
    This allows the user to type continuously while the AI processes previous
    messages in the background.
    """

    def __init__(self):
        self.api_client = create_api_client()
        self.agent = AgentEngine(SYSTEM_PROMPT, self.api_client)

        # Shared state
        self.input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.output_lock = asyncio.Lock()
        self.agent_busy = False
        self.running = True
        self._quit_requested = False  # Track /quit confirmation

        # Tasks
        self._input_task: asyncio.Task | None = None
        self._agent_task: asyncio.Task | None = None

    # ── Prompt helpers ──────────────────────────────────────────────────

    # Separator character and style for input/output boundary
    _SEP_CHAR = "▬"
    _SEP_WIDTH = 70  # characters

    def _print_input_separator(self) -> None:
        """Print a clear separator line between output area and input area."""
        # Clear separation: blank line + dashed line + status
        console.print()
        sep_line = self._SEP_CHAR * self._SEP_WIDTH
        if self.agent_busy:
            qsize = self.input_queue.qsize()
            if qsize > 0:
                label = f" [⏳ AI working · {qsize} queued] "
            else:
                label = " [⏳ AI working] "
            console.print(f"  [dim cyan]{sep_line}[/dim cyan]")
            console.print(f"  [dim cyan]▐[/dim cyan][bold yellow]{label}[/bold yellow][dim cyan]{self._SEP_CHAR * (self._SEP_WIDTH - len(label) - 2)}[/dim cyan]")
        else:
            console.print(f"  [dim cyan]{sep_line}[/dim cyan]")
            console.print(f"  [dim cyan]▐ [bold cyan]INPUT[/bold cyan] {self._SEP_CHAR * (self._SEP_WIDTH - 13)}[/dim cyan]")

    def _get_prompt_text(self) -> str:
        """Get the plain-text input prompt."""
        return "  ⏵ "

    # ── Input loop (runs continuously) ──────────────────────────────────

    async def input_loop(self) -> None:
        """Continuously read user input and queue it for the agent.

        Runs as its own asyncio task. Reads from stdin via executor
        (to avoid blocking the event loop). Handles commands immediately.
        """
        loop = asyncio.get_event_loop()

        while self.running:
            # Print separator to clearly separate output from input
            self._print_input_separator()
            prompt_text = self._get_prompt_text()
            try:
                line = await loop.run_in_executor(None, safe_input, prompt_text)
            except (EOFError, KeyboardInterrupt):
                continue

            if not line:
                continue

            line = line.strip()
            if not line:
                continue

            # Handle commands immediately (don't queue)
            if line.startswith("/") or line.lower() in ("quit", "exit", ":q"):
                handled = await self._handle_command(line)
                if not handled:
                    continue  # Command was handled but keep running
                if handled == "quit":
                    break  # Quit command
                continue

            # Queue the message for the agent
            self._quit_requested = False  # Reset quit confirmation
            await self.input_queue.put(line)

    # ── Agent loop (processes queue) ────────────────────────────────────

    async def agent_loop(self) -> None:
        """Process messages from the queue one at a time.

        Runs as its own asyncio task. Pops messages from the queue
        and processes them through the agent engine.
        """
        while self.running:
            try:
                # Wait for a message (short timeout to check running flag)
                message = await asyncio.wait_for(
                    self.input_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue

            self.agent_busy = True
            try:
                # Reset stop flag for new message
                self.agent.reset_stop()
                await self.agent.chat(message)
            except Exception as e:
                console.print(f"\n  [bold red]Error:[/bold red] {e}\n")
            finally:
                self.agent_busy = False
                self.input_queue.task_done()
                # Print output-end separator so input area stands out
                console.print()
                console.print(f"  [dim cyan]{self._SEP_CHAR * self._SEP_WIDTH}[/dim cyan]")
                console.print("  [dim cyan]▐ [dim]END OUTPUT[/dim][/dim cyan]")
                console.print()

    # ── Command handler ─────────────────────────────────────────────────

    async def _handle_command(self, cmd: str) -> str | None:
        """Handle slash commands. Returns 'quit' to exit, None to continue."""
        cmd_lower = cmd.lower().strip()

        # Reset quit confirmation on any non-quit command
        if cmd_lower not in ("/quit", "/exit", ":q", "quit", "exit"):
            self._quit_requested = False

        # ── Quit ──
        if cmd_lower in ("/quit", "/exit", ":q", "quit", "exit"):
            # First /quit while busy: warn and set flag
            if self.agent_busy and not self._quit_requested:
                self._quit_requested = True
                qsize = self.input_queue.qsize()
                if qsize > 0:
                    console.print(
                        f"\n  [bold yellow]⚠ Agent is busy with {qsize} queued message(s). "
                        "Type /quit again to force quit.[/bold yellow]"
                    )
                else:
                    console.print(
                        "\n  [bold yellow]⚠ Agent is busy. "
                        "Type /quit again to force quit.[/bold yellow]"
                    )
                return None

            # Second /quit, or agent is idle → actually quit
            self.running = False
            self._quit_requested = False
            # If agent is busy, request stop so it doesn't hang
            if self.agent_busy:
                self.agent.request_stop()
            return "quit"

        # ── Help ──
        if cmd_lower == "/help":
            console.print("""
[bold cyan]Jude Code Commands:[/bold cyan]
  [bold]/help[/bold]      - Show this help
  [bold]/quit[/bold]      - Exit Jude Code
  [bold]/clear[/bold]     - Clear the conversation history
  [bold]/model[/bold]     - Show current model info
  [bold]/continue[/bold]  - Manually trigger continuation
  [bold]/stop[/bold]      - Pause the agent after current action (same as Ctrl+C)
  [bold]/pause[/bold]     - Alias for /stop
  [bold]/status[/bold]    - Show continuation status and history
  [bold]/queue[/bold]     - Show pending message queue
  [bold]Ctrl+C[/bold]     - Pause agent if running, exit if idle
  [bold]Ctrl+C twice[/bold] - Force exit

💡 [bold green]New![/bold green] You can type new messages while the AI is working.
   Messages will be queued and processed in order.
""")
            return None

        # ── Clear conversation ──
        if cmd_lower == "/clear":
            if self.agent_busy:
                console.print(
                    "\n  [bold yellow]⚠ Cannot clear while agent is busy. "
                    "Use /stop first.[/bold yellow]"
                )
                return None
            self.agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            self.agent.reset_stop()
            self.agent._turn_count = 0
            console.print("  [dim]Conversation cleared[/dim]\n", style="cyan")
            return None

        # ── Model info ──
        if cmd_lower == "/model":
            console.print(f"\n  [dim]Provider: {PROVIDER.upper()}[/dim]", style="bold green")
            console.print(f"  [dim]Model: {MODEL}[/dim]", style="cyan")
            console.print(f"  [dim]API: {BASE_URL}[/dim]\n", style="dim")
            return None

        # ── Status ──
        if cmd_lower == "/status":
            ct = self.agent.continuation
            console.print(f"\n  [bold cyan]Continuation Status:[/bold cyan]")
            console.print(f"    Max continuations: [bold]{ct.max_continuations}[/bold]")
            console.print(f"    Used: [bold]{ct.count}[/bold]")
            console.print(f"    Stream error recovery: [bold]{'✓' if ct.continue_on_stream_error else '✗'}[/bold]")
            console.print(f"    Incomplete work detection: [bold]{'✓' if ct.continue_on_incomplete_work else '✗'}[/bold]")
            console.print(f"    Tool error recovery: [bold]{'✓' if ct.continue_on_tool_error else '✗'}[/bold]")
            if ct.history:
                console.print(f"\n    [dim]History:[/dim]")
                for h in ct.history:
                    console.print(f"      #{h['count']}: {h['reason']} at {h['timestamp'][:19]}")
            else:
                console.print(f"\n    [dim]No continuations triggered yet.[/dim]")
            console.print()
            return None

        # ── Queue status ──
        if cmd_lower == "/queue":
            qsize = self.input_queue.qsize()
            console.print(f"\n  [bold cyan]Message Queue:[/bold cyan]")
            console.print(f"    Pending messages: [bold]{qsize}[/bold]")
            console.print(f"    Agent status: [bold]{'🔵 Busy' if self.agent_busy else '🟢 Idle'}[/bold]")
            console.print()
            return None

        # ── Stop / Pause ──
        if cmd_lower in ("/stop", "/pause"):
            if self.agent_busy:
                self.agent.request_stop()
                console.print(
                    "\n  [bold yellow]⏸ Stop requested. "
                    "Waiting for current action to finish...[/bold yellow]"
                )
            else:
                console.print(
                    "\n  [dim]Agent is not currently running.[/dim]\n"
                )
            return None

        # ── Continue ──
        if cmd_lower == "/continue":
            if self.agent_busy:
                console.print(
                    "\n  [dim]Agent is already running.[/dim]\n"
                )
            elif self.agent.continuation.can_continue():
                self.agent_busy = True
                try:
                    await self.agent.continue_task()
                except Exception as e:
                    console.print(f"\n  [bold red]Error:[/bold red] {e}\n")
                finally:
                    self.agent_busy = False
            else:
                console.print(
                    "\n  [bold red]Max continuations reached. "
                    "Start a new task or clear the conversation.[/bold red]\n"
                )
            return None

        # ── Unknown command ──
        console.print(
            f"\n  [dim]Unknown command: {cmd}. Type /help for available commands.[/dim]\n"
        )
        return None

    # ── Main runner ─────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main entry point — starts input and agent tasks concurrently."""
        # Ensure stdin handles encoding errors gracefully
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

        # ── Signal handling ──
        def handle_sigint(sig, frame):
            """Ctrl+C: pause agent if running, exit if idle."""
            if self.agent_busy:
                self.agent.request_stop()
                console.print(
                    "\n  [bold yellow](Press Ctrl+C again to force exit)[/bold yellow]"
                )
            else:
                console.print()
                print_goodbye()
                self.running = False
                os._exit(0)

        original_sigint = signal.signal(signal.SIGINT, handle_sigint)

        print_greeting()

        # Print initial separator for clean start
        console.print(f"  [dim cyan]{'▬' * 70}[/dim cyan]")
        console.print(f"  [dim cyan]▐ [bold cyan]START[/bold cyan] {'▬' * 55}[/dim cyan]")
        console.print()

        try:
            # Start both tasks
            self._agent_task = asyncio.create_task(self.agent_loop())
            self._input_task = asyncio.create_task(self.input_loop())

            # Wait for input task to finish (user types /quit)
            await self._input_task

        finally:
            # Cleanup
            signal.signal(signal.SIGINT, original_sigint)
            self.running = False

            # Cancel agent task if still running
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

    # Handle --version and --help flags
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
            print("Without arguments, starts the interactive chat session")
            print("with concurrent input/output (Type while AI works!)")
            return
        if arg == "--legacy":
            # Fall back to legacy single-thread mode
            from judecode.ui.terminal import main_cli as legacy_main
            legacy_main()
            return

    runner = AsyncAgentRunner()
    asyncio.run(runner.run())


if __name__ == "__main__":
    main_cli()
