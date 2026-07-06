"""ANSI escape-code → NSAttributedString parser for the native Mac UI.

Rich (via ``console.export_text(styles=True)``) produces text with SGR
escape sequences like ``\\x1b[1;36m...\\x1b[0m``. This module converts such
text into an ``NSAttributedString`` so it can be appended to an ``NSTextView``
with proper colours, bold, italic, underline, etc.

Only the subset of SGR codes that Rich actually emits is supported:
  - 0  reset
  - 1  bold
  - 2  dim (faint)        → rendered as 60% opacity grey
  - 3  italic
  - 4  underline
  - 22 bold off / dim off
  - 23 italic off
  - 24 underline off
  - 30-37  standard fg colours
  - 90-97  bright fg colours
  - 38;5;n 256-colour fg
  - 38;2;r;g;b true-colour fg
  - 39 default fg
"""

from __future__ import annotations

import re

from AppKit import (
    NSAttributedString,
    NSMutableDictionary,
    NSFont,
    NSFontManager,
    NSColor,
    NSRange,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSUnderlineStyleAttributeName,
)
from Foundation import NSMutableString

# ── Colour table (standard 16 + approximated 256) ──────────────────────

_STANDARD = [
    (0x00, 0x00, 0x00), (0xcc, 0x00, 0x00), (0x4e, 0x9a, 0x06),
    (0xc4, 0xa0, 0x00), (0x34, 0x65, 0xa4), (0x75, 0x50, 0x7b),
    (0x06, 0x98, 0x9a), (0xd3, 0xd7, 0xcf),
]
_BRIGHT = [
    (0x55, 0x57, 0x53), (0xef, 0x29, 0x29), (0x8a, 0xe2, 0x34),
    (0xfc, 0xe9, 0x4f), (0x72, 0x9f, 0xcf), (0xad, 0x7f, 0xa8),
    (0x34, 0xe2, 0xe2), (0xee, 0xee, 0xec),
]

# 6×6×6 colour cube + 24 greys for 256-colour mode
_CUBE_STEPS = [0, 95, 135, 175, 215, 255]


def _build_256_table() -> list:
    table = list(_STANDARD) + list(_BRIGHT)
    for r in _CUBE_STEPS:
        for g in _CUBE_STEPS:
            for b in _CUBE_STEPS:
                table.append((r, g, b))
    for i in range(24):
        v = 8 + i * 10
        table.append((v, v, v))
    return table


_256_TABLE = _build_256_table()


def _color_from_rgb(r: int, g: int, b: int) -> NSColor:
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255.0, g / 255.0, b / 255.0, 1.0)


def _color_for_index(idx: int) -> NSColor:
    r, g, b = _256_TABLE[idx]
    return _color_from_rgb(r, g, b)


# ── Attribute state ────────────────────────────────────────────────────

class _AttrState:
    __slots__ = ("bold", "dim", "italic", "underline", "fg", "bg")

    def __init__(self):
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.fg: NSColor | None = None
        self.bg: NSColor | None = None

    def copy(self) -> "_AttrState":
        s = _AttrState()
        s.bold = self.bold
        s.dim = self.dim
        s.italic = self.italic
        s.underline = self.underline
        s.fg = self.fg
        s.bg = self.bg
        return s

    def reset(self):
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.fg = None
        self.bg = None


# Regex for CSI sequences: \x1b[  params  final-letter
_CSI_RE = re.compile(r"\x1b\[([0-9;?]*)m")
# Also strip other common escape sequences (cursor moves etc.) silently.
_OTHER_ESC_RE = re.compile(r"\x1b\][^\x07]*\x07|\x1b\[[0-9;?]*[A-Za-z]")


def _apply_sgr(state: _AttrState, params: str):
    """Apply a single SGR parameter string to the attribute state."""
    if params == "":
        codes = [0]
    else:
        # Filter out empty parts from things like "1;;36"
        codes = [int(p) for p in params.split(";") if p != ""]

    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            state.reset()
        elif c == 1:
            state.bold = True
        elif c == 2:
            state.dim = True
        elif c == 3:
            state.italic = True
        elif c == 4:
            state.underline = True
        elif c == 22:
            state.bold = False
            state.dim = False
        elif c == 23:
            state.italic = False
        elif c == 24:
            state.underline = False
        elif 30 <= c <= 37:
            state.fg = _color_from_rgb(*_STANDARD[c - 30])
        elif c == 38:
            # Extended colour: 38;5;n  or  38;2;r;g;b
            if i + 1 < len(codes):
                mode = codes[i + 1]
                if mode == 5 and i + 2 < len(codes):
                    state.fg = _color_for_index(codes[i + 2])
                    i += 2
                elif mode == 2 and i + 4 < len(codes):
                    state.fg = _color_from_rgb(codes[i + 2], codes[i + 3], codes[i + 4])
                    i += 4
        elif c == 39:
            state.fg = None
        elif 40 <= c <= 47:
            state.bg = _color_from_rgb(*_STANDARD[c - 40])
        elif c == 49:
            state.bg = None
        elif 90 <= c <= 97:
            state.fg = _color_from_rgb(*_BRIGHT[c - 90])
        elif 100 <= c <= 107:
            state.bg = _color_from_rgb(*_BRIGHT[c - 100])
        i += 1


