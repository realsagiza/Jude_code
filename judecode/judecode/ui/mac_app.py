"""Native macOS app for Jude Code (PyObjC / AppKit).

This module provides a real native Cocoa UI — no Electron, no Tkinter.
It is designed to be frozen with PyInstaller into a ``.app`` bundle and
double-clicked from the Finder.

Layout
------
* Main window: chat output (NSTextView) + multi-line input + Send button.
* Settings sheet: choose provider (DeepSeek / Anthropic / Z.AI), edit API
  keys & models, optional vision API. Saved to
  ``~/Library/Application Support/JudeCode/config.env``.

Threading
---------
The agent engine is async, so it runs on a dedicated background thread that
owns its own asyncio event loop. The main thread runs the NSApplication
event loop. Output is marshalled between threads via a thread-safe
``queue.Queue``; the main thread drains it on a 20 ms timer.
"""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
import traceback
from typing import Any, Optional

import objc  # noqa: E402  — must come before AppKit for super_init
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSWindow,
    NSView,
    NSTextView,
    NSScrollView,
    NSTextField,
    NSButton,
    NSPopUpButton,
    NSSecureTextField,
    NSStackView,
    NSGridView,
    NSColor,
    NSFont,
    NSFontManager,
    NSAttributedString,
    NSMutableAttributedString,
    NSRange,
    NSBezelBorder,
    NSLayoutConstraint,
    NSLayoutAttributeTop,
    NSLayoutAttributeBottom,
    NSLayoutAttributeLeading,
    NSLayoutAttributeTrailing,
    NSLayoutAttributeWidth,
    NSLayoutAttributeHeight,
    NSLayoutAttributeCenterY,
    NSLayoutAttributeCenterX,
    NSLayoutRelationEqual,
    NSLayoutConstraintOrientationHorizontal,
    NSLayoutConstraintOrientationVertical,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSBackingStoreBuffered,
    NSEvent,
    NSEventModifierFlagCommand,
    NSEventTypeKeyDown,
    NSAlert,
    NSAlertStyleInformational,
    NSAlertStyleWarning,
    NSAlertFirstButtonReturn,
    NSImage,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSMenu,
    NSMenuItem,
    NSWorkspace,
    NSURL,
    NSPanel,
    NSBox,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSUnderlineStyleAttributeName,
)
from Foundation import (
    NSObject,
    NSMutableDictionary,
    NSMutableString,
    NSRunLoop,
    NSTimer,
    NSDefaultRunLoopMode,
    NSString,
    NSNotification,
)

from judecode.ui.mac_ansi import ansi_to_attributed_string
from judecode.ui.mac_config import (
    load_config,
    save_config,
    has_api_key_for_provider,
    PROVIDERS,
    PROVIDER_LABELS,
    PROVIDER_FIELDS,
    VISION_FIELDS,
    CONFIG_PATH,
)


# ── Colours ────────────────────────────────────────────────────────────
#
# We use **system colors** (NSColor.controlTextColor, textBackgroundColor,
# etc.) so the UI adapts automatically to the user's light/dark mode and
# always has correct contrast. Hard-coded RGB values would look fine in one
# mode but become unreadable in the other.

def _sys(name: str) -> NSColor:
    """Get an adaptive system color by name (cached lookup)."""
    return getattr(NSColor, name)()


# Lazy references — resolved at first use so they pick up the current
# appearance (light/dark) rather than capturing it at import time.
def _col_bg() -> NSColor:
    return NSColor.textBackgroundColor()

def _col_bg_panel() -> NSColor:
    return NSColor.controlBackgroundColor()

def _col_fg() -> NSColor:
    return NSColor.textColor()

def _col_fg_dim() -> NSColor:
    return NSColor.secondaryLabelColor()

def _col_accent() -> NSColor:
    return NSColor.controlAccentColor()

def _col_input_bg() -> NSColor:
    return NSColor.textBackgroundColor()

def _col_error() -> NSColor:
    return NSColor.systemRedColor()

def _col_success() -> NSColor:
    return NSColor.systemGreenColor()

def _col_user_bubble() -> NSColor:
    return NSColor.controlAccentColor()

def _col_user_fg() -> NSColor:
    return NSColor.textBackgroundColor()


# ── Helper: build a font ───────────────────────────────────────────────

def _font(size: float, bold: bool = False, mono: bool = True) -> NSFont:
    if mono:
        family = "Menlo"
    else:
        family = ".AppleSystemUIFont" if not bold else ".AppleSystemUIFontBold"
    if mono:
        font = NSFont.fontWithName_size_("Menlo", size)
        if font is None:
            font = NSFont.fontWithName_size_("SF Mono", size)
        if font is None:
            font = NSFont.fontWithName_size_("Monaco", size)
        if font is None:
            font = NSFont.systemFontOfSize_(size)
        if bold:
            fm = NSFontManager.sharedFontManager()
            traits = 0x02  # NSBoldFontMask
            new_font = fm.convertFont_hasTraits_(font, traits)
            if new_font is not None:
                font = new_font
        return font
    if bold:
        return NSFont.boldSystemFontOfSize_(size)
    return NSFont.systemFontOfSize_(size)


# ── Output message bus ─────────────────────────────────────────────────

