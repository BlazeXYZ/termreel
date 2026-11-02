"""Scripted demos: describe a terminal session in a text file, get a recording.

Reproducible, editable, and it needs no pty, so it works the same on every OS.

    # comments start with '#'
    @size 80x16              terminal size (before any output)
    @title My CLI            window title
    @prompt [green]~/app[/] $        prompt shown before each command
    @typing 18               typing speed, characters per second
    @sleep 1.5               pause
    @clear                   clear the screen
    $ ls -la                 type the command, then really run it
    ~ deploy --prod          type the command but do not run it
    > [bold green]✓[/] done  print a line of output (markup allowed)

Markup: [bold], [dim], [italic], [underline], [strike], color names such as
[red] or [bright-cyan], backgrounds such as [on-blue], hex colors [#ff8800],
combined as [bold red on-black], closed with [/]. A literal '[' is '[['.
"""

from __future__ import annotations

import os
import random
import re
import subprocess

from .cast import Recording

COLORS = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white"]
ATTRS = {"bold": "1", "dim": "2", "italic": "3", "underline": "4", "inverse": "7", "strike": "9"}


def _color_code(name: str, background: bool) -> str | None:
    if re.fullmatch(r"#[0-9a-fA-F]{6}", name):
        r, g, b = (int(name[i:i + 2], 16) for i in (1, 3, 5))
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
        m = re.compile(r"\[(/|[a-z#0-9\- ]+)\]").match(text, i)
        if not m:
            out.append(text[i])
            i += 1
            continue
        tag = m.group(1)
        codes = []
        if tag == "/":
            codes = ["0"]
        else:
            for word in tag.split():
                if word in ATTRS:
                    codes.append(ATTRS[word])
                elif word.startswith("on-"):
                    codes.append(_color_code(word[3:], True))
                else:
                    codes.append(_color_code(word, False))
        if None in codes:
            out.append(m.group(0))  # not markup after all; keep it literally
        else:
            out.append(f"\x1b[{';'.join(codes)}m")
        i = m.end()
    return "".join(out)


class _Builder:
    def __init__(self, seed: int):
        self.rec = Recording(80, 24)
        self.clock = 0.25
        self.cps = 16.0
        self.rng = random.Random(seed)

    def emit(self, data: str, gap: float = 0.0) -> None:
        self.clock += gap
        self.rec.events.append((self.clock, data))

    def type_out(self, text: str) -> None:
        for ch in text:
            # Human-ish rhythm: jitter per key, a little longer after spaces.
            delay = self.rng.uniform(0.5, 1.5) / self.cps
            if ch == " ":
                delay *= 1.6
            self.emit(ch, delay)


def build(script_text: str, seed: int = 7, run_cwd: str | None = None) -> Recording:
    b = _Builder(seed)
    prompt = "\x1b[32m$\x1b[0m "
    prompt_shown = False

    def show_prompt() -> None:
        nonlocal prompt_shown
        if not prompt_shown:
            b.emit(prompt, 0.05)
            prompt_shown = True

    for lineno, raw in enumerate(script_text.splitlines(), 1):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        kind, _, arg = line.partition(" ")

        if kind == "@size":
            cols, _, rows = arg.lower().partition("x")
            b.rec.cols, b.rec.rows = int(cols), int(rows)
        elif kind == "@title":
            b.rec.title = arg
        elif kind == "@prompt":
            prompt = markup(arg) + " "
        elif kind == "@typing":
            b.cps = float(arg)
        elif kind == "@sleep":
            b.clock += float(arg)
        elif kind == "@clear":
            b.emit("\x1b[H\x1b[2J", 0.1)
            prompt_shown = False
        elif kind in ("$", "~"):
            show_prompt()
            b.clock += 0.6  # beat before typing starts
            b.type_out(arg)
            b.clock += 0.35  # beat before pressing enter
            b.emit("\r\n")
            if kind == "$":
                _run(b, arg, run_cwd)
            prompt_shown = False
            b.clock += 0.2
        elif kind == ">":
            b.emit(markup(arg) + "\x1b[0m\r\n", 0.06)
            prompt_shown = False
        else:
            raise ValueError(f"line {lineno}: unknown directive {kind!r}")

    show_prompt()  # end on an idle prompt, like a real session
    return b.rec


def _run(b: _Builder, command: str, cwd: str | None) -> None:
    """Run a command for real and stream its output into the recording."""
    import codecs
    import time

    env = dict(os.environ, FORCE_COLOR="1", CLICOLOR_FORCE="1", PY_COLORS="1", PYTHONIOENCODING="utf-8",
               COLUMNS=str(b.rec.cols), LINES=str(b.rec.rows), TERMREEL="1")
    start, base = time.monotonic(), b.clock
    with subprocess.Popen(command, shell=True, cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as proc:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while True:
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            # Pipes give bare LF where a terminal would have produced CRLF.
            text = re.sub(r"(?<!\r)\n", "\r\n", decoder.decode(chunk))
            b.clock = base + (time.monotonic() - start)
            if text:
                b.rec.events.append((b.clock, text))
    b.clock = max(b.clock, base + (time.monotonic() - start))


def build_file(path: str, seed: int = 7) -> Recording:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return build(text, seed=seed, run_cwd=os.path.dirname(os.path.abspath(path)))
