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
    SYSTEM_PROMPT, MODEL, BASE_URL, MAX_CONTINUATIONS, PROVIDER,
)
from judecode.api import create_api_client
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

    # ── Check for incomplete sessions (crash recovery) ──
    try:
        from judecode.agent.autonomous import SessionState
        incomplete = SessionState.find_incomplete_sessions()
        if incomplete:
            console.print("\n  [bold yellow]🔄 Incomplete sessions detected:[/bold yellow]")
            for s in incomplete[-3:]:  # Show last 3
                console.print(
                    f"    • {s.session_id} — {s.status} — "
                    f"{len(s.completed_tasks)} tasks done — "
                    f"Goal: {s.original_goal[:60]}..."
                )
            console.print(
                "  [dim]Type /resume <session_id> to continue, or start a new task.[/dim]\n"
            )
    except Exception:
        pass  # Don't block startup if session check fails


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

    api_client = create_api_client()
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
  [bold]/help[/bold]           - Show this help
  [bold]/quit[/bold]           - Exit Jude Code
  [bold]/clear[/bold]          - Clear the conversation history
  [bold]/compact[/bold]        - Compact conversation (save tokens)
  [bold]/model[/bold]          - Show current model info
  [bold]/continue[/bold]       - Manually trigger continuation
  [bold]/stop[/bold]           - Pause the agent (same as Ctrl+C)
  [bold]/status[/bold]         - Show continuation + autonomous status
  [bold]/budget[/bold]         - Show budget usage and circuit breaker

[bold cyan]Persistence & Recovery:[/bold cyan]
  [bold]/resume[/bold]         - Resume an incomplete session
  [bold]/checkpoint[/bold]     - Show checkpoint history
  [bold]/rollback[/bold]       - Rollback to checkpoint (e.g. /rollback 3)
  [bold]/diff[/bold]           - Show diff between checkpoint and current
  [bold]/decisions[/bold]      - Decision log (/decisions search <query>)
  [bold]/memory[/bold]         - Cross-session memory (/memory patterns|sessions)

[bold cyan]Safety & Control:[/bold cyan]
  [bold]/sandbox[/bold]        - Toggle sandbox mode (preview before apply)
  [bold]/sandbox apply[/bold]  - Apply sandboxed changes
  [bold]/sandbox discard[/bold] - Discard sandboxed changes
  [bold]/permissions[/bold]    - Show permission levels
  [bold]/permissions set[/bold] - Set permission (/permissions set delete=ask)

[bold cyan]Daemon & Automation:[/bold cyan]
  [bold]/daemon[/bold]         - Show daemon status
  [bold]/daemon start[/bold]   - Start background daemon with goal
  [bold]/daemon stop[/bold]    - Stop daemon
  [bold]/daemon logs[/bold]    - Show daemon logs
  [bold]/notify[/bold]         - Test notification system

