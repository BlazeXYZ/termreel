"""A small VT100/xterm-compatible terminal emulator.

Feeds raw output bytes (as text) through an escape-sequence state machine and
maintains a grid of styled cells, which can be snapshotted at any point.
It covers what real-world shells and CLI tools emit: SGR colors (16, 256 and
truecolor), cursor movement, erasing, insert/delete, scroll regions, the
alternate screen, wide characters, the DEC line-drawing character set,
window resizes and OSC window titles. DCS/APC/PM strings are swallowed.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Style:
    # Colors are None (terminal default), int 0-255 (palette) or "#rrggbb".
    fg: object = None
    bg: object = None
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: bool = False
    inverse: bool = False
    strike: bool = False
    hidden: bool = False


DEFAULT_STYLE = Style()

# Placeholder occupying the right half of a double-width character.
WIDE_TAIL = ""

# Largest terminal accepted from a resize, and the cap on numeric parameters,
# so a hostile or corrupt recording cannot make the emulator loop for ever.
MAX_SIZE = 1000
MAX_PARAM = 65535

# DEC special graphics (selected with ESC ( 0): the line-drawing set that
# ncurses, tput and dialog fall back to.
DEC_GRAPHICS = dict(zip("`abcdefghijklmnopqrstuvwxyz{|}~",
                        "◆▒␉␌␍␊°±␤␋┘┐┌└┼⎺⎻─⎼⎽├┤┴┬│≤≥π≠£·"))

# Zero-width format characters (joiners, word joiner, BOM). Other format
# characters, such as the soft hyphen, take a cell in most terminals.
_ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿"}


@dataclass
class Cell:
    char: str = " "
    style: Style = DEFAULT_STYLE


@dataclass(frozen=True)
class Snapshot:
    """An immutable, hashable view of the screen used for rendering."""

    rows: tuple  # tuple of rows; each row is a tuple of (text, Style) runs
    cursor: tuple  # (row, col)
    cursor_visible: bool
    title: str
    cols: int = 80


def char_width(ch: str) -> int:
    """Cells taken by one code point: 0 (combining), 1, or 2 (wide/emoji)."""
    if not ch:
        return 0
    if ch < "̀":
        return 1  # fast path for Latin text
    if unicodedata.combining(ch) or ch in _ZERO_WIDTH or unicodedata.category(ch) in ("Mn", "Me"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _is_wide(cell_char: str) -> bool:
    return bool(cell_char) and cell_char[0] >= "ᄀ" and char_width(cell_char[0]) == 2


def resize_sequence(cols: int, rows: int) -> str:
    """The xterm window-resize sequence (CSI 8;rows;cols t)."""
    return f"\x1b[8;{rows};{cols}t"


class Terminal:
    def __init__(self, cols: int = 80, rows: int = 24):
        self.cols = max(1, min(cols, MAX_SIZE))
        self.rows = max(1, min(rows, MAX_SIZE))
        self.title = ""
        self.reset()

    # ------------------------------------------------------------------ state

    def reset(self) -> None:
        self.grid = [self._blank_row() for _ in range(self.rows)]
        self.saved_main = None
        self.cx = self.cy = 0
        self.saved_cursor = (0, 0, DEFAULT_STYLE)
        self.style = DEFAULT_STYLE
        self.cursor_visible = True
        self.wrap_pending = False
        self.autowrap = True
        self.insert_mode = False
        self.charsets = ["B", "B"]  # G0, G1
        self.shift = 0  # which of G0/G1 is in use (SI/SO)
        self.last_char = None
        self.top, self.bottom = 0, self.rows - 1
        self._state = "ground"
        self._params = ""
        self._osc = ""
        self._charset_slot = None

    def _blank_row(self, style: Style = DEFAULT_STYLE) -> list:
        # Erased cells keep the current background color, like xterm.
        blank = Style(bg=style.bg) if style.bg is not None else DEFAULT_STYLE
        return [Cell(" ", blank) for _ in range(self.cols)]

    def resize(self, cols: int, rows: int) -> None:
        """Change the screen size, keeping the cursor line on screen."""
        cols, rows = max(1, min(cols, MAX_SIZE)), max(1, min(rows, MAX_SIZE))
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows

        def fit(grid: list, cy: int) -> tuple:
            excess = max(0, cy + 1 - rows)  # lines pushed off the top
            grid, cy = grid[excess:excess + rows], cy - excess
            for line in grid:
                del line[cols:]
                line.extend(Cell() for _ in range(cols - len(line)))
            grid += [self._blank_row() for _ in range(rows - len(grid))]
            return grid, cy

        self.grid, self.cy = fit(self.grid, self.cy)
        self.cx = min(self.cx, cols - 1)
        if self.saved_main is not None:
            grid, x, y = self.saved_main
            grid, y = fit(grid, y)
            self.saved_main = (grid, min(x, cols - 1), y)
        self.top, self.bottom = 0, rows - 1
        self.wrap_pending = False

    # ---------------------------------------------------------------- parsing

    def feed(self, data: str) -> None:
        for ch in data:
            state = self._state
            if state == "ground":
                self._ground(ch)
            elif state == "escape":
                self._escape(ch)
            elif state == "csi":
                if "\x40" <= ch <= "\x7e":
                    self._state = "ground"
                    self._csi(self._params, ch)
                elif ch == "\x1b":
                    self._state = "escape"
                elif ch in "\x18\x1a":
                    self._state = "ground"  # CAN and SUB cancel the sequence
                elif ch < " ":
                    self._ground(ch)  # C0 controls still act mid-sequence
                elif len(self._params) < 256:
                    self._params += ch
            elif state in ("osc", "string"):
                # OSC (titles, hyperlinks) and DCS/SOS/PM/APC strings end at
                # BEL or ST (ESC \). Only OSC content is kept.
                if ch == "\x07":
                    self._state = "ground"
                    if state == "osc":
                        self._osc_done()
                elif ch == "\x1b":
                    self._state = state + "_esc"
                elif ch in "\x18\x1a":
                    self._state = "ground"
                elif state == "osc" and len(self._osc) < 4096:
                    self._osc += ch
            elif state in ("osc_esc", "string_esc"):
                # ESC \ ends the string; ESC + anything else ends it and
                # starts a new escape sequence.
                if state == "osc_esc":
                    self._osc_done()
                self._state = "ground"
                if ch != "\\":
                    self._escape(ch)
            elif state == "charset":
                self._state = "ground"
                if self._charset_slot is not None:
                    self.charsets[self._charset_slot] = ch

    def _ground(self, ch: str) -> None:
        if ch >= " " and ch != "\x7f" and not ("\x80" <= ch <= "\x9f"):
            self._put(ch)
        elif ch == "\x1b":
            self._state = "escape"
        elif ch == "\r":
            self.cx = 0
            self.wrap_pending = False
        elif ch in "\n\x0b\x0c":
            self._linefeed()
        elif ch == "\b":
            self.wrap_pending = False
            self.cx = max(0, self.cx - 1)
        elif ch == "\t":
            self._tab(1)
        elif ch == "\x0e":
            self.shift = 1  # SO: use G1
        elif ch == "\x0f":
            self.shift = 0  # SI: use G0
        # BEL, DEL, C1 controls and other C0 controls are ignored.

    def _escape(self, ch: str) -> None:
        self._state = "ground"
        if ch == "[":
            self._state, self._params = "csi", ""
        elif ch == "]":
            self._state, self._osc = "osc", ""
        elif ch in "PX^_":
            self._state = "string"  # DCS, SOS, PM, APC: swallowed
        elif ch in "()":
            self._state, self._charset_slot = "charset", "()".index(ch)
        elif ch in "*+-./#% ":
            # G2/G3 designation, DECALN, UTF-8 selection...: one more byte, ignored.
            self._state, self._charset_slot = "charset", None
        elif ch == "7":
            self.saved_cursor = (self.cx, self.cy, self.style)
        elif ch == "8":
            self._restore_cursor()
        elif ch == "D":
            self._linefeed()
        elif ch == "E":
            self.cx = 0
            self._linefeed()
        elif ch == "M":
            self.wrap_pending = False
            if self.cy == self.top:
                self._scroll_down(1)
            else:
                self.cy = max(0, self.cy - 1)
        elif ch == "c":
            self.reset()

    def _osc_done(self) -> None:
        code, _, text = self._osc.partition(";")
        if code in ("0", "2"):
            self.title = text

    def _restore_cursor(self) -> None:
        cx, cy, self.style = self.saved_cursor
        self.cx, self.cy = min(cx, self.cols - 1), min(cy, self.rows - 1)
        self.wrap_pending = False

    # ---------------------------------------------------------------- writing

    def _put(self, ch: str) -> None:
        if "\ud800" <= ch <= "\udfff":
            ch = "�"  # lone surrogate from a corrupt cast file
        if self.charsets[self.shift] == "0":
            ch = DEC_GRAPHICS.get(ch, ch)
        width = char_width(ch)
        if width == 0:
            # Combining mark: attach to the previous character.
            x = self.cx if self.wrap_pending else self.cx - 1
            if x < 0:
                return
            row = self.grid[self.cy]
            if row[x].char == WIDE_TAIL and x > 0:
                x -= 1
            row[x].char += ch
            return
        if width > self.cols:
            ch, width = " ", 1
        if self.wrap_pending or self.cx + width > self.cols:
            if self.autowrap:
                self.cx = 0
                self._linefeed()
            else:
                self.cx = self.cols - width
            self.wrap_pending = False
        row = self.grid[self.cy]
        if self.insert_mode:
            row[self.cx:self.cx] = [Cell() for _ in range(width)]
            del row[self.cols:]
        row[self.cx] = Cell(ch, self.style)
        if width == 2:
            row[self.cx + 1] = Cell(WIDE_TAIL, self.style)
        self.last_char = ch
        if self.cx + width >= self.cols:
            self.cx = self.cols - 1
            self.wrap_pending = self.autowrap
        else:
            self.cx += width

    def _tab(self, n: int) -> None:
        for _ in range(min(n, self.cols)):
            self.cx = min(self.cols - 1, (self.cx // 8 + 1) * 8)

    def _back_tab(self, n: int) -> None:
        for _ in range(min(n, self.cols)):
            self.cx = max(0, (self.cx - 1) // 8 * 8)

    def _linefeed(self) -> None:
        self.wrap_pending = False
        if self.cy == self.bottom:
            self._scroll_up(1)
        elif self.cy < self.rows - 1:
            self.cy += 1

    def _scroll_up(self, n: int) -> None:
        for _ in range(min(n, self.bottom - self.top + 1)):
            del self.grid[self.top]
            self.grid.insert(self.bottom, self._blank_row(self.style))

    def _scroll_down(self, n: int) -> None:
        for _ in range(min(n, self.bottom - self.top + 1)):
            del self.grid[self.bottom]
            self.grid.insert(self.top, self._blank_row(self.style))

    def _erase(self, row: int, start: int, end: int) -> None:
        blank = Style(bg=self.style.bg) if self.style.bg is not None else DEFAULT_STYLE
        line = self.grid[row]
        for x in range(max(0, start), min(self.cols, end)):
            line[x] = Cell(" ", blank)

    def _soft_reset(self) -> None:
        """DECSTR (CSI ! p): reset modes and style, keep the screen."""
        self.style = DEFAULT_STYLE
        self.cursor_visible = self.autowrap = True
        self.insert_mode = self.wrap_pending = False
        self.charsets, self.shift = ["B", "B"], 0
        self.top, self.bottom = 0, self.rows - 1
        self.saved_cursor = (0, 0, DEFAULT_STYLE)

    # -------------------------------------------------------------------- CSI

    def _csi(self, raw: str, final: str) -> None:
        private = raw[:1] in ("?", ">", "=", "<")
        body = raw[1:] if private else raw
        intermediates = body.rstrip("0123456789;:")
        if intermediates:
            # Sequences with intermediate bytes (cursor style, DECSTR...).
            if raw == "!" and final == "p":
                self._soft_reset()
            return
        # Parameters are ';'-separated; each may carry ':' sub-parameters.
        groups = [[min(int(s), MAX_PARAM) if s else None for s in part.split(":")]
                  for part in body.split(";")]
        args = [g[0] for g in groups]

        def arg(i: int, default: int = 1) -> int:
            v = args[i] if i < len(args) else None
            return default if v in (None, 0) else v

        if private:
            if raw[0] == "?" and final in "hl":
                self._dec_mode(args, final == "h")
            return

        if final != "m":
            self.wrap_pending = False
        if final == "A":
            self.cy = max(self.top if self.cy >= self.top else 0, self.cy - arg(0))
        elif final in "Be":
            self.cy = min(self.bottom if self.cy <= self.bottom else self.rows - 1, self.cy + arg(0))
        elif final in "Ca":
            self.cx = min(self.cols - 1, self.cx + arg(0))
        elif final == "D":
            self.cx = max(0, self.cx - arg(0))
        elif final == "E":
            self.cx, self.cy = 0, min(self.rows - 1, self.cy + arg(0))
        elif final == "F":
            self.cx, self.cy = 0, max(0, self.cy - arg(0))
        elif final in "G`":
            self.cx = min(self.cols - 1, arg(0) - 1)
        elif final in "Hf":
            self.cy = min(self.rows - 1, arg(0) - 1)
            self.cx = min(self.cols - 1, arg(1) - 1)
        elif final == "d":
            self.cy = min(self.rows - 1, arg(0) - 1)
        elif final == "I":
            self._tab(arg(0))
        elif final == "Z":
            self._back_tab(arg(0))
        elif final == "J":
            mode = args[0] or 0
            if mode == 0:
                self._erase(self.cy, self.cx, self.cols)
                for y in range(self.cy + 1, self.rows):
                    self._erase(y, 0, self.cols)
            elif mode == 1:
                for y in range(self.cy):
                    self._erase(y, 0, self.cols)
                self._erase(self.cy, 0, self.cx + 1)
            elif mode == 2:
                for y in range(self.rows):
                    self._erase(y, 0, self.cols)
            # mode 3 clears the scrollback only, which is not drawn.
        elif final == "K":
            mode = args[0] or 0
            if mode == 0:
                self._erase(self.cy, self.cx, self.cols)
            elif mode == 1:
                self._erase(self.cy, 0, self.cx + 1)
            elif mode == 2:
                self._erase(self.cy, 0, self.cols)
        elif final == "X":
            self._erase(self.cy, self.cx, self.cx + arg(0))
        elif final == "P":
            row, n = self.grid[self.cy], min(arg(0), self.cols - self.cx)
            del row[self.cx:self.cx + n]
            row.extend(Cell() for _ in range(n))
        elif final == "@":
            row, n = self.grid[self.cy], min(arg(0), self.cols - self.cx)
            row[self.cx:self.cx] = [Cell() for _ in range(n)]
            del row[self.cols:]
        elif final in "LM":
            if self.top <= self.cy <= self.bottom:
                saved_top, self.top = self.top, self.cy
                (self._scroll_down if final == "L" else self._scroll_up)(arg(0))
                self.top = saved_top
                self.cx = 0
        elif final == "S":
            self._scroll_up(arg(0))
        elif final == "T":
            self._scroll_down(arg(0))
        elif final == "b":
            if self.last_char is not None:
                for _ in range(min(arg(0), self.cols * self.rows)):
                    self._put(self.last_char)
        elif final == "r":
            top, bottom = arg(0) - 1, arg(1, self.rows) - 1
            if top < bottom < self.rows:
                self.top, self.bottom = top, bottom
                self.cx = self.cy = 0
        elif final in "hl":
            if 4 in args:
                self.insert_mode = final == "h"
        elif final == "s":
            self.saved_cursor = (self.cx, self.cy, self.style)
        elif final == "u":
            self._restore_cursor()
        elif final == "t":
            if args[0] == 8:
                self.resize(arg(2, self.cols), arg(1, self.rows))
        elif final == "m":
            self._sgr(groups)

    def _dec_mode(self, args: list, enable: bool) -> None:
        for mode in args:
            if mode == 25:
                self.cursor_visible = enable
            elif mode == 7:
                self.autowrap = enable
                if not enable:
                    self.wrap_pending = False
            elif mode in (47, 1047, 1049):
                if enable and self.saved_main is None:
                    self.saved_main = (self.grid, self.cx, self.cy)
                    self.grid = [self._blank_row() for _ in range(self.rows)]
                elif not enable and self.saved_main is not None:
                    self.grid, self.cx, self.cy = self.saved_main
                    self.saved_main = None
                self.wrap_pending = False

    def _sgr(self, groups: list) -> None:
        s = self.style
        i = 0
        while i < len(groups):
            group = groups[i]
            a = group[0] or 0
            if len(group) > 1:
                # Colon form carries its own sub-parameters: 38:2::r:g:b, 4:3.
                if a in (38, 48):
                    color = self._colon_color(group[1:])
                    if color is not None:
                        s = replace(s, fg=color) if a == 38 else replace(s, bg=color)
                elif a == 4:
                    s = replace(s, underline=(group[1] or 0) != 0)
                i += 1
                continue
            if a == 0:
                s = DEFAULT_STYLE
            elif a == 1:
                s = replace(s, bold=True)
            elif a == 2:
                s = replace(s, dim=True)
            elif a == 3:
                s = replace(s, italic=True)
            elif a in (4, 21):
                s = replace(s, underline=True)
            elif a == 7:
                s = replace(s, inverse=True)
            elif a == 8:
                s = replace(s, hidden=True)
            elif a == 9:
                s = replace(s, strike=True)
            elif a == 22:
                s = replace(s, bold=False, dim=False)
            elif a == 23:
                s = replace(s, italic=False)
            elif a == 24:
                s = replace(s, underline=False)
            elif a == 27:
                s = replace(s, inverse=False)
            elif a == 28:
                s = replace(s, hidden=False)
            elif a == 29:
                s = replace(s, strike=False)
            elif 30 <= a <= 37:
                s = replace(s, fg=a - 30)
            elif 40 <= a <= 47:
                s = replace(s, bg=a - 40)
            elif 90 <= a <= 97:
                s = replace(s, fg=a - 90 + 8)
            elif 100 <= a <= 107:
                s = replace(s, bg=a - 100 + 8)
            elif a == 39:
                s = replace(s, fg=None)
            elif a == 49:
                s = replace(s, bg=None)
            elif a in (38, 48, 58):
                # 58 is the underline color: parsed so its arguments are not
                # mistaken for attributes, then ignored.
                color, used = self._extended_color([g[0] for g in groups[i + 1:i + 5]])
                i += used
                if color is not None and a != 58:
                    s = replace(s, fg=color) if a == 38 else replace(s, bg=color)
            i += 1
        self.style = s

    @staticmethod
    def _extended_color(rest: list):
        """Parse the tail of 38;5;n or 38;2;r;g;b. Returns (color, args consumed)."""
        if not rest:
            return None, 0
        if rest[0] == 5:
            if len(rest) >= 2:
                return (rest[1] or 0) % 256, 2
            return None, 1
        if rest[0] == 2:
            if len(rest) >= 4:
                r, g, b = (min(255, v or 0) for v in rest[1:4])
                return f"#{r:02x}{g:02x}{b:02x}", 4
            return None, len(rest)
        return None, 1

    @staticmethod
    def _colon_color(sub: list):
        """Parse colon sub-parameters: 5:n, 2:r:g:b or 2:colorspace:r:g:b."""
        if sub[0] == 5 and len(sub) >= 2:
            return (sub[1] or 0) % 256
        if sub[0] == 2:
            vals = sub[1:]
            if len(vals) >= 4:
                vals = vals[1:4]  # skip the color-space id
            if len(vals) == 3:
                r, g, b = (min(255, v or 0) for v in vals)
                return f"#{r:02x}{g:02x}{b:02x}"
        return None

    # ------------------------------------------------------------- snapshots

    def snapshot(self) -> Snapshot:
        rows = []
        for line in self.grid:
            runs = []
            text, style = "", None
            for x, cell in enumerate(line):
                ch = cell.char
                if ch == WIDE_TAIL:
                    if x > 0 and _is_wide(line[x - 1].char):
                        continue
                    ch = " "  # the wide character it belonged to was overwritten
                elif _is_wide(ch) and (x + 1 >= len(line) or line[x + 1].char != WIDE_TAIL):
                    ch = " "  # its right half was overwritten
                if cell.style != style and text:
                    runs.append((text, style))
                    text = ""
                style = cell.style
                text += ch
            if text:
                runs.append((text, style))
            rows.append(tuple(runs))
        return Snapshot(
            rows=tuple(rows),
            cursor=(self.cy, self.cx),
            cursor_visible=self.cursor_visible,
            title=self.title,
            cols=self.cols,
        )

    def text(self) -> str:
        """Plain-text screen contents, trailing blanks stripped (handy for tests)."""
        return "\n".join(
            "".join(c.char for c in row).rstrip() for row in self.grid
        ).rstrip("\n")
