"""A small VT100/xterm-compatible terminal emulator.

Feeds raw output bytes (as text) through an escape-sequence state machine and
maintains a grid of styled cells, which can be snapshotted at any point.
It covers what real-world shells and CLI tools emit: SGR colors (16, 256 and
truecolor), cursor movement, erasing, insert/delete, scroll regions, the
alternate screen, wide characters and OSC window titles.
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


DEFAULT_STYLE = Style()

# Placeholder occupying the right half of a double-width character.
WIDE_TAIL = ""


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


def char_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class Terminal:
    def __init__(self, cols: int = 80, rows: int = 24):
        self.cols = cols
        self.rows = rows
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
        self.top, self.bottom = 0, self.rows - 1
        self._state = "ground"
        self._params = ""
        self._osc = ""

    def _blank_row(self, style: Style = DEFAULT_STYLE) -> list:
        # Erased cells keep the current background color, like xterm.
        blank = Style(bg=style.bg) if style.bg is not None else DEFAULT_STYLE
        return [Cell(" ", blank) for _ in range(self.cols)]

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
                else:
                    self._params += ch
            elif state == "osc":
                if ch == "\x07":
                    self._state = "ground"
                    self._osc_done()
                elif ch == "\x1b":
                    self._state = "osc_esc"
                else:
                    self._osc += ch
            elif state == "osc_esc":
                # ESC \ (string terminator) ends the OSC; anything else aborts it.
                self._osc_done()
                self._state = "ground"
                if ch != "\\":
                    self._ground(ch)
            elif state == "charset":
                self._state = "ground"  # ESC ( B etc.: designate charset, ignored

    def _ground(self, ch: str) -> None:
        if ch == "\x1b":
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
            self.cx = min(self.cols - 1, (self.cx // 8 + 1) * 8)
        elif ch < " " or ch == "\x7f":
            pass  # BEL and other C0 controls
        else:
            self._put(ch)

    def _escape(self, ch: str) -> None:
        self._state = "ground"
        if ch == "[":
            self._state, self._params = "csi", ""
        elif ch == "]":
            self._state, self._osc = "osc", ""
        elif ch in "()*+":
            self._state = "charset"
        elif ch == "7":
            self.saved_cursor = (self.cx, self.cy, self.style)
        elif ch == "8":
            self.cx, self.cy, self.style = self.saved_cursor
        elif ch == "D":
            self._linefeed()
        elif ch == "E":
            self.cx = 0
            self._linefeed()
        elif ch == "M":
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

    # ---------------------------------------------------------------- writing

    def _put(self, ch: str) -> None:
        width = char_width(ch)
        if width == 0:
            # Combining mark: attach to the previous cell.
            x = max(0, self.cx - (0 if self.wrap_pending else 1))
            cell = self.grid[self.cy][x]
            cell.char += ch
            return
        if self.wrap_pending or self.cx + width > self.cols:
            self.cx = 0
            self._linefeed()
            self.wrap_pending = False
        row = self.grid[self.cy]
        row[self.cx] = Cell(ch, self.style)
        if width == 2:
            row[self.cx + 1] = Cell(WIDE_TAIL, self.style)
        if self.cx + width >= self.cols:
            self.cx = self.cols - 1
            self.wrap_pending = True
        else:
            self.cx += width

    def _linefeed(self) -> None:
        self.wrap_pending = False
        if self.cy == self.bottom:
            self._scroll_up(1)
        elif self.cy < self.rows - 1:
            self.cy += 1

    def _scroll_up(self, n: int) -> None:
        for _ in range(n):
            del self.grid[self.top]
            self.grid.insert(self.bottom, self._blank_row(self.style))

    def _scroll_down(self, n: int) -> None:
        for _ in range(n):
            del self.grid[self.bottom]
            self.grid.insert(self.top, self._blank_row(self.style))

    def _erase(self, row: int, start: int, end: int) -> None:
        blank = Style(bg=self.style.bg) if self.style.bg is not None else DEFAULT_STYLE
        line = self.grid[row]
        for x in range(max(0, start), min(self.cols, end)):
            line[x] = Cell(" ", blank)

    # -------------------------------------------------------------------- CSI

    def _csi(self, raw: str, final: str) -> None:
        private = bool(raw) and raw[0] in "?>=<"
        body = raw[1:] if private else raw
        args = []
        for part in body.split(";"):
            # Colon sub-parameters (38:2::r:g:b) are normalised to semicolons.
            for sub in part.split(":"):
                args.append(int(sub) if sub.isdigit() else None)

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
        elif final == "B":
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
        elif final == "J":
            mode = args[0] if args and args[0] is not None else 0
            if mode == 0:
                self._erase(self.cy, self.cx, self.cols)
                for y in range(self.cy + 1, self.rows):
                    self._erase(y, 0, self.cols)
            elif mode == 1:
                for y in range(self.cy):
                    self._erase(y, 0, self.cols)
                self._erase(self.cy, 0, self.cx + 1)
            else:
                for y in range(self.rows):
                    self._erase(y, 0, self.cols)
        elif final == "K":
            mode = args[0] if args and args[0] is not None else 0
            if mode == 0:
                self._erase(self.cy, self.cx, self.cols)
            elif mode == 1:
                self._erase(self.cy, 0, self.cx + 1)
            else:
                self._erase(self.cy, 0, self.cols)
        elif final == "X":
            self._erase(self.cy, self.cx, self.cx + arg(0))
        elif final == "P":
            row, n = self.grid[self.cy], min(arg(0), self.cols - self.cx)
            del row[self.cx:self.cx + n]
            row.extend(Cell() for _ in range(n))
        elif final == "@":
            row, n = self.grid[self.cy], min(arg(0), self.cols - self.cx)
            for _ in range(n):
                row.insert(self.cx, Cell())
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
        elif final == "r":
            top, bottom = arg(0) - 1, arg(1, self.rows) - 1
            if top < bottom < self.rows:
                self.top, self.bottom = top, bottom
                self.cx = self.cy = 0
        elif final == "s":
            self.saved_cursor = (self.cx, self.cy, self.style)
        elif final == "u":
            self.cx, self.cy, self.style = self.saved_cursor
        elif final == "m":
            self._sgr(args)

    def _dec_mode(self, args: list, enable: bool) -> None:
        for mode in args:
            if mode == 25:
                self.cursor_visible = enable
            elif mode in (47, 1047, 1049):
                if enable and self.saved_main is None:
                    self.saved_main = (self.grid, self.cx, self.cy)
                    self.grid = [self._blank_row() for _ in range(self.rows)]
                elif not enable and self.saved_main is not None:
                    self.grid, self.cx, self.cy = self.saved_main
                    self.saved_main = None

    def _sgr(self, args: list) -> None:
        if not args:
            args = [0]
        s = self.style
        i = 0
        while i < len(args):
            a = args[i] or 0
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
            elif a in (38, 48):
                color, used = self._extended_color(args[i + 1:])
                i += used
                if color is not None or used:
                    s = replace(s, fg=color) if a == 38 else replace(s, bg=color)
            i += 1
        self.style = s

    @staticmethod
    def _extended_color(rest: list):
        """Parse the tail of 38;5;n or 38;2;r;g;b. Returns (color, args consumed)."""
        if not rest:
            return None, 0
        if rest[0] == 5 and len(rest) >= 2:
            return (rest[1] or 0) % 256, 2
        if rest[0] == 2 and len(rest) >= 4:
            # Tolerate the colon form's optional colorspace id: 38:2::r:g:b
            vals = rest[1:5] if len(rest) >= 5 and rest[1] is None else rest[1:4]
            consumed = 1 + len(vals)
            r, g, b = (min(255, v or 0) for v in vals[-3:])
            return f"#{r:02x}{g:02x}{b:02x}", consumed
        return None, 1

    # ------------------------------------------------------------- snapshots

    def snapshot(self) -> Snapshot:
        rows = []
        for line in self.grid:
            runs = []
            text, style = "", None
            for cell in line:
                if cell.char == WIDE_TAIL:
                    continue
                if cell.style != style and text:
                    runs.append((text, style))
                    text = ""
                style = cell.style
                text += cell.char
            if text:
                runs.append((text, style))
            rows.append(tuple(runs))
        return Snapshot(
            rows=tuple(rows),
            cursor=(self.cy, self.cx),
            cursor_visible=self.cursor_visible,
            title=self.title,
        )

    def text(self) -> str:
        """Plain-text screen contents, trailing blanks stripped (handy for tests)."""
        return "\n".join(
            "".join(c.char for c in row).rstrip() for row in self.grid
        ).rstrip("\n")