class _OutputBus:
    """Thread-safe queue of (text, kind) tuples.

    ``kind`` is one of:
      - "ansi"   : Rich output with ANSI escape codes (rendered with colours)
      - "plain"  : Plain text (no styling)
      - "user"   : Echo of the user's own message (rendered as a bubble)
      - "system" : Small grey system line
      - "error"  : Red error text
    """

    def __init__(self):
        self._q: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def put(self, text: str, kind: str = "ansi") -> None:
        if text:
            self._q.put((text, kind))

    def drain(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        while True:
            try:
                items.append(self._q.get_nowait())
            except queue.Empty:
                break
        return items


# ── Agent worker (single-thread, cooperative asyncio) ──────────────────
#
# IMPORTANT: We deliberately avoid spawning a background ``threading.Thread``
# because PyInstaller's frozen loader can deadlock on the GIL when a new
# thread tries to import sub-modules that haven't been loaded yet on the
# main thread. Instead we run the asyncio loop *cooperatively* on the main
# thread: each NSTimer tick we pump the loop with a short timeout so the
# NSApplication event loop stays responsive.


class _AgentWorker:
    """Cooperative agent worker — runs asyncio on the main thread."""

    def __init__(self, output_bus: _OutputBus):
        self.output_bus = output_bus
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.engine: Optional[Any] = None
        self._ready = False
        self._stop = False
        self._busy = False
        self._pending: list[str] = []
        self._bootstrap_task: Optional[asyncio.Task] = None
        self._chat_task: Optional[asyncio.Task] = None
        self._rebuild_task: Optional[asyncio.Task] = None

    # ── Public API ──

    def start_and_wait(self, timeout: float = 0.0) -> bool:
        """Create the asyncio loop and kick off bootstrap.

        Returns immediately (the bootstrap runs cooperatively on the main
        thread via :meth:`pump`).
        """
        log = _logging.getLogger("judecode.mac.worker")
        log.info("start: creating asyncio loop")
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        log.info("start: scheduling bootstrap task")
        self._bootstrap_task = self.loop.create_task(self._bootstrap())
        log.info("start: done (bootstrap will run on pump)")
        return True

    def pump(self, timeout: float = 0.01) -> None:
        """Run the asyncio loop for up to ``timeout`` seconds.

        Call this from the NSTimer callback on the main thread so the
        agent's async work makes progress without blocking the UI.
        """
        if self.loop is None or self.loop.is_closed():
            return
        try:
            self.loop.run_until_complete(
                asyncio.wait_for(asyncio.sleep(0), timeout=timeout)
            )
        except asyncio.TimeoutError:
            pass
        except RuntimeError:
            # Loop may be already running or stopped — ignore.
            pass
        # If bootstrap finished, check for queued messages.
        if (
            self._ready
            and not self._busy
            and self._pending
            and self._chat_task is None
        ):
            msg = self._pending.pop(0)
            self._busy = True
            self._chat_task = self.loop.create_task(self._run_chat(msg))

    def send_message(self, text: str) -> None:
        self._pending.append(text)

    def request_stop(self) -> None:
        self._stop = True

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def is_ready(self) -> bool:
        return self._ready

    # ── Async coroutines ──

    async def _bootstrap(self) -> None:
        log = _logging.getLogger("judecode.mac.worker")
        log.info("bootstrap: importing judecode")
        try:
            from judecode.api import create_api_client
            from judecode.agent.engine import AgentEngine
            from judecode.config import SYSTEM_PROMPT, PROVIDER, MODEL

            log.info("bootstrap: provider=%s model=%s", PROVIDER, MODEL)
            self.output_bus.put(
                f"Connecting to {PROVIDER.upper()} · {MODEL}\n", "system"
            )
            client = create_api_client()
            self.engine = AgentEngine(system_prompt=SYSTEM_PROMPT, api_client=client)
            self._ready = True
            log.info("bootstrap: done, engine ready")
            self.output_bus.put("✓ Ready — type a message below.\n\n", "system")
        except Exception:
            tb = traceback.format_exc()
            log.exception("bootstrap failed")
            self.output_bus.put(f"Failed to start agent:\n{tb}\n", "error")

    async def _run_chat(self, user_message: str) -> None:
        log = _logging.getLogger("judecode.mac.worker")
        if self.engine is None:
            self.output_bus.put("Agent not ready yet — please wait.\n", "error")
            self._busy = False
            self._chat_task = None
            return
        try:
            log.info("chat: starting (%d chars)", len(user_message))
            await self.engine.chat(user_message)
            self.output_bus.put("\n", "plain")
            log.info("chat: done")
        except Exception:
            tb = traceback.format_exc()
            log.exception("chat crashed")
            self.output_bus.put(f"\n[Chat error]\n{tb}\n", "error")
        finally:
            self._busy = False
            self._chat_task = None

    def rebuild_engine(self) -> None:
        """Re-create the API client + engine with the current env config."""
        if self.loop is None or self.loop.is_closed():
            return
        log = _logging.getLogger("judecode.mac.worker")
        log.info("scheduling rebuild")
        self._rebuild_task = self.loop.create_task(self._rebuild_async())

    async def _rebuild_async(self) -> None:
        try:
            import importlib
            import judecode.config as _cfg
            importlib.reload(_cfg)
            from judecode.api import create_api_client
            from judecode.agent.engine import AgentEngine
            self.engine = AgentEngine(
                system_prompt=_cfg.SYSTEM_PROMPT,
                api_client=create_api_client(),
            )
            self._ready = True
            self.output_bus.put(
                f"✓ Ready — provider: {_cfg.PROVIDER.upper()}, "
                f"model: {_cfg.MODEL}\n",
                "system",
            )
        except Exception:
            tb = traceback.format_exc()
            self.output_bus.put(f"Failed to reload config:\n{tb}\n", "error")


# ── Console sink: route Rich output into the bus ───────────────────────

class _RichSink:
    """A Rich console sink that captures rendered ANSI text into the bus."""

    def __init__(self, bus: _OutputBus):
        self._bus = bus
        self._buf = []

    def __call__(self, *args, **kwargs) -> None:
        # Re-export with styles so we keep colours. We use a private Rich
        # Console instance to render to a string with ANSI codes intact.
        from rich.console import Console as _RichConsole
        tmp = _RichConsole(
            file=open(os.devnull, "w"),
            force_terminal=True,
            color_system="256",
            record=True,
            width=100,
            soft_wrap=True,
        )
        tmp.print(*args, **kwargs)
        text = tmp.export_text(styles=True)
        self._bus.put(text, "ansi")


def _install_rich_sink(bus: _OutputBus) -> None:
    """Install our sink into judecode.ui.console so all Rich output is captured."""
    try:
        from judecode.ui import console as _console_mod
        _console_mod.set_console_sink(_RichSink(bus))
    except Exception:
        # If we can't install the sink, the agent will still print to stdout
        # (which is hidden in a .app bundle) — not ideal but not fatal.
        pass


def _uninstall_rich_sink() -> None:
    try:
        from judecode.ui import console as _console_mod
        _console_mod.set_console_sink(None)
    except Exception:
        pass


# ── Application delegate ───────────────────────────────────────────────

class JudeCodeAppDelegate(NSObject):
    """Main application delegate + controller.

    Holds references to the main window, the worker thread, and the
    settings sheet. All UI callbacks are defined here as Objective-C
    selector methods (decorated with nothing — PyObjC discovers them by
    name via ``objc.selector``).
    """

    # ── Initialization ──

    def init(self):
        self = objc.super(JudeCodeAppDelegate, self).init()
        if self is None:
            return None
        self._bus = _OutputBus()
        self._worker = _AgentWorker(self._bus)
        self._window: Optional[NSWindow] = None
        self._output_view: Optional[NSTextView] = None
        self._input_view: Optional[NSTextView] = None
        self._send_button: Optional[NSButton] = None
        self._status_field: Optional[NSTextField] = None
        self._drain_timer: Optional[NSTimer] = None
        self._settings_panel: Optional[NSPanel] = None
        self._settings_fields: dict[str, Any] = {}
        self._provider_popup: Optional[NSPopUpButton] = None
        self._config_cache: dict[str, str] = {}
        self._auto_scroll = True
        return self

    # ── Application lifecycle ──

    def applicationDidFinishLaunching_(self, notification):
        log = _logging.getLogger("judecode.mac")
        log.info("applicationDidFinishLaunching")
        try:
            # Activate the app (bring to front) — important for .app bundles.
            app = NSApplication.sharedApplication()
            app.activateIgnoringOtherApps_(True)

            self._config_cache = load_config()
            log.info("config loaded: provider=%s", self._config_cache.get("JUDECODE_PROVIDER"))

            # Build the main window before starting the worker so the user sees
            # something immediately.
            log.info("building main window")
            self._build_main_window()
            log.info("main window built OK")
            self._build_menu_bar()
            log.info("menu bar built OK")

            # Install the Rich sink so agent output goes to our bus.
            _install_rich_sink(self._bus)

            # Start the agent worker thread.
            log.info("starting worker")
            if not self._worker.start_and_wait(timeout=15.0):
                self._bus.put(
                    "Agent worker did not start in time. Check your API key in Settings.\n",
                    "error",
                )
            log.info("worker ready=%s", self._worker.engine is not None)

            # Start the drain timer (20 ms cadence).
            self._drain_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.05, self, b"drainOutput:", None, True
            )
            NSRunLoop.currentRunLoop().addTimer_forMode_(
                self._drain_timer, NSDefaultRunLoopMode
            )
            log.info("drain timer started")

            # If no API key is configured, prompt the user to open Settings.
            provider = self._config_cache.get("JUDECODE_PROVIDER", "deepseek")
            if not has_api_key_for_provider(self._config_cache, provider):
                self._bus.put(
                    "⚠️  No API key configured for "
                    f"{PROVIDER_LABELS.get(provider, provider)}.\n"
                    "Open Settings (⌘,) to add your key.\n\n",
                    "system",
                )
            log.info("applicationDidFinishLaunching done")
        except Exception:
            log.exception("Error in applicationDidFinishLaunching")

    def applicationWillTerminate_(self, notification):
        _uninstall_rich_sink()
        self._worker.request_stop()
        if self._drain_timer is not None:
            self._drain_timer.invalidate()

    def applicationSupportsSecureRestorableState_(self, app):
        return True

    # ── Main window construction ──

    def _build_main_window(self) -> None:
        rect = ((100.0, 100.0), (960.0, 680.0))
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, style, NSBackingStoreBuffered, False
        )
        window.setTitle_("Jude Code")
        window.setMinSize_((640.0, 480.0))
        window.setBackgroundColor_(_col_bg())
        window.setOpaque_(False)
        window.setHasShadow_(True)
        window.center()
        window.makeKeyAndOrderFront_(None)
        window.setDelegate_(self)
        self._window = window

        content = window.contentView()

        # ── Output scroll view ──
        output_scroll = NSScrollView.alloc().initWithFrame_(((0, 0), (0, 0)))
        output_scroll.setHasVerticalScroller_(True)
        output_scroll.setAutohidesScrollers_(True)
        output_scroll.setBorderType_(NSBezelBorder)
        output_scroll.setDrawsBackground_(False)

        output_view = NSTextView.alloc().initWithFrame_(((0, 0), (0, 0)))
        output_view.setEditable_(False)
        output_view.setSelectable_(True)
        output_view.setRichText_(True)
        output_view.setDrawsBackground_(True)
        output_view.setBackgroundColor_(_col_bg())
        output_view.setTextColor_(_col_fg())
        output_view.setFont_(_font(13.0, mono=True))
        output_view.setTextContainerInset_((8.0, 8.0))
        output_view.setVerticallyResizable_(True)
        output_view.setHorizontallyResizable_(False)
        output_view.textContainer().setWidthTracksTextView_(True)
        output_view.textContainer().setContainerSize_((1.0e7, 1.0e7))
        output_scroll.setDocumentView_(output_view)
        self._output_view = output_view

        # ── Status field ──
        status = NSTextField.labelWithString_("")
        status.setTranslatesAutoresizingMaskIntoConstraints_(False)
        status.setTextColor_(_col_fg_dim())
        status.setFont_(_font(11.0, mono=False))
        status.setBackgroundColor_(_col_bg())
        status.setBezeled_(False)
        status.setDrawsBackground_(False)
        status.setStringValue_("Ready")
        self._status_field = status

        # ── Input area ──
        input_scroll = NSScrollView.alloc().initWithFrame_(((0, 0), (0, 0)))
        input_scroll.setHasVerticalScroller_(True)
        input_scroll.setAutohidesScrollers_(True)
        input_scroll.setBorderType_(NSBezelBorder)
        input_scroll.setDrawsBackground_(False)

        input_view = NSTextView.alloc().initWithFrame_(((0, 0), (0, 0)))
        input_view.setEditable_(True)
        input_view.setRichText_(False)
        input_view.setDrawsBackground_(True)
        input_view.setBackgroundColor_(_col_input_bg())
        input_view.setTextColor_(_col_fg())
        input_view.setFont_(_font(13.0, mono=True))
        input_view.setTextContainerInset_((6.0, 6.0))
        input_view.setVerticallyResizable_(True)
        input_view.setHorizontallyResizable_(False)
        input_view.textContainer().setWidthTracksTextView_(True)
        input_view.textContainer().setContainerSize_((1.0e7, 1.0e7))
        input_view.setDelegate_(self)
        input_scroll.setDocumentView_(input_view)
        self._input_view = input_view

        # ── Send button ──
        send = NSButton.buttonWithTitle_target_action_("Send", self, b"sendClicked:")
        send.setBezelStyle_(4)  # NSBezelStyleRounded
        send.setKeyEquivalent_("\\r")  # Enter (but we override in text view)
        send.setFont_(_font(13.0, bold=True, mono=False))
        # Use accent color via attributed title
        send.setControlSize_(1)  # NSControlSizeRegular
        self._send_button = send

        # ── Layout with Auto Layout ──
        for v in (output_scroll, status, input_scroll, send):
            v.setTranslatesAutoresizingMaskIntoConstraints_(False)
            content.addSubview_(v)

        constraints = [
            # Output: top, leading, trailing
            output_scroll.topAnchor().constraintEqualToAnchor_constant_(
                content.topAnchor(), 10.0),
            output_scroll.leadingAnchor().constraintEqualToAnchor_constant_(
                content.leadingAnchor(), 10.0),
            output_scroll.trailingAnchor().constraintEqualToAnchor_constant_(
                content.trailingAnchor(), -10.0),
            # Status: between output and input
            status.topAnchor().constraintEqualToAnchor_constant_(
                output_scroll.bottomAnchor(), 6.0),
            status.leadingAnchor().constraintEqualToAnchor_(
                output_scroll.leadingAnchor()),
            status.trailingAnchor().constraintEqualToAnchor_constant_(
                send.leadingAnchor(), -8.0),
            # Input: below status
            input_scroll.topAnchor().constraintEqualToAnchor_constant_(
                status.bottomAnchor(), 6.0),
            input_scroll.leadingAnchor().constraintEqualToAnchor_(
                output_scroll.leadingAnchor()),
            input_scroll.trailingAnchor().constraintEqualToAnchor_(
                output_scroll.trailingAnchor()),
            input_scroll.bottomAnchor().constraintEqualToAnchor_constant_(
                content.bottomAnchor(), -10.0),
            input_scroll.heightAnchor().constraintGreaterThanOrEqualToConstant_(60.0),
            input_scroll.heightAnchor().constraintLessThanOrEqualToConstant_(220.0),
            # Send button
            send.bottomAnchor().constraintEqualToAnchor_(
                input_scroll.topAnchor()),
            send.trailingAnchor().constraintEqualToAnchor_(
                output_scroll.trailingAnchor()),
            send.widthAnchor().constraintEqualToConstant_(90.0),
        ]
        for c in constraints:
            c.setActive_(True)

        # Make input first responder.
        window.makeFirstResponder_(input_view)

    # ── Menu bar ──

    def _build_menu_bar(self) -> None:
        app = NSApplication.sharedApplication()
        main_menu = NSMenu.alloc().init()

        # App menu
        app_menu = NSMenu.alloc().init()
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Jude Code", None, "")
        item.setSubmenu_(app_menu)
        main_menu.addItem_(item)

        app_menu.addItemWithTitle_action_keyEquivalent_(
            "About Jude Code", b"showAbout:", "")
        app_menu.addItem_(NSMenuItem.separatorItem())
        app_menu.addItemWithTitle_action_keyEquivalent_(
            "Settings…", b"showSettings:", ",")
        app_menu.addItem_(NSMenuItem.separatorItem())
        app_menu.addItemWithTitle_action_keyEquivalent_(
            "Hide", b"hide:", "h")
        app_menu.addItemWithTitle_action_keyEquivalent_(
            "Quit Jude Code", b"terminate:", "q")

        # Edit menu
        edit_menu = NSMenu.alloc().init()
        edit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Edit", None, "")
        edit_item.setSubmenu_(edit_menu)
        main_menu.addItem_(edit_item)
        edit_menu.addItemWithTitle_action_keyEquivalent_(
            "Undo", b"undo:", "z")
        edit_menu.addItemWithTitle_action_keyEquivalent_(
            "Redo", b"redo:", "Z")
        edit_menu.addItem_(NSMenuItem.separatorItem())
        edit_menu.addItemWithTitle_action_keyEquivalent_(
            "Cut", b"cut:", "x")
        edit_menu.addItemWithTitle_action_keyEquivalent_(
            "Copy", b"copy:", "c")
        edit_menu.addItemWithTitle_action_keyEquivalent_(
            "Paste", b"paste:", "v")
        edit_menu.addItemWithTitle_action_keyEquivalent_(
            "Select All", b"selectAll:", "a")

        # Action menu (Jude Code specific)
        action_menu = NSMenu.alloc().init()
        action_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Action", None, "")
        action_item.setSubmenu_(action_menu)
        main_menu.addItem_(action_item)
        action_menu.addItemWithTitle_action_keyEquivalent_(
            "Send Message", b"sendClicked:", "\\r")
        action_menu.addItemWithTitle_action_keyEquivalent_(
            "Clear Conversation", b"clearConversation:", "k")
        action_menu.addItem_(NSMenuItem.separatorItem())
        action_menu.addItemWithTitle_action_keyEquivalent_(
            "Open Settings…", b"showSettings:", ",")

        app.setMainMenu_(main_menu)

    # ── Settings sheet ──

    def showSettings_(self, sender):
        """Open the settings sheet (modal)."""
        if self._settings_panel is not None:
            # Already open — just bring to front.
            self._settings_panel.makeKeyAndOrderFront_(None)
            return

        self._config_cache = load_config()

        rect = ((0.0, 0.0), (560.0, 540.0))
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_("Jude Code Settings")
        panel.setMinSize_((520.0, 480.0))
        panel.setBackgroundColor_(_col_bg())
        panel.setOpaque_(False)
        panel.center()
        self._settings_panel = panel

        content = panel.contentView()
        self._settings_fields = {}

        # ── Header / instructions ──
        header = NSTextField.labelWithString_(
            "Configure your AI provider and API keys below. "
            "Your keys are stored locally and never sent anywhere except the provider you choose."
        )
        header.setTranslatesAutoresizingMaskIntoConstraints_(False)
        header.setTextColor_(_col_fg_dim())
        header.setFont_(_font(11.0, mono=False))
        header.setLineBreakMode_(0)  # NSLineBreakByWordWrapping
        header.setPreferredMaxLayoutWidth_(500.0)
        header.setMaximumNumberOfLines_(0)
        content.addSubview_(header)

        # ── Import .env button ──
        import_btn = NSButton.buttonWithTitle_target_action_(
            "Import from .env…", self, b"importFromEnv:")
        import_btn.setTranslatesAutoresizingMaskIntoConstraints_(False)
        import_btn.setBezelStyle_(1)  # rounded
        import_btn.setFont_(_font(11.0, mono=False))
        import_btn.setToolTip_(
            "Load API keys from a .env file (e.g. your project's .env)")
        content.addSubview_(import_btn)

        # Provider popup
        provider_label = NSTextField.labelWithString_("AI Provider:")
        provider_label.setTranslatesAutoresizingMaskIntoConstraints_(False)
        provider_label.setTextColor_(_col_fg())
        provider_label.setFont_(_font(13.0, bold=True, mono=False))
        provider_label.setAlignment_(2)  # NSRightTextAlignment
        content.addSubview_(provider_label)

        popup = NSPopUpButton.alloc().initWithFrame_pullsDown_(((0, 0), (0, 0)), False)
        popup.setTranslatesAutoresizingMaskIntoConstraints_(False)
        popup.addItemsWithTitles_(list(PROVIDER_LABELS.values()))
        current_provider = self._config_cache.get("JUDECODE_PROVIDER", "deepseek")
        try:
            idx = PROVIDERS.index(current_provider)
        except ValueError:
            idx = 0
        popup.selectItemAtIndex_(idx)
        popup.setTarget_(self)
        popup.setAction_(b"providerChanged:")
        popup.setFont_(_font(13.0, mono=False))
        content.addSubview_(popup)
        self._provider_popup = popup

        # Field grid container (we'll add rows dynamically).
        fields_container = NSStackView.alloc().initWithFrame_(((0, 0), (0, 0)))
        fields_container.setTranslatesAutoresizingMaskIntoConstraints_(False)
        fields_container.setOrientation_(1)  # NSUserInterfaceLayoutOrientationVertical
        fields_container.setAlignment_(4)  # NSLayoutAttributeLeading
        fields_container.setSpacing_(8.0)
        content.addSubview_(fields_container)
        self._fields_container = fields_container

        # Build the initial field rows.
        self._rebuild_provider_fields()

        # Buttons
        cancel_btn = NSButton.buttonWithTitle_target_action_(
            "Cancel", self, b"cancelSettings:")
        cancel_btn.setTranslatesAutoresizingMaskIntoConstraints_(False)
        cancel_btn.setKeyEquivalent_("\\e")
        cancel_btn.setFont_(_font(13.0, mono=False))
        content.addSubview_(cancel_btn)

        save_btn = NSButton.buttonWithTitle_target_action_(
            "Save", self, b"saveSettings:")
        save_btn.setTranslatesAutoresizingMaskIntoConstraints_(False)
        save_btn.setKeyEquivalent_("\\r")
        save_btn.setFont_(_font(13.0, bold=True, mono=False))
        save_btn.setBezelStyle_(4)
        content.addSubview_(save_btn)

        # Layout constraints
        constraints = [
            # Header at top
            header.topAnchor().constraintEqualToAnchor_constant_(
                content.topAnchor(), 16.0),
            header.leadingAnchor().constraintEqualToAnchor_constant_(
                content.leadingAnchor(), 20.0),
            header.trailingAnchor().constraintEqualToAnchor_constant_(
                content.trailingAnchor(), -20.0),

            # Import button below header
            import_btn.topAnchor().constraintEqualToAnchor_constant_(
                header.bottomAnchor(), 10.0),
            import_btn.trailingAnchor().constraintEqualToAnchor_constant_(
                content.trailingAnchor(), -20.0),

            # Provider row below import button
            provider_label.topAnchor().constraintEqualToAnchor_constant_(
                import_btn.bottomAnchor(), 16.0),
            provider_label.leadingAnchor().constraintEqualToAnchor_constant_(
                content.leadingAnchor(), 20.0),
            provider_label.widthAnchor().constraintEqualToConstant_(110.0),

            popup.centerYAnchor().constraintEqualToAnchor_(
                provider_label.centerYAnchor()),
            popup.leadingAnchor().constraintEqualToAnchor_constant_(
                provider_label.trailingAnchor(), 8.0),
            popup.trailingAnchor().constraintEqualToAnchor_constant_(
                content.trailingAnchor(), -20.0),

            fields_container.topAnchor().constraintEqualToAnchor_constant_(
                provider_label.bottomAnchor(), 18.0),
            fields_container.leadingAnchor().constraintEqualToAnchor_(
                content.leadingAnchor()),
            fields_container.trailingAnchor().constraintEqualToAnchor_(
                content.trailingAnchor()),

            cancel_btn.bottomAnchor().constraintEqualToAnchor_constant_(
                content.bottomAnchor(), -16.0),
            cancel_btn.trailingAnchor().constraintEqualToAnchor_constant_(
                save_btn.leadingAnchor(), -8.0),

            save_btn.bottomAnchor().constraintEqualToAnchor_(
                cancel_btn.bottomAnchor()),
            save_btn.trailingAnchor().constraintEqualToAnchor_constant_(
                content.trailingAnchor(), -20.0),

            fields_container.bottomAnchor().constraintLessThanOrEqualToAnchor_constant_(
                cancel_btn.topAnchor(), -12.0),
        ]
        for c in constraints:
            c.setActive_(True)

        NSApplication.sharedApplication().runModalForWindow_(panel)

    def importFromEnv_(self, sender):
        """Open a file picker to import keys from a .env file."""
        from AppKit import NSOpenPanel, NSFileHandlingPanelOKButton
        panel = NSOpenPanel.openPanel()
        panel.setTitle_("Select a .env file to import")
        panel.setAllowedFileTypes_(["env", "txt", ""])
        panel.setAllowsMultipleSelection_(False)
        panel.setCanChooseDirectories_(False)
        panel.setCanChooseFiles_(True)
        # Default to the project .env if it exists.
        from judecode.ui.mac_config import find_project_env
        default = find_project_env()
        if default is not None:
            panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(default.parent)))
        result = panel.runModal()
        if result != NSFileHandlingPanelOKButton:
            return
        url = panel.URL()
        if url is None:
            return
        path = url.path()
        from judecode.ui.mac_config import import_from_env_file
        from pathlib import Path
        imported = import_from_env_file(Path(path))
        if not imported:
            self._show_alert(
                "Nothing to import",
                f"No recognised keys were found in:\n{path}",
                NSAlertStyleWarning,
            )
            return
        # Merge into cache and refresh the fields.
        self._config_cache.update(imported)
        self._rebuild_provider_fields()
        self._show_alert(
            "Imported",
            f"Loaded {len(imported)} value(s) from:\n{path}\n\n"
            "Click Save to apply them.",
            NSAlertStyleInformational,
        )

    def _rebuild_provider_fields(self) -> None:
        """Rebuild the form fields based on the selected provider."""
        # Remove all existing arranged subviews.
        container = self._fields_container
        for sv in list(container.arrangedSubviews()):
            container.removeView_(sv)
        self._settings_fields = {}

        provider = self._current_provider()
        fields = PROVIDER_FIELDS[provider]

        for key, default, is_secret, label in fields:
            row = self._make_field_row(key, label, default, is_secret)
            container.addArrangedSubview_(row)

        # Separator + Vision section
        sep = NSBox.alloc().init()
        sep.setTranslatesAutoresizingMaskIntoConstraints_(False)
        sep.setBoxType_(2)  # NSBoxSeparator
        sep.setTransparent_(False)
        container.addArrangedSubview_(sep)

        vision_header = NSTextField.labelWithString_(
            "Vision API (optional — for screenshot analysis)")
        vision_header.setTranslatesAutoresizingMaskIntoConstraints_(False)
        vision_header.setTextColor_(_col_fg_dim())
        vision_header.setFont_(_font(11.0, mono=False))
        container.addArrangedSubview_(vision_header)

        for key, default, is_secret, label in VISION_FIELDS:
            row = self._make_field_row(key, label, default, is_secret)
            container.addArrangedSubview_(row)

        # Tavily
        sep2 = NSBox.alloc().init()
        sep2.setTranslatesAutoresizingMaskIntoConstraints_(False)
        sep2.setBoxType_(2)
        container.addArrangedSubview_(sep2)

        tavily_header = NSTextField.labelWithString_(
            "Tavily Search API (optional — for web search)")
        tavily_header.setTranslatesAutoresizingMaskIntoConstraints_(False)
        tavily_header.setTextColor_(_col_fg_dim())
        tavily_header.setFont_(_font(11.0, mono=False))
        container.addArrangedSubview_(tavily_header)

        row = self._make_field_row("TAVILY_API_KEY", "Tavily API Key", "", True)
        container.addArrangedSubview_(row)

    def _make_field_row(self, key: str, label: str, default: str, is_secret: bool):
        """Create one labelled field row and register it in _settings_fields."""
        row = NSView.alloc().init()
        row.setTranslatesAutoresizingMaskIntoConstraints_(False)

        lbl = NSTextField.labelWithString_(label)
        lbl.setTranslatesAutoresizingMaskIntoConstraints_(False)
        lbl.setTextColor_(_col_fg())
        lbl.setFont_(_font(12.0, mono=False))
        lbl.setAlignment_(2)  # right
        row.addSubview_(lbl)

        if is_secret:
            field = NSSecureTextField.alloc().init()
            # Show a placeholder so the user knows what to type.
            field.setPlaceholderString_("Paste your API key here…")
        else:
            field = NSTextField.alloc().init()
            field.setPlaceholderString_(default if default else "—")
        field.setTranslatesAutoresizingMaskIntoConstraints_(False)
        field.setFont_(_font(12.0, mono=True))
        field.setBezelStyle_(1)  # NSTextFieldRoundedBezel
        # Use system colors so the field is readable in light & dark mode.
        field.setTextColor_(NSColor.textColor())
        field.setBackgroundColor_(NSColor.textBackgroundColor())
        current = self._config_cache.get(key, default)
        # Pre-fill with the current value (masked automatically for secure fields).
        field.setStringValue_(current if current else "")
        row.addSubview_(field)

        constraints = [
            lbl.leadingAnchor().constraintEqualToAnchor_constant_(
                row.leadingAnchor(), 20.0),
            lbl.centerYAnchor().constraintEqualToAnchor_(field.centerYAnchor()),
            lbl.widthAnchor().constraintEqualToConstant_(130.0),

            field.leadingAnchor().constraintEqualToAnchor_constant_(
                lbl.trailingAnchor(), 8.0),
            field.trailingAnchor().constraintEqualToAnchor_constant_(
                row.trailingAnchor(), -20.0),
            field.topAnchor().constraintEqualToAnchor_constant_(
                row.topAnchor(), 4.0),
            field.bottomAnchor().constraintEqualToAnchor_constant_(
                row.bottomAnchor(), -4.0),
            field.heightAnchor().constraintEqualToConstant_(24.0),
        ]
        for c in constraints:
            c.setActive_(True)

        self._settings_fields[key] = field
        return row

    def _current_provider(self) -> str:
        if self._provider_popup is None:
            return self._config_cache.get("JUDECODE_PROVIDER", "deepseek")
        idx = self._provider_popup.indexOfSelectedItem()
        if 0 <= idx < len(PROVIDERS):
            return PROVIDERS[idx]
        return "deepseek"

    def providerChanged_(self, sender):
        self._rebuild_provider_fields()

    def cancelSettings_(self, sender):
        self._close_settings_sheet(accept=False)

    def saveSettings_(self, sender):
        """Collect field values, save to disk, and reload the agent."""
        # Gather values from all visible fields.
        new_values: dict[str, str] = {}
        new_values["JUDECODE_PROVIDER"] = self._current_provider()
        for key, field in self._settings_fields.items():
            val = field.stringValue()
            new_values[key] = val if val is not None else ""

        # Merge with the cached config so we don't lose fields that belong
        # to other providers.
        merged = dict(self._config_cache)
        merged.update(new_values)

        try:
            save_config(merged)
            self._config_cache = merged
        except Exception as exc:
            self._show_alert(
                "Could not save settings",
                f"Failed to write config:\n{exc}",
                NSAlertStyleWarning,
            )
            return

        # Reload the agent with the new config.
        self._worker.rebuild_engine()

        self._close_settings_sheet(accept=True)
        self._bus.put(
            f"✓ Settings saved to {CONFIG_PATH}\n", "system"
        )

    def _close_settings_sheet(self, accept: bool) -> None:
        panel = self._settings_panel
        self._settings_panel = None
        self._settings_fields = {}
        self._provider_popup = None
        if panel is not None:
            panel.close()
        NSApplication.sharedApplication().stopModal()

    def _show_alert(self, title: str, message: str, style=NSAlertStyleInformational) -> None:
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.setAlertStyle_(style)
        alert.runModal()

    # ── Output drain timer ──

    def drainOutput_(self, timer):
        """Called ~50×/s on the main run loop. Pumps asyncio + flushes output."""
        # Pump the agent's asyncio loop so it makes progress.
        self._worker.pump(timeout=0.005)

        items = self._bus.drain()
        if not items:
            # Update status only
            self._update_status()
            return

        text_view = self._output_view
        if text_view is None:
            return

        # Remember the current scroll position so we can decide whether to
        # auto-scroll to the bottom after appending.
        should_scroll = self._is_scrolled_to_bottom(text_view)

        for text, kind in items:
            self._append_output(text_view, text, kind)

        if should_scroll or self._auto_scroll:
            self._scroll_to_bottom(text_view)

        self._update_status()

    def _append_output(self, text_view: NSTextView, text: str, kind: str) -> None:
        """Append one chunk of text to the output view with appropriate styling."""
        base_font = _font(13.0, mono=True)

        if kind == "user":
            # Render the user's message with an accent colour + prefix.
            prefix = "❯ "
            attr = NSMutableAttributedString.alloc().initWithString_(prefix + text)
            attr.addAttribute_value_range_(
                NSForegroundColorAttributeName, _col_accent(),
                NSRange(0, len(prefix)))
            attr.addAttribute_value_range_(
                NSFontAttributeName, _font(13.0, bold=True, mono=True),
                NSRange(0, len(prefix)))
            attr.addAttribute_value_range_(
                NSForegroundColorAttributeName, _col_user_fg(),
                NSRange(len(prefix), len(prefix) + len(text)))
            self._append_attributed(text_view, attr)
            return

        if kind == "system":
            attr = ansi_to_attributed_string(text, base_font, fg_color=_col_fg_dim())
            self._append_attributed(text_view, attr)
            return

        if kind == "error":
            attr = ansi_to_attributed_string(text, base_font, fg_color=_col_error())
            self._append_attributed(text_view, attr)
            return

        if kind == "plain":
            attr = NSMutableAttributedString.alloc().initWithString_(text)
            attr.addAttribute_value_range_(
                NSFontAttributeName, base_font, NSRange(0, len(text)))
            attr.addAttribute_value_range_(
                NSForegroundColorAttributeName, _col_fg(), NSRange(0, len(text)))
            self._append_attributed(text_view, attr)
            return

        # Default: "ansi" — Rich output with escape codes.
        attr = ansi_to_attributed_string(text, base_font, fg_color=_col_fg())
        self._append_attributed(text_view, attr)

    def _append_attributed(self, text_view: NSTextView, attr: NSAttributedString) -> None:
        text_storage = text_view.textStorage()
        text_storage.appendAttributedString_(attr)

    def _is_scrolled_to_bottom(self, text_view: NSTextView) -> bool:
        scroll = text_view.enclosingScrollView()
        if scroll is None:
            return True
        clip = scroll.contentView()
        bounds = clip.bounds()
        doc_height = text_view.bounds().size.height
        # If we're within ~40 px of the bottom, treat as "at bottom".
        return (bounds.origin.y + bounds.size.height) >= (doc_height - 40.0)

    def _scroll_to_bottom(self, text_view: NSTextView) -> None:
        scroll = text_view.enclosingScrollView()
        if scroll is None:
            return
        clip = scroll.contentView()
        doc_height = text_view.bounds().size.height
        clip.bounds().origin.y = max(0.0, doc_height - clip.bounds().size.height)
        text_view.scrollRangeToVisible_(
            NSRange(text_view.string().length(), 0))

    def _update_status(self) -> None:
        if self._status_field is None:
            return
        if self._worker.is_busy:
            self._status_field.setStringValue_("● working…")
            self._status_field.setTextColor_(_col_accent())
        else:
            self._status_field.setStringValue_("○ ready")
            self._status_field.setTextColor_(_col_fg_dim())

    # ── Send button / input handling ──

    def sendClicked_(self, sender):
        """Send the current input text to the agent."""
        if self._input_view is None:
            return
        text = self._input_view.string()
        if not text or not text.strip():
            return
        if self._worker.is_busy:
            # Queue the message instead of dropping it.
            self._bus.put("(queued — agent is busy)\n", "system")
        # Echo the user's message into the output view.
        self._bus.put(text.rstrip() + "\n", "user")
        # Clear the input.
        self._input_view.setString_("")
        # Send to worker.
        self._worker.send_message(text)

    def clearConversation_(self, sender):
        """Clear the output view."""
        if self._output_view is not None:
            self._output_view.setString_("")

    # ── About box ──

    def showAbout_(self, sender):
        try:
            from judecode import __version__
            version = __version__
        except Exception:
            version = "0.1.0"
        alert = NSAlert.alloc().init()
        alert.setMessageText_("Jude Code")
        alert.setInformativeText_(
            f"Version {version}\n\n"
            "Your AI coding assistant — native macOS edition.\n"
            "Powered by your own API key."
        )
        alert.setAlertStyle_(NSAlertStyleInformational)
        alert.addButtonWithTitle_("OK")
        alert.runModal()

    # ── NSTextView delegate: ⌘+Enter to send ──

    def textView_doCommandBySelector_(self, tv, selector):
        # "insertNewline:" is plain Enter. We want ⌘+Enter to send, plain
        # Enter to insert a newline. NSTextView calls this for *some*
        # commands but not plain Enter. We use the keyDown approach below
        # instead. Returning NO lets the default handling proceed.
        return False


