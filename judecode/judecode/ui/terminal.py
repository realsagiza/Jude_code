"""Terminal UI for Jude Code - with cool greeting and interactive loop.

Supports long multi-line input with plain Python input():
- Enter twice (blank line) to submit
- No external dependencies needed

Interrupt/Pause support:
- Ctrl+C during agent execution → pauses the agent (does NOT exit)
- /stop command → pauses the agent after current action
- When paused, type a new message to redirect, or /continue to resume
- Ctrl+C while idle (waiting for input) → normal exit
"""

import asyncio
import sys
import os
import signal

from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.align import Align
from rich.rule import Rule
from rich.box import DOUBLE_EDGE, HEAVY_EDGE

from judecode.config import (
    SYSTEM_PROMPT, MODEL, BASE_URL, MAX_CONTINUATIONS,
)
from judecode.api.client import ApiClient
from judecode.agent.engine import AgentEngine
from judecode.ui.console import console


def safe_input(prompt: str = "") -> str:
    """Wrapper around input() that handles encoding errors gracefully.

    Falls back to reading raw bytes + replacing invalid UTF-8 sequences
    if the built-in input() raises UnicodeDecodeError.
    """
    try:
        return input(prompt)
    except UnicodeDecodeError:
        # If stdin contains invalid UTF-8 bytes, fall back to reading raw bytes
        # and decoding with error replacement
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


def print_greeting() -> None:
    """Print a cool greeting similar to Claude Code."""
    from rich.columns import Columns
    from rich.rule import Rule

    # Cool ASCII-style text
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
    tips.append("Press ", style="white")
    tips.append("Enter twice", style="bold green")
    tips.append(" (blank line) to submit multi-line text", style="white")

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


def print_thinking() -> None:
    """Print a thinking indicator."""
    console.print("\n  [dim]⚡ Thinking...[/dim]\n", end="")


def print_tool_call(tool_name: str) -> None:
    """Print when a tool is called."""
    console.print(f"\n  [dim]\u279c Executing [/dim][bold magenta]{tool_name}[/bold magenta]", end="\n\n")


def print_response_header() -> None:
    """Print a response header."""
    console.print("\n[bold cyan]\u2022\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500[/bold cyan]\n")


def print_response_footer() -> None:
    """Print a response footer."""
    console.print("\n[dim cyan]\u2022\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500[/dim cyan]\n")


def render_markdown(text: str) -> Markdown:
    """Render markdown text. Used for final output if not streaming."""
    return Markdown(text)


def get_input_prompt() -> str:
    """Return the styled input prompt."""
    return "\u250c\u2500\u2500 [bold cyan]\u254b[/bold cyan] [white]\u256d[/white] ".replace("\u250c", "\u250c")


async def run_agent_interactive() -> None:
    """Main interactive agent loop with multi-line input support."""
    # ── Ensure stdin handles encoding errors gracefully ──
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass  # stdin might not be reconfigureable in some environments

    api_client = ApiClient()
    agent = AgentEngine(SYSTEM_PROMPT, api_client)

    # ── Interrupt/Pause signal handling ──
    # Track whether the agent is currently running
    agent_running = False

    def handle_sigint(sig, frame):
        """Handle Ctrl+C: pause agent if running, exit if idle."""
        nonlocal agent_running
        if agent_running:
            agent.request_stop()
            console.print(
                "  [bold yellow](Press Ctrl+C again to force exit)[/bold yellow]"
            )
        else:
            # Exit if idle (waiting for input)
            console.print()
            print_goodbye()
            os._exit(0)

    # Install the custom SIGINT handler
    original_sigint = signal.signal(signal.SIGINT, handle_sigint)

    print_greeting()

    try:
        while True:
            # ── Get user input (long text supported) ──
            try:
                console.print("\n  \u254b[bold cyan]\u256d[/bold cyan] ", end="")
                loop = asyncio.get_running_loop()
                first_line = await loop.run_in_executor(None, safe_input)
                if not first_line:
                    continue

                # Check if it's a single-line command
                first_stripped = first_line.strip()
                is_command = first_stripped.startswith("/") or first_stripped in (
                    "quit", "exit", ":q"
                )

                if is_command:
                    user_input = first_stripped
                else:
                    # Multi-line: read more lines until blank line
                    lines = [first_line]
                    while True:
                        try:
                            line = await loop.run_in_executor(None, safe_input)
                            if not line:
                                break
                            lines.append(line)
                        except (EOFError, KeyboardInterrupt):
                            raise
                    user_input = "\n".join(lines)
            except (EOFError, KeyboardInterrupt):
                print_goodbye()
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # ── Commands ──
            if user_input.lower() in ("/quit", "/exit", ":q"):
                print_goodbye()
                break

            if user_input.lower() == "/help":
                console.print("""
[bold cyan]Jude Code Commands:[/bold cyan]
  [bold]/help[/bold]      - Show this help
  [bold]/quit[/bold]      - Exit Jude Code
  [bold]/clear[/bold]     - Clear the conversation history
  [bold]/model[/bold]     - Show current model info
  [bold]/continue[/bold]  - Manually trigger continuation (nudge agent to continue)
  [bold]/stop[/bold]      - Pause the agent after current action (same as Ctrl+C)
  [bold]/pause[/bold]     - Alias for /stop
  [bold]/status[/bold]    - Show continuation status and history
  [bold]Ctrl+C[/bold]     - Pause agent if running, exit if idle
  [bold]Ctrl+C twice[/bold] - Force exit

You can type any question or request.
For multi-line input, just press Enter twice (blank line) to send.
The agent can use tools like shell, read, write, edit, grep, web_search, etc.
""")
                continue

            if user_input.lower() == "/clear":
                agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                agent.reset_stop()
                agent._turn_count = 0
                console.print("  [dim]Conversation cleared[/dim]\n", style="cyan")
                continue

            if user_input.lower() == "/model":
                console.print(f"  [dim]Model: {MODEL}[/dim]", style="cyan")
                console.print(f"  [dim]API: {BASE_URL}[/dim]\n", style="dim")
                continue

            if user_input.lower() == "/status":
                ct = agent.continuation
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
                continue

            if user_input.lower() in ("/stop", "/pause"):
                if agent_running:
                    agent.request_stop()
                    console.print(
                        "  [bold yellow]Stop requested. Waiting for current action to finish...[/bold yellow]"
                    )
                else:
                    console.print(
                        "  [dim]Agent is not currently running.[/dim]\n"
                    )
                continue

            if user_input.lower() == "/continue":
                if agent.continuation.can_continue():
                    agent_running = True
                    try:
                        await agent.continue_task()
                    finally:
                        agent_running = False
                else:
                    console.print(
                        "\n  [bold red]Max continuations reached. Start a new task or clear the conversation.[/bold red]\n"
                    )
                continue

            # ── Normal message - process through agent ──
            agent_running = True
            try:
                await agent.chat(user_input)
            except Exception as e:
                console.print(f"\n  [bold red]Error:[/bold red] {e}\n")
            finally:
                agent_running = False

    finally:
        # Restore original SIGINT handler
        signal.signal(signal.SIGINT, original_sigint)
        await api_client.close()


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
            print("  --version, -v   Show version and exit")
            print("  --help, -h      Show this help message and exit")
            print()
            print("Without arguments, starts the interactive chat session.")
            return

    asyncio.run(run_agent_interactive())


if __name__ == "__main__":
    main_cli()
