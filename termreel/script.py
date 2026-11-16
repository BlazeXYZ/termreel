"""Scripted demos: describe a terminal session in a text file, get a recording.

Reproducible, editable, and it needs no pty, so it works the same on every OS.

    # comments start with '#'
    @size 80x16              terminal size
    @title My CLI            window title
    @theme nord              color theme (a built-in name, or a .json file next to the script)
    @prompt [green]~/app[/] $        prompt shown before each command
    @typing 18               typing speed, characters per second
    @cwd ..                  run later `$` commands here (relative to the script's folder)
    @max-lines 20            show only the first 20 lines of each command's output (0 = all)
    @sleep 1.5               pause (kept exactly: --idle does not shorten it)
    @clear                   clear the screen
    $ ls -la                 type the command, then really run it
    ~ deploy --prod          type the command but do not run it
    > [bold green]✓[/] done  print a line of output (markup allowed)

Markup: [bold], [dim], [italic], [underline], [strike], [inverse], color
names such as [red] or [bright-cyan], backgrounds such as [on-blue], hex
colors [#ff8800] or [#f80], combined as [bold red on-black], closed with [/].
A literal '[' is '[['. Lines may be indented. Errors name the line they come
from.
"""

from __future__ import annotations

import codecs
import math
import os
import random
import re
import subprocess
import time

from .cast import Recording
from .themes import is_path

# Scripted pauses are written as a run of empty events no further apart than
# this, so the renderer's idle cap (--idle) never shortens them.
KEEPALIVE = 0.25

TAG_RE = re.compile(r"\[(/|[a-zA-Z#0-9\- ]+)\]")
HEX_RE = re.compile(r"#([0-9a-f]{3}|[0-9a-f]{6})")
COLORS = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
ATTRS = {"bold": "1", "dim": "2", "italic": "3", "underline": "4", "inverse": "7", "strike": "9"}


def _color_code(name: str, background: bool) -> str | None:
    m = HEX_RE.fullmatch(name)
    if m:
        digits = m.group(1) if len(m.group(1)) == 6 else "".join(c * 2 for c in m.group(1))
        r, g, b = (int(digits[i:i + 2], 16) for i in (0, 2, 4))
        return f"{48 if background else 38};2;{r};{g};{b}"
    bright = name.startswith("bright-")
    base = name.removeprefix("bright-")
    if base in COLORS:
        offset = (100 if bright else 40) if background else (90 if bright else 30)
        return str(offset + COLORS.index(base))
    return None


def markup(text: str) -> str:
    """Convert [style]...[/] markup into ANSI escape sequences."""
    out, i = [], 0
    while i < len(text):
        if text.startswith("[[", i):
            out.append("[")
            i += 2
            continue
        m = TAG_RE.match(text, i)
        if not m:
            out.append(text[i])
            i += 1
            continue
        tag = m.group(1)
        codes = []
        if tag == "/":
            codes = ["0"]
        else:
            for word in tag.lower().split():
                if word in ATTRS:
                    codes.append(ATTRS[word])
                elif word.startswith("on-"):
                    codes.append(_color_code(word[3:], True))
                else:
                    codes.append(_color_code(word, False))
        if not codes or None in codes:
            out.append(m.group(0))  # not markup after all, so keep it literally
        else:
            out.append(f"\x1b[{';'.join(codes)}m")
        i = m.end()
    return "".join(out)


class _Builder:
    def __init__(self, seed: int, run_cwd: str | None, base_dir: str | None):
        self.rec = Recording(80, 24)
        self.clock = 0.25
        self.cps = 16.0
        self.rng = random.Random(seed)
        self.max_lines = 0
        self.pending = 0.0  # scripted sleep not yet written out
        self.prompt = "\x1b[32m$\x1b[0m "
        self.prompt_shown = False
        self.run_cwd = run_cwd
        self.base_dir = base_dir  # where relative @theme paths start

    def emit(self, data: str, gap: float = 0.0) -> None:
        self.flush()
        self.clock += gap
        self.rec.events.append((self.clock, data))

    def flush(self) -> None:
        """Write out a pending @sleep as keep-alive events."""
        if self.pending <= 0:
            return
        end = self.clock + self.pending
        self.pending = 0.0
        while self.clock + KEEPALIVE < end:
            self.clock += KEEPALIVE
            self.rec.events.append((self.clock, ""))
        self.clock = end
        self.rec.events.append((self.clock, ""))

    def show_prompt(self) -> None:
        if not self.prompt_shown:
            self.emit(self.prompt, 0.05)
            self.prompt_shown = True

    def type_out(self, text: str) -> None:
        for ch in text:
            # Human-ish rhythm: jitter per key, a little longer after spaces.
            delay = self.rng.uniform(0.5, 1.5) / self.cps
            if ch == " ":
                delay *= 1.6
            self.emit(ch, delay)


