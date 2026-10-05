"""Exercise actual mouse selection in the output widget without an API client."""
import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Input
from judecode.ui.tui_app import JudeCodeTUI, OutputLog


class SelectionApp(App):
    BINDINGS = JudeCodeTUI.BINDINGS
    action_stop_or_quit = JudeCodeTUI.action_stop_or_quit
    action_copy_selection = JudeCodeTUI.action_copy_selection
    ai_busy = False

    def __init__(self):
        super().__init__()
        self.copied = None

    def compose(self) -> ComposeResult:
        yield OutputLog(id="output", wrap=True)
        yield Input(id="message")

    def _copy_text(self, text, what):
        self.copied = text


@pytest.mark.asyncio
async def test_drag_highlights_and_ctrl_c_copies_without_quitting():
    app = SelectionApp()
    async with app.run_test(size=(80, 24)) as pilot:
        log = app.query_one(OutputLog)
        log.write(Text("hello world", style="green"))
        log.write("second line")
        await pilot.pause()
        await pilot.mouse_down("#output", offset=(0, 0))
        await pilot.hover("#output", offset=(4, 0))
        await pilot.mouse_up("#output", offset=(4, 0))
        await pilot.pause()
        assert app.screen.get_selected_text() == "hello"
        selection_style = app.screen.get_component_rich_style("screen--selection")
        assert any(segment.style.bgcolor == selection_style.bgcolor
                   for segment in log.render_line(0) if segment.style)
        await pilot.press("ctrl+c")
        assert app.copied == "hello"
        assert app.is_running
        log.write("new streaming output")
        await pilot.pause()
        assert app.screen.get_selected_text() == "hello"


@pytest.mark.asyncio
async def test_multiline_selection_and_scrolled_output():
    app = SelectionApp()
    async with app.run_test(size=(80, 24)) as pilot:
        log = app.query_one(OutputLog)
        for i in range(40):
            log.write(f"line {i:02d} Thai ภาษาไทย")
        await pilot.pause()
        log.scroll_to(y=10, animate=False)
        await pilot.pause()
        y = int(log.scroll_offset.y)
        await pilot.mouse_down("#output", offset=(0, 0))
        await pilot.hover("#output", offset=(6, 1))
        await pilot.mouse_up("#output", offset=(6, 1))
        await pilot.pause()
        assert app.screen.get_selected_text() == f"line {y:02d} Thai ภาษาไทย\nline {y + 1:02d}"
