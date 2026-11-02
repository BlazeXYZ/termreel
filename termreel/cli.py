"""Command-line interface: termreel record | script | render | play | themes."""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__, cast, script
from .render import Options, render
from .themes import THEMES


def _load_any(path: str, seed: int) -> cast.Recording:
    """A .cast file is loaded as-is; anything else is treated as a demo script."""
    if path.endswith(".cast"):
        return cast.load(path)
    return script.build_file(path, seed=seed)


def _human(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def cmd_record(args) -> None:
    from .record import record

    print(f"termreel: recording to {args.output}. Exit the shell (or press Ctrl-D) to stop.\n")
    rec = record(args.command, title=args.title or "")
    cast.save(rec, args.output)
    print(f"\ntermreel: saved {len(rec.events)} events, {rec.duration:.1f}s -> {args.output}")


def cmd_script(args) -> None:
    rec = script.build_file(args.input, seed=args.seed)
    out = args.output or os.path.splitext(args.input)[0] + ".cast"
    cast.save(rec, out)
    print(f"termreel: {rec.cols}x{rec.rows}, {rec.duration:.1f}s -> {out}")


def cmd_render(args) -> None:
    rec = _load_any(args.input, args.seed)
    if args.cols:
        rec.cols = args.cols
    if args.rows:
        rec.rows = args.rows
    opts = Options(
        theme=args.theme, font_size=args.font_size, fps=args.fps, speed=args.speed,
        idle_limit=args.idle, loop_delay=args.loop_delay, loop=not args.no_loop,
        window=not args.no_window, title=args.title, cursor=not args.no_cursor,
        at=args.at, trim=not args.no_trim, padding=args.padding,
    )
    svg, stats = render(rec, opts)
    out = args.output or os.path.splitext(args.input)[0] + ".svg"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(svg)
    print(f"termreel: {stats.frames} frames ({stats.unique_frames} unique), "
          f"{stats.unique_rows} unique rows, {stats.duration:.1f}s loop, "
          f"{_human(stats.size)} -> {out}")


def cmd_play(args) -> None:
    rec = _load_any(args.input, args.seed)
    clock = 0.0
    try:
        for t, data in rec.events:
            delay = min(t - clock, args.idle) / args.speed
            if delay > 0:
                time.sleep(delay)
            clock = t
            sys.stdout.write(data)
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[0m\n")


def cmd_themes(args) -> None:
    for name, theme in THEMES.items():
        swatches = "".join(
            f"\x1b[48;2;{int(c[1:3], 16)};{int(c[3:5], 16)};{int(c[5:7], 16)}m  " for c in theme["ansi"][:8]
        )
        print(f"{name:<16}{swatches}\x1b[0m")


def main(argv: list | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="termreel",
        description="Record terminal sessions and turn them into lightweight animated SVGs.",
    )
    p.add_argument("--version", action="version", version=f"termreel {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="record a live shell session to a .cast file")
    r.add_argument("output", nargs="?", default="session.cast")
    r.add_argument("-c", "--command", help="command to record (default: your shell)")
    r.add_argument("--title")
    r.set_defaults(fn=cmd_record)

    s = sub.add_parser("script", help="turn a demo script into a .cast file")
    s.add_argument("input")
    s.add_argument("-o", "--output")
    s.add_argument("--seed", type=int, default=7, help="seed for the typing rhythm")
    s.set_defaults(fn=cmd_script)

    v = sub.add_parser("render", help="render a .cast file or demo script to an animated SVG")
    v.add_argument("input")
    v.add_argument("-o", "--output")
    v.add_argument("-t", "--theme", default="dracula", choices=sorted(THEMES))
    v.add_argument("--font-size", type=float, default=14.0)
    v.add_argument("--fps", type=int, default=30, help="maximum frame rate (default 30)")
    v.add_argument("--speed", type=float, default=1.0, help="playback speed multiplier")
    v.add_argument("--idle", type=float, default=1.5, help="cap pauses at N seconds (default 1.5)")
    v.add_argument("--loop-delay", type=float, default=3.0, help="hold the end for N seconds")
    v.add_argument("--no-loop", action="store_true", help="play once and stop on the last frame")
    v.add_argument("--no-window", action="store_true", help="omit the window title bar")
    v.add_argument("--no-cursor", action="store_true")
    v.add_argument("--no-trim", action="store_true", help="keep unused rows at the bottom")
    v.add_argument("--title", help="window title (default: from the recording)")
    v.add_argument("--at", type=float, help="render a still image of the screen at N seconds")
    v.add_argument("--cols", type=int, help="override terminal width")
    v.add_argument("--rows", type=int, help="override terminal height")
    v.add_argument("--padding", type=float, default=14.0)
    v.add_argument("--seed", type=int, default=7, help=argparse.SUPPRESS)
    v.set_defaults(fn=cmd_render)

    pl = sub.add_parser("play", help="replay a recording in this terminal")
    pl.add_argument("input")
    pl.add_argument("--speed", type=float, default=1.0)
    pl.add_argument("--idle", type=float, default=1.5)
    pl.add_argument("--seed", type=int, default=7, help=argparse.SUPPRESS)
    pl.set_defaults(fn=cmd_play)

    th = sub.add_parser("themes", help="list the built-in color themes")
    th.set_defaults(fn=cmd_themes)

    args = p.parse_args(argv)
    if os.name == "nt":
        os.system("")  # enable VT escape processing in the Windows console
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        args.fn(args)
    except (OSError, ValueError) as e:
        sys.exit(f"termreel: {e}")