def build(script_text: str, seed: int = 7, run_cwd: str | None = None,
          base_dir: str | None = None) -> Recording:
    """Turn demo-script text into a Recording.

    run_cwd is where `$` commands run and base_dir is where relative @theme
    paths are looked up. Both default to the current directory.
    """
    b = _Builder(seed, run_cwd, base_dir or run_cwd)
    for lineno, raw in enumerate(script_text.splitlines(), 1):
        line = raw.lstrip()
        if not line or line.startswith("#"):
            continue
        kind, _, arg = line.partition(" ")
        if "\t" in kind:  # a tab works as the separator too
            kind, _, rest = kind.partition("\t")
            arg = rest + (" " + arg if arg else "")
        try:
            _directive(b, kind, arg)
        except (ValueError, OSError) as e:
            raise ValueError(f"line {lineno}: {e}") from None

    # End on an idle prompt, like a real session. A trailing @sleep holds
    # that final screen instead of delaying the prompt.
    hold, b.pending = b.pending, 0.0
    b.show_prompt()
    b.pending = hold
    b.flush()
    return b.rec


def _number(arg: str, what: str, minimum: float = 0, integer: bool = False, above: bool = False):
    try:
        value = int(arg) if integer else float(arg)
    except ValueError:
        raise ValueError(f"{what} needs a number, got {arg.strip()!r}") from None
    if not math.isfinite(value):
        raise ValueError(f"{what} must be a finite number")
    if value < minimum or (above and value == minimum):
        raise ValueError(f"{what} must be {'more than' if above else 'at least'} {minimum}")
    return value


def _directive(b: _Builder, kind: str, arg: str) -> None:
    """Apply one script line to the builder."""
    if kind == "@size":
        cols, sep, rows = arg.strip().lower().partition("x")
        if not sep:
            raise ValueError(f"@size needs COLSxROWS, such as 80x24, got {arg.strip()!r}")
        b.rec.cols = _number(cols, "@size columns", 1, integer=True)
        b.rec.rows = _number(rows, "@size rows", 1, integer=True)
    elif kind == "@title":
        b.rec.title = arg.strip()
    elif kind == "@theme":
        ref = arg.strip()
        if not ref:
            raise ValueError("@theme needs a theme name or a path to a .json theme")
        if is_path(ref) and not os.path.isabs(ref):
            ref = os.path.normpath(os.path.join(b.base_dir or os.getcwd(), ref))
        b.rec.theme = ref
    elif kind == "@prompt":
        b.prompt = markup(arg) + " "
    elif kind == "@typing":
        b.cps = _number(arg, "@typing", 0, above=True)
    elif kind == "@cwd":
        run_cwd = os.path.normpath(os.path.join(b.run_cwd or os.getcwd(), arg.strip()))
        if not os.path.isdir(run_cwd):
            raise ValueError(f"@cwd: no such directory: {run_cwd}")
        b.run_cwd = run_cwd
    elif kind == "@max-lines":
        b.max_lines = _number(arg, "@max-lines", 0, integer=True)
    elif kind == "@sleep":
        b.pending += _number(arg, "@sleep", 0)
    elif kind == "@clear":
        b.emit("\x1b[H\x1b[2J", 0.1)
        b.prompt_shown = False
    elif kind in ("$", "~"):
        b.show_prompt()
        b.clock += 0.6  # beat before typing starts
        b.type_out(arg)
        b.clock += 0.35  # beat before pressing enter
        b.emit("\r\n")
        if kind == "$" and arg.strip():
            _run(b, arg, b.run_cwd)
        b.prompt_shown = False
        b.clock += 0.2
    elif kind == ">":
        b.emit(markup(arg) + "\x1b[0m\r\n", 0.06)
        b.prompt_shown = False
    else:
        raise ValueError(f"unknown directive {kind!r}")


def _run(b: _Builder, command: str, cwd: str | None) -> None:
    """Run a command for real and stream its output into the recording."""
    env = dict(os.environ, FORCE_COLOR="1", CLICOLOR_FORCE="1", PY_COLORS="1", PYTHONIOENCODING="utf-8",
               COLUMNS=str(b.rec.cols), LINES=str(b.rec.rows), TERM="xterm-256color", TERMREEL="1")
    start, base = time.monotonic(), b.clock
    shown, hidden = 0, 0  # output lines kept and dropped under @max-lines
    partial_hidden = False  # a dropped line that has not ended yet
    # stdin is closed so a command that asks for input fails instead of
    # waiting on (or eating) the keyboard of whoever runs termreel.
    with subprocess.Popen(command, shell=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            # Pipes give bare LF where a terminal would have produced CRLF.
            text = re.sub(r"(?<!\r)\n", "\r\n", decoder.decode(chunk))
            b.clock = base + (time.monotonic() - start)
            if text and b.max_lines:
                pieces = text.split("\r\n")
                keep = []
                for i, piece in enumerate(pieces):
                    ends = i < len(pieces) - 1  # every piece but the last ended with a newline
                    if shown < b.max_lines:
                        keep.append(piece + ("\r\n" if ends else ""))
                        shown += ends
                    elif ends:
                        hidden += 1
                        partial_hidden = False
                    elif piece:
                        partial_hidden = True
                text = "".join(keep)
            if text:
                b.rec.events.append((b.clock, text))
    b.clock = max(b.clock, base + (time.monotonic() - start))
    hidden += partial_hidden  # a last line with no newline still counts
    if hidden:
        b.emit("\x1b[0m" + markup(f"[dim]… {hidden} more line{'s' * (hidden != 1)}[/]") + "\r\n", 0.05)


def build_file(path: str, seed: int = 7) -> Recording:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    folder = os.path.dirname(os.path.abspath(path))
    try:
        return build(text, seed=seed, run_cwd=folder, base_dir=folder)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None
