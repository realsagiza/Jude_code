"""Terminal UI for Jude Code - with cool greeting and interactive loop."""

import asyncio
import sys

from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text
from rich.align import Align
from rich.rule import Rule
from rich.box import DOUBLE_EDGE, HEAVY_EDGE

from judecode.config import SYSTEM_PROMPT, MODEL, BASE_URL
from judecode.api.client import ApiClient
from judecode.agent.engine import AgentEngine
from judecode.ui.console import console


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
    info.append(f"{BASE_URL}", style="dim blue")

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
    tips.append(" to clear conversation", style="white")

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
    """Main interactive agent loop."""
    api_client = ApiClient()
    agent = AgentEngine(SYSTEM_PROMPT, api_client)

    print_greeting()

    try:
        while True:
            # Print prompt
            try:
                console.print("\n\u254b[bold cyan]\u256d[/bold cyan] ", end="")
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
  [bold]/help[/bold]   - Show this help
  [bold]/quit[/bold]   - Exit Jude Code
  [bold]/clear[/bold]  - Clear the conversation history
  [bold]/model[/bold]  - Show current model info
  [bold]Ctrl+D[/bold]  - Exit

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

            # Normal message - process through agent
            print_thinking()

            try:
                await agent.chat(user_input)
            except Exception as e:
                console.print(f"\n  [bold red]Error:[/bold red] {e}\n")

    finally:
        await api_client.close()


def main_cli() -> None:
    """Entry point for `judecode` command."""
    asyncio.run(run_agent_interactive())


if __name__ == "__main__":
    main_cli()
