"""Reading and writing asciinema cast files (v1, v2 and v3).

termreel writes v2, the format almost every tool understands, and reads v1
(one JSON document with relative delays), v2 (absolute timestamps) and v3
(relative intervals). Resize events become the xterm resize sequence, which
the emulator understands, and are written back out as "r" events.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .terminal import resize_sequence

RESIZE_RE = re.compile(r"\x1b\[8;(\d+);(\d+)t")


@dataclass
class Recording:
    cols: int = 80
    rows: int = 24
    title: str = ""
    # (seconds since start, output text); only output ("o") events are kept,
    # plus resizes, stored as the resize escape sequence.
    events: list = field(default_factory=list)
    theme: str | None = None  # set by a script's @theme; not saved to .cast files

    @property
    def duration(self) -> float:
        return self.events[-1][0] if self.events else 0.0


def load(path: str) -> Recording:
    with open(path, encoding="utf-8") as f:
        try:
            return loads(f.read())
        except ValueError as e:
            raise ValueError(f"{path}: {e}") from None


def _size(value, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"invalid {what} in cast header: {value!r}")
    return value


def _parse_resize(data) -> tuple | None:
    cols, sep, rows = str(data).partition("x")
    if sep and cols.isdigit() and rows.isdigit() and int(cols) and int(rows):
        return int(cols), int(rows)
    return None


def loads(text: str) -> Recording:
    if not text.strip():
        raise ValueError("empty cast file")
    try:
        doc = json.loads(text)
    except ValueError:
        doc = None  # v2/v3: one JSON value per line
    if isinstance(doc, dict) and doc.get("version") == 1:
        return _loads_v1(doc)

    lines = [ln for ln in text.splitlines() if ln.strip()]
    try:
        header = json.loads(lines[0])
    except ValueError as e:
        raise ValueError(f"line 1: the header is not valid JSON ({e})") from None
    if not isinstance(header, dict):
        raise ValueError("line 1: the header must be a JSON object")
    version = header.get("version")
    if version == 2:
        rec = Recording(_size(header.get("width", 80), "width"), _size(header.get("height", 24), "height"),
                        str(header.get("title") or ""))
    elif version == 3:
        term = header.get("term") or {}
        rec = Recording(_size(term.get("cols", 80), "cols"), _size(term.get("rows", 24), "rows"),
                        str(header.get("title") or ""))
    else:
        raise ValueError(f"unsupported cast version: {version!r}")

    clock = 0.0
    for n, line in enumerate(lines[1:], 2):
        if line.lstrip().startswith("#"):
            continue  # v3 allows comment lines
        try:
            t, code, data = json.loads(line)
            t = float(t)
        except (ValueError, TypeError):
            raise ValueError(f"line {n}: expected an event like [1.5, \"o\", \"text\"]") from None
        clock = clock + t if version == 3 else t
        if code == "o":
            rec.events.append((clock, str(data)))
        elif code == "r":
            size = _parse_resize(data)
            if size is None:
                continue
            if rec.events:
                rec.events.append((clock, resize_sequence(*size)))
            else:
                rec.cols, rec.rows = size  # before any output: just the initial size
    return rec


def _loads_v1(doc: dict) -> Recording:
    rec = Recording(_size(doc.get("width", 80), "width"), _size(doc.get("height", 24), "height"),
                    str(doc.get("title") or ""))
    clock = 0.0
    for n, frame in enumerate(doc.get("stdout") or []):
        try:
            delay, data = frame
            clock += float(delay)
        except (ValueError, TypeError):
            raise ValueError(f"stdout[{n}]: expected [delay, \"text\"]") from None
        rec.events.append((clock, str(data)))
    return rec


def dumps(rec: Recording) -> str:
    header = {
        "version": 2,
        "width": rec.cols,
        "height": rec.rows,
        "timestamp": int(time.time()),
        "env": {"TERM": "xterm-256color"},
    }
    if rec.title:
        header["title"] = rec.title
    out = [json.dumps(header)]
    for t, data in rec.events:
        m = RESIZE_RE.fullmatch(data)
        if m:
            out.append(json.dumps([round(t, 6), "r", f"{m.group(2)}x{m.group(1)}"]))
        else:
            out.append(json.dumps([round(t, 6), "o", data], ensure_ascii=False))
    return "\n".join(out) + "\n"


def save(rec: Recording, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(rec))
