"""Terminal UI for Jude Code - with cool greeting and interactive loop.

Supports long multi-line input via prompt_toolkit with:
- Multi-line editing (Alt+Enter for new line, Enter to submit)
- Syntax highlighting
- Command history (up/down arrows)
- Vi/Emacs key bindings
- Auto-indent
"""

import asyncio
import sys
import os

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

# ── prompt_toolkit for long input support ──
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.lexers import PygmentsLexer
    from prompt_toolkit.styles import Style as PTKStyle
    from prompt_toolkit.key_binding import KeyBindings
    from pygments.lexers import PythonLexer
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


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
    tips.append(" or ", style="white")
    tips.append("Ctrl+D", style="bold magenta")
    tips.append(" to exit\n", style="white")
    tips.append("Type ", style="white")
    tips.append("/clear", style="bold magenta")
    tips.append(" to clear conversation\n", style="white")
    tips.append("Press ", style="white")
    tips.append("Enter", style="bold green")
    tips.append(" to submit, ", style="white")
    tips.append("Alt+Enter", style="bold green")
    tips.append(" for new line", style="white")

    tips_panel = Panel(
        tips,
        title="[bold]Commands[/bold]",
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
    api_client = ApiClient()
    agent = AgentEngine(SYSTEM_PROMPT, api_client)

    print_greeting()

    # ── Setup prompt_toolkit for long input ──
    if PROMPT_TOOLKIT_AVAILABLE:
        # Custom key bindings: Enter = submit, Alt+Enter = new line
        kb = KeyBindings()

        @kb.add("enter")
        def _(event):
            """Enter submits the text. Alt+Enter inserts new line."""
            if event.is_alt:
                event.current_buffer.insert_text("\n")
            else:
                event.current_buffer.validate_and_handle()

        ptk_style = PTKStyle.from_dict({
            "prompt": "bold cyan",
        })

        session = PromptSession(
            history=InMemoryHistory(),
            style=ptk_style,
            key_bindings=kb,
            enable_history_search=True,
            multiline=True,
            lexer=None,
            wrap_lines=True,
            complete_while_typing=False,
        )
    else:
        session = None

    try:
        while True:
            # ── Get user input (long text supported) ──
            try:
                if session:
                    prompt_text = "\n  \u254b[bold cyan]\u256d[/bold cyan] "
                    user_input = await session.prompt_async(prompt_text, default="")
                else:
                    console.print("\n  \u254b[bold cyan]\u256d[/bold cyan] ", end="")
                    user_input = input()
            except (EOFError, KeyboardInterrupt):
                print_goodbye()
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            # Commands
            if user_input.lower() in ("/quit", "/exit", ":q", "quit", "exit"):
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
  [bold]/status[/bold]    - Show continuation status and history
  [bold]Ctrl+D[/bold]     - Exit

You can type any question or request.
The agent can use tools like shell, read, write, edit, grep, web_search, etc.
""")
                continue

            if user_input.lower() == "/clear":
                agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
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

            if user_input.lower() == "/continue":
                if agent.continuation.can_continue():
                    await agent.continue_task()
                else:
                    console.print(
                        "\n  [bold red]Max continuations reached. Start a new task or clear the conversation.[/bold red]\n"
                    )
                continue

            # Normal message - process through agent
            # Thinking indicator is now handled inside agent.chat() for every turn

            try:
                await agent.chat(user_input)
            except Exception as e:
                console.print(f"\n  [bold red]Error:[/bold red] {e}\n")

    finally:
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