[bold]Ctrl+C[/bold]            - Pause agent / exit if idle
[bold]Ctrl+C twice[/bold]      - Force exit
""")
                continue

            if user_input.lower() == "/clear":
                agent.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                agent.reset_stop()
                agent._turn_count = 0
                console.print("  [dim]Conversation cleared[/dim]\n", style="cyan")
                continue

            if user_input.lower() == "/compact":
                # Compact conversation: keep system + last few exchanges,
                # summarize the rest into a single context message
                if len(agent.messages) <= 6:
                    console.print("  [dim]Conversation is short, no need to compact.[/dim]\n", style="cyan")
                    continue

                # Build summary of old messages
                old_msgs = agent.messages[1:-4]  # skip system, keep last 4
                summary_parts = []
                for msg in old_msgs:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if not content:
                        continue
                    if role == "user":
                        # Keep nudge messages short
                        if content.strip().startswith("[SYSTEM:"):
                            continue
                        summary_parts.append(f"User asked: {content[:200]}")
                    elif role == "assistant":
                        summary_parts.append(f"Assistant did: {content[:200]}")
                    elif role == "tool":
                        summary_parts.append(f"Tool result: {content[:100]}...")

                if summary_parts:
                    summary = "Previous conversation summary:\n" + "\n".join(
                        f"- {p}" for p in summary_parts[-15:]  # Keep last 15 items max
                    )
                    # Rebuild messages: system + summary + last 4
                    last_4 = agent.messages[-4:]
                    agent.messages = [
                        agent.messages[0],  # system prompt
                        {"role": "user", "content": "[CONTEXT SUMMARY] " + summary},
                        {"role": "assistant", "content": "Understood, I have the context summary. Ready to continue."},
                        *last_4,
                    ]
                else:
                    # Just keep system + last 4
                    agent.messages = [agent.messages[0]] + agent.messages[-4:]

                agent._turn_count = 0
                console.print(
                    f"  [dim]Conversation compacted "
                    f"({len(old_msgs)} old messages → summary)[/dim]\n",
                    style="cyan",
                )
                continue

            if user_input.lower() == "/model":
                console.print(f"  [dim]Provider: {PROVIDER.upper()}[/dim]", style="bold green")
                console.print(f"  [dim]Model: {MODEL}[/dim]", style="cyan")
                console.print(f"  [dim]API: {BASE_URL}[/dim]\n", style="dim")
                continue

            if user_input.lower() == "/budget":
                console.print(f"\n  [bold yellow]💰 Token Budget Report:[/bold yellow]")
                console.print(agent.autonomous.budget.get_status())
                console.print()
                continue

            if user_input.lower() == "/checkpoint":
                from judecode.agent.checkpoint import CheckpointManager
                cp = CheckpointManager(session_id=agent.autonomous.session.session_id)
                console.print(f"\n  [bold green]📦 Checkpoint Status:[/bold green]")
                console.print(cp.get_summary())
                console.print()
                continue

            if user_input.lower().startswith("/rollback"):
                from judecode.agent.checkpoint import CheckpointManager
                parts = user_input.strip().split(maxsplit=1)
                step_num = None
                if len(parts) > 1:
                    try:
                        step_num = int(parts[1])
                    except ValueError:
                        console.print("  [red]Usage: /rollback [step_number][/red]\n")
                        continue
                cp = CheckpointManager(session_id=agent.autonomous.session.session_id)
                result = cp.rollback(step=step_num)
                if result["success"]:
                    console.print(f"\n  [bold green]✅ Rolled back to step {result['rolled_back_to']}[/bold green]")
                    for r in result.get("restored", []):
                        console.print(f"    {r['action']}: {r['path']}")
                    if result.get("errors"):
                        for e in result["errors"]:
                            console.print(f"    [red]error: {e['path']}: {e['error']}[/red]")
                else:
                    console.print(f"  [red]❌ {result.get('error', 'Rollback failed')}[/red]")
                console.print()
                continue

            if user_input.lower().startswith("/diff"):
                from judecode.agent.checkpoint import CheckpointManager
                parts = user_input.strip().split(maxsplit=1)
                step_num = None
                file_path = None
                if len(parts) > 1:
                    try:
                        step_num = int(parts[1])
                    except ValueError:
                        file_path = parts[1]
                cp = CheckpointManager(session_id=agent.autonomous.session.session_id)
                diff = cp.get_diff(step=step_num, file_path=file_path)
                console.print(f"\n  [bold cyan]📝 Diff:[/bold cyan]")
                console.print(diff)
                console.print()
                continue

            if user_input.lower().startswith("/decisions"):
                from judecode.agent.memory import DecisionLog
                parts = user_input.strip().split(maxsplit=1)
                dl = DecisionLog(session_id=agent.autonomous.session.session_id)
                if len(parts) > 1 and parts[1] == "search":
                    # /decisions search <query>
                    query_parts = user_input.strip().split(maxsplit=2)
                    query = query_parts[2] if len(query_parts) > 2 else ""
                    if query:
                        results = dl.search(query)
                        console.print(f"\n  [bold cyan]🔍 Search results for '{query}':[/bold cyan]")
                        for r in results:
                            console.print(f"    #{r['id']}: {r['task']} → {r['strategy']} ({r['result']})")
                    else:
                        console.print("  [dim]Usage: /decisions search <query>[/dim]")
                else:
                    console.print(f"\n  [bold cyan]📝 Decision Log:[/bold cyan]")
                    console.print(dl.get_summary())
                    entries = dl.get_entries(limit=5)
                    for e in entries:
                        icon = "✅" if e.get("result") == "pass" else "❌" if e.get("result") == "fail" else "⏳"
                        console.print(f"    {icon} #{e['id']}: {e['task']} → {e['strategy']}")
                        if e.get("learnings"):
                            console.print(f"       💡 {e['learnings']}")
                console.print()
                continue

            if user_input.lower().startswith("/memory"):
                from judecode.agent.memory import CrossSessionMemory
                mem = CrossSessionMemory()
                parts = user_input.strip().split(maxsplit=1)
                subcmd = parts[1] if len(parts) > 1 else ""

                if subcmd.startswith("patterns"):
                    patterns = mem.get_successful_patterns()
                    failed = mem.get_failed_patterns()
                    console.print(f"\n  [bold green]✅ Successful patterns ({len(patterns)}):[/bold green]")
                    for p in patterns[:5]:
                        console.print(f"    • {p['pattern']} — {p['context']}")
                    console.print(f"\n  [bold red]❌ Failed patterns ({len(failed)}):[/bold red]")
                    for p in failed[:5]:
                        console.print(f"    • {p['pattern']} — {p['context']}")
                elif subcmd.startswith("sessions"):
                    summaries = mem.get_session_summaries()
                    console.print(f"\n  [bold cyan]📋 Recent sessions ({len(summaries)}):[/bold cyan]")
                    for s in summaries:
                        rate = s.get('completion_rate', 0)
                        console.print(f"    • {s['session_id']}: {rate:.0%} complete — {s['goal'][:50]}")
                else:
                    console.print(f"\n  [bold cyan]🧠 Cross-Session Memory:[/bold cyan]")
                    console.print(f"    /memory patterns — Show learned patterns")
                    console.print(f"    /memory sessions — Show session history")
                console.print()
                continue

            if user_input.lower() == "/sandbox":
                if agent.sandbox.is_active:
                    result = agent.sandbox.deactivate()
                else:
                    result = agent.sandbox.activate()
                console.print(f"\n  {result}\n")
                continue

            if user_input.lower() == "/sandbox apply":
                result = agent.sandbox.apply_all()
                console.print(f"\n  🧪 Applied {result['applied']} change(s)")
                if result['errors']:
                    console.print(f"  ❌ {result['errors']} error(s)")
                console.print()
                continue

            if user_input.lower() == "/sandbox discard":
                result = agent.sandbox.discard_all()
                console.print(f"\n  {result}\n")
                continue

            if user_input.lower() == "/permissions":
                console.print(f"\n  {agent.permissions.get_permissions_summary()}\n")
                continue

            if user_input.lower().startswith("/permissions set"):
                # /permissions set delete=ask
                parts = user_input.strip().split()
                if len(parts) >= 3:
                    try:
                        category, level = parts[2].split("=")
                        agent.permissions.set_permission(category, level)
                        console.print(f"  ✅ Set {category} = {level}\n")
                    except ValueError:
                        console.print("  [red]Usage: /permissions set <category>=<auto|ask|deny>[/red]\n")
                else:
                    console.print("  [dim]Usage: /permissions set delete=ask[/dim]\n")
                continue

            if user_input.lower() == "/daemon":
                from judecode.agent.daemon import DaemonManager
                dm = DaemonManager()
                status = dm.get_status()
                icon = "🟢" if status.get("running") else "🔴"
                console.print(f"\n  {icon} Daemon: {'Running' if status.get('running') else 'Stopped'}")
                if status.get("goal"):
                    console.print(f"  🎯 Goal: {status['goal']}")
                if status.get("pid"):
                    console.print(f"  🔢 PID: {status['pid']}")
                console.print(f"  📋 Logs: {status.get('log_file', 'N/A')}")
                console.print()
                continue

            if user_input.lower().startswith("/daemon start"):
                from judecode.agent.daemon import DaemonManager
                parts = user_input.strip().split(maxsplit=2)
                goal = parts[2] if len(parts) > 2 else ""
                if not goal:
                    console.print("  [red]Usage: /daemon start <goal>[/red]\n")
                else:
                    dm = DaemonManager()
                    result = dm.start(goal=goal)
                    console.print(f"\n  {result}\n")
                continue

            if user_input.lower() == "/daemon stop":
                from judecode.agent.daemon import DaemonManager
                dm = DaemonManager()
                result = dm.stop()
                console.print(f"\n  {result}\n")
                continue

            if user_input.lower().startswith("/daemon logs"):
                from judecode.agent.daemon import DaemonManager
                dm = DaemonManager()
                logs = dm.get_logs(lines=30)
                console.print(f"\n  [bold cyan]📋 Daemon Logs:[/bold cyan]")
                console.print(logs)
                continue

            if user_input.lower() == "/notify":
                result = agent.notifications.notify("JudeCode Test", "Notification system is working!")
                console.print(f"\n  🔔 Notification results: {result}\n")
                continue

            if user_input.lower().startswith("/resume"):
                parts = user_input.strip().split(maxsplit=1)
                session_id = parts[1] if len(parts) > 1 else ""
                if not session_id:
                    # List incomplete sessions
                    from judecode.agent.autonomous import SessionState
                    incomplete = SessionState.find_incomplete_sessions()
                    if incomplete:
                        console.print("\n  [bold yellow]📋 Incomplete sessions:[/bold yellow]")
                        for s in incomplete[-5:]:
                            console.print(
                                f"    • {s.session_id} — {s.status} — "
                                f"{len(s.completed_tasks)} tasks done"
                            )
                        console.print("  [dim]Usage: /resume <session_id>[/dim]\n")
                    else:
                        console.print("  [dim]No incomplete sessions found.[/dim]\n")
                else:
                    from judecode.agent.autonomous import SessionState
                    session = SessionState.load(session_id.strip())
                    if session:
                        agent.autonomous.session = session
                        console.print(f"\n  [bold green]✅ Session {session_id} loaded![/bold green]")
                        console.print(session.get_progress_summary())
                        console.print()
                        # Nudge agent to continue
                        nudge = (
                            f"[SYSTEM: Resuming session {session_id}. "
                            f"Original goal: {session.original_goal}. "
                            f"Completed {len(session.completed_tasks)} tasks. "
                            f"Current task: #{session.current_task_id}. "
                            f"Please continue from where we left off.]"
                        )
                        agent_running = True
                        try:
                            await agent.chat(nudge)
                        finally:
                            agent_running = False
                    else:
                        console.print(f"  [red]❌ Session '{session_id}' not found.[/red]\n")
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
                # ── Autonomous Mode Status ──
                console.print(f"\n  [bold magenta]🤖 Autonomous Mode:[/bold magenta]")
                console.print(agent.autonomous.get_status())
                console.print(f"\n  [bold yellow]💰 {agent.autonomous.budget.get_compact_status()}[/bold yellow]")
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
    """Entry point for `judecode` command.

    v2.1 — Default: async mode.
    """
    import sys

    # DEBUG: uncomment to verify version
    # print("DEBUG: terminal.py v2.1 async-default", file=sys.stderr)

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
            print("  --classic       Use the classic scrolling async UI")
            print("  --legacy        Use the legacy single-thread UI")
            print()
            print("Default: Beautiful full-screen TUI (Textual).")
            return
        if arg == "--legacy":
            asyncio.run(run_agent_interactive())
            return
        if arg == "--classic":
            from judecode.ui.async_terminal import main_cli as async_main
            async_main()
            return

    # Default: beautiful Textual TUI
    try:
        from judecode.ui.tui_app import run_tui
        run_tui()
    except Exception as e:
        # Fall back to the classic async UI if the TUI can't start
        console.print(f"[yellow]TUI failed to start ({e}); falling back to classic UI.[/yellow]")
        from judecode.ui.async_terminal import main_cli as async_main
        async_main()


if __name__ == "__main__":
    main_cli()
