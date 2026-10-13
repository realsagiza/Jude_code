"""Help must reflect UI availability and remain readable through Rich."""
import asyncio
from types import SimpleNamespace

from rich.text import Text

from judecode.ui.command_help import is_help_command, render_help
from judecode.ui.tui_app import JudeCodeTUI


def plain(mode, command):
    return Text.from_markup(render_help(mode, command)).plain


def test_help_distinguishes_current_commands_from_legacy():
    current = plain('tui', '/help')
    assert '/sessions' in current and '/copy' in current
    assert '/rollback' not in current
    all_commands = plain('tui', '/help all')
    assert '/rollback [step] [ใช้ได้เฉพาะ Legacy (--legacy)]' in all_commands
    assert '/daemon start เป้าหมาย' in all_commands
    assert '/sessions' not in plain('classic', '/help')


def test_search_aliases_and_markup_are_literal():
    assert '/stop' in plain('tui', '/help /pause')
    assert 'ไม่พบคำสั่ง' in plain('tui', '/help [unknown]')
    assert '[unknown]' in plain('tui', '/help [unknown]')
    assert is_help_command(' /HELP   resume ')
    assert is_help_command('/commands')
    assert is_help_command('/?')
    assert not is_help_command('/helpful')


def test_tui_help_dispatch_does_not_require_agent_or_execute_tools():
    output = []
    ui = SimpleNamespace(_console_sink=output.append)
    for cmd in ['/help', '/help all', '/help resume', '/commands', '/?']:
        asyncio.run(JudeCodeTUI._handle_command(ui, cmd))
    assert len(output) == 5
    assert '/resume latest' in Text.from_markup(output[2]).plain
