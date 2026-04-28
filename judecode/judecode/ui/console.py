"""Shared Rich console instance.

Use this console for all Rich output so everything works consistently
across the terminal UI and the agent engine.
"""

from rich.console import Console

console = Console(force_terminal=True, force_interactive=True)
