"""Reading and writing asciinema cast files (v2 and v3).

termreel writes v2, the format almost every tool understands, and reads both
v2 (absolute timestamps) and v3 (relative intervals).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


@dataclass
class Recording:
    cols: int = 80
    rows: int = 24
    title: str = ""
    # (seconds since start, output text); only output ("o") events are kept.
    events: list = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.events[-1][0] if self.events else 0.0


def load(path: str) -> Recording:
    with open(path, encoding="utf-8") as f:
        return loads(f.read())


def loads(text: str) -> Recording:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty cast file")
    header = json.loads(lines[0])
    version = header.get("version")
    if version == 2:
        rec = Recording(header["width"], header["height"], header.get("title", ""))
    elif version == 3:
        term = header.get("term", {})
        rec = Recording(term.get("cols", 80), term.get("rows", 24), header.get("title", ""))
    else:
        raise ValueError(f"unsupported cast version: {version!r}")

    clock = 0.0
    for line in lines[1:]:
        if line.lstrip().startswith("#"):
            continue  # v3 allows comment lines
        t, code, data = json.loads(line)
        clock = clock + t if version == 3 else t
        if code == "o":
            rec.events.append((clock, data))
        elif code == "r" and not rec.events:
            # A resize before any output just sets the initial size.
            cols, _, rows = data.partition("x")
            rec.cols, rec.rows = int(cols), int(rows)
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
    out += [json.dumps([round(t, 6), "o", data], ensure_ascii=False) for t, data in rec.events]
    return "\n".join(out) + "\n"


def save(rec: Recording, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(rec))