def _font_for_state(state: _AttrState, base_font: NSFont, bold_font: NSFont,
                    italic_font: NSFont, bold_italic_font: NSFont) -> NSFont:
    if state.bold and state.italic:
        return bold_italic_font
    if state.bold:
        return bold_font
    if state.italic:
        return italic_font
    return base_font


def ansi_to_attributed_string(
    ansi_text: str,
    base_font: NSFont,
    fg_color: NSColor = None,
) -> NSAttributedString:
    """Convert ANSI-styled text into an NSAttributedString.

    Args:
        ansi_text: Text possibly containing SGR escape sequences.
        base_font: The font to use for regular text.
        fg_color: Default foreground colour (defaults to near-black).
    """
    if fg_color is None:
        fg_color = NSColor.colorWithSRGBRed_green_blue_alpha_(0.13, 0.13, 0.13, 1.0)

    fm = NSFontManager.sharedFontManager()

    def _make_traits(font: NSFont, bold: bool, italic: bool) -> NSFont:
        traits = 0
        if bold:
            traits |= 0x02  # NSBoldFontMask
        if italic:
            traits |= 0x01  # NSItalicFontMask
        if traits == 0:
            return font
        result = fm.convertFont_hasTraits_(font, traits)
        return result if result is not None else font

    bold_font = _make_traits(base_font, True, False)
    italic_font = _make_traits(base_font, False, True)
    bold_italic_font = _make_traits(base_font, True, True)

    mutable = NSMutableString.string()
    attrs_list: list[tuple[NSRange, NSMutableDictionary]] = []
    state = _AttrState()

    pos = 0
    # We walk through the text, handling CSI SGR sequences and stripping
    # other escape sequences, copying plain text with current attributes.
    index = 0
    n = len(ansi_text)
    while index < n:
        ch = ansi_text[index]
        if ch == "\x1b":
            # Try to match a CSI SGR sequence first
            m = _CSI_RE.match(ansi_text, index)
            if m:
                # Flush any pending plain text up to here (already flushed
                # incrementally; nothing to do).
                _apply_sgr(state, m.group(1))
                index = m.end()
                continue
            # Otherwise strip any other escape sequence
            m2 = _OTHER_ESC_RE.match(ansi_text, index)
            if m2:
                index = m2.end()
                continue
            # Lone escape char — skip it
            index += 1
            continue
        else:
            # Accumulate plain text until next escape
            start = index
            while index < n and ansi_text[index] != "\x1b":
                index += 1
            chunk = ansi_text[start:index]

            range_start = mutable.length()
            mutable.appendString_(chunk)
            range_len = len(chunk.encode("utf-8", errors="replace"))
            # NSMutableString.length() counts UTF-16 code units; we need the
            # actual length we just appended.
            range_len = mutable.length() - range_start
            rng = NSRange(range_start, range_len)

            attrs = NSMutableDictionary.dictionary()
            font = _font_for_state(state, base_font, bold_font, italic_font, bold_italic_font)
            attrs[NSFontAttributeName] = font
            color = state.fg if state.fg is not None else fg_color
            if state.dim:
                # Render dim as a translucent grey
                color = NSColor.colorWithSRGBRed_green_blue_alpha_(0.45, 0.45, 0.45, 1.0)
            attrs[NSForegroundColorAttributeName] = color
            if state.underline:
                attrs[NSUnderlineStyleAttributeName] = 1  # NSUnderlineStyleSingle
            attrs_list.append((rng, attrs))

    # Build the attributed string
    result = NSMutableAttributedString.alloc().initWithString_(mutable)
    for rng, attrs in attrs_list:
        result.setAttributes_range_(attrs, rng)
    return result