# ── Input-view subclass to capture ⌘+Enter ────────────────────────────

# We can't easily subclass NSTextView in pure PyObjC without objc, so we
# use a global approach: the AppDelegate installs itself as the window
# delegate and intercepts key events at the window level.

# ── Module-level entry point ──────────────────────────────────────────

import logging as _logging

_LOG_PATH = os.path.expanduser(
    "~/Library/Logs/JudeCode/mac_app.log"
)


def _setup_logging() -> None:
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        _logging.basicConfig(
            filename=_LOG_PATH,
            level=_logging.DEBUG,
            format="%(asctime)s [%(threadName)s] %(levelname)s %(name)s: %(message)s",
            force=True,
        )
        # Force flush so logs from the worker thread appear immediately.
        for h in _logging.getLogger().handlers:
            h.flush()
        _logging.getLogger("judecode").info("=== Jude Code Mac app starting ===")
    except Exception as exc:
        print(f"logging setup failed: {exc}", file=sys.stderr)


def run_mac_app() -> int:
    """Entry point: launch the native Mac app."""
    _setup_logging()
    log = _logging.getLogger("judecode.mac")

    try:
        from judecode.ui.mac_config import ensure_config_dir
        ensure_config_dir()

        # Create the shared application.
        log.info("Creating NSApplication")
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)

        # Set activation policy before showing any UI.
        log.info("Allocating delegate")
        delegate = JudeCodeAppDelegate.alloc().init()
        app.setDelegate_(delegate)

        # Run the event loop. This blocks until the user quits.
        log.info("Running event loop")
        app.run()
        log.info("Event loop exited")
        return 0
    except Exception:
        log.exception("Fatal error in run_mac_app")
        raise


if __name__ == "__main__":
    sys.exit(run_mac_app())
