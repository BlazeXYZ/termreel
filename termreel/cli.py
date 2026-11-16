"""Command-line interface: termreel record | script | render | play | themes."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

from . import __version__, cast, script, themes
from .cast import RESIZE_RE
from .render import Options, render


def _load_any(path: str, seed: int) -> cast.Recording:
    """A .cast file is loaded as-is; anything else is treated as a demo script."""
    if path.lower().endswith(".cast"):
        return cast.load(path)
    return script.build_file(path, seed=seed)


def _human(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def _positive(kind=float, allow_zero: bool = False):
    """argparse type: a number that must be > 0 (or >= 0)."""
    def parse(text: str):
        try:
            value = kind(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
        if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
            raise argparse.ArgumentTypeError(f"must be {'0 or more' if allow_zero else 'more than 0'}: {text}")
        return value
    return parse


def _write(path: str, text: str) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _swatches(theme: themes.Theme) -> str:
    return "".join(
        f"\x1b[48;2;{int(c[1:3], 16)};{int(c[3:5], 16)};{int(c[5:7], 16)}m  " for c in theme.ansi[:8]
    ) + "\x1b[0m"


# ------------------------------------------------------------------ commands

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
        theme=themes.load(args.theme or rec.theme or themes.DEFAULT_THEME),
        font_size=args.font_size, fps=args.fps, speed=args.speed,
        idle_limit=args.idle, loop_delay=args.loop_delay, loop=not args.no_loop,
        window=not args.no_window, title=args.title, cursor=not args.no_cursor,
        at=args.at, trim=not args.no_trim, padding=args.padding,
    )
    svg, stats = render(rec, opts)
    out = args.output or os.path.splitext(args.input)[0] + ".svg"
    _write(out, svg)
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
            if RESIZE_RE.fullmatch(data):
                continue  # don't resize the viewer's own terminal
            sys.stdout.write(data)
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[0m\n")


def cmd_themes_list(args) -> None:
    for name, path, kind in themes.available():
        try:
            theme = themes.load_file(path)
        except themes.ThemeError as e:
            print(f"{name:<18}(invalid) {e}")
            continue
        note = theme.description if kind == "built-in" else f"[{kind}] {theme.description or path}"
        print(f"{name:<18}{_swatches(theme)}  {note}")
    dirs = os.pathsep.join(str(d) for d in themes.search_dirs())
    print(f"\nCustom themes are found by name in: {dirs}\n"
          f"or pass a path: termreel render demo.reel -t my-theme.json")


def cmd_themes_show(args) -> None:
    theme = themes.load(args.theme)
    print(json.dumps(theme.to_json(), indent=2))


def cmd_themes_init(args) -> None:
    path = args.path if args.path.lower().endswith(".json") else args.path + ".json"
    if os.path.exists(path) and not args.force:
        raise ValueError(f"{path} already exists (use --force to overwrite it)")
    name = Path(path).stem
    if args.base:
        doc = themes.load(args.base).to_json()
        doc["name"] = name
        doc["description"] = f"Based on {args.base}."
        doc.pop("author", None)
        text = json.dumps(doc, indent=2) + "\n"
    else:
        text = themes.TEMPLATE_PATH.read_text(encoding="utf-8")
        text = re.sub(r'"name": "template"', json.dumps({"name": name})[1:-1], text, count=1)
        text = re.sub(r'"description": "[^"]*"', '"description": ""', text, count=1)
        text = re.sub(r'"author": "[^"]*"', '"author": ""', text, count=1)
    _write(path, text)
    print(f"termreel: wrote {path}. Edit it, then try:\n"
          f"  termreel themes check {path}\n"
          f"  termreel themes preview {path}")


def _theme_files(paths: list) -> list:
    files = []
    for p in paths:
        if os.path.isdir(p):
            files += sorted(str(f) for f in Path(p).glob("*.json"))
        else:
            files.append(p)
    if not files:
        raise ValueError("no theme files found")
    return files


def cmd_themes_check(args) -> None:
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    failed = 0
    for path in _theme_files(args.paths):
        try:
            theme, warnings = themes.check(path)
        except (themes.ThemeError, OSError) as e:
            failed += 1
            print(f"error    {path}: {e}")
            if annotate:
                print(f"::error file={path}::{e}")
            continue
        status = "ok" if not warnings else ("error" if args.strict else "warning")
        print(f"{status:<9}{path} ({theme.name})")
        for w in warnings:
            print(f"           - {w}")
            if annotate:
                print(f"::{'error' if args.strict else 'warning'} file={path}::{w}")
        if warnings and args.strict:
            failed += 1
    if failed:
        sys.exit(f"termreel: {failed} theme file{'s' * (failed != 1)} failed the check")


def cmd_themes_preview(args) -> None:
    theme = themes.load(args.theme)
    out = args.output or f"{theme.name}.svg"
    _write(out, themes.preview_svg(theme, animated=args.animated))
    print(f"termreel: preview of {theme.name} -> {out}")


def cmd_themes_gallery(args) -> None:
    """Write a preview SVG for every built-in theme, plus a README table."""
    out = Path(args.output)
    names = themes.builtin_names() + ["template"]
    rows = []
    for name in names:
        theme = themes.load(name)
        _write(str(out / f"{name}.svg"), themes.preview_svg(theme))
        rows.append(f"| `{name}` | {theme.description} | <img src=\"{name}.svg\" alt=\"{name} theme preview\" width=\"440\"> |")
    readme = (
        "# Theme gallery\n\n"
        "Every built-in theme, plus the `template` theme that new themes start from. "
        "This page is generated by `termreel themes gallery docs/themes`, "
        "so edit the theme files, not this page.\n\n"
        "To make your own theme, see [the custom themes guide](../custom-themes.md).\n\n"
        "| Theme | Description | Preview |\n| --- | --- | --- |\n" + "\n".join(rows) + "\n"
    )
    _write(str(out / "README.md"), readme)
    print(f"termreel: {len(names)} theme previews -> {out}")


# ---------------------------------------------------------------------- main

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
    v.add_argument("-t", "--theme", help="a built-in theme name, a custom theme name, or a path to a "
                                         ".json theme (default: the script's @theme, else dracula)")
    v.add_argument("--font-size", type=_positive(), default=14.0)
    v.add_argument("--fps", type=_positive(int), default=30, help="maximum frame rate (default 30)")
    v.add_argument("--speed", type=_positive(), default=1.0, help="playback speed multiplier")
    v.add_argument("--idle", type=_positive(), default=1.5,
                   help="cap pauses in recordings at N seconds (default 1.5); script @sleeps are kept")
    v.add_argument("--loop-delay", type=_positive(allow_zero=True), default=3.0,
                   help="hold the end for N seconds")
    v.add_argument("--no-loop", action="store_true", help="play once and stop on the last frame")
    v.add_argument("--no-window", action="store_true", help="omit the window title bar")
    v.add_argument("--no-cursor", action="store_true")
    v.add_argument("--no-trim", action="store_true", help="keep unused rows at the bottom")
    v.add_argument("--title", help="window title (default: from the recording)")
    v.add_argument("--at", type=_positive(allow_zero=True), help="render a still image of the screen at N seconds")
    v.add_argument("--cols", type=_positive(int), help="override terminal width")
    v.add_argument("--rows", type=_positive(int), help="override terminal height")
    v.add_argument("--padding", type=_positive(allow_zero=True), default=14.0)
    v.add_argument("--seed", type=int, default=7, help=argparse.SUPPRESS)
    v.set_defaults(fn=cmd_render)

    pl = sub.add_parser("play", help="replay a recording in this terminal")
    pl.add_argument("input")
    pl.add_argument("--speed", type=_positive(), default=1.0)
    pl.add_argument("--idle", type=_positive(), default=1.5)
    pl.add_argument("--seed", type=int, default=7, help=argparse.SUPPRESS)
    pl.set_defaults(fn=cmd_play)

    th = sub.add_parser("themes", help="list, create, check and preview color themes")
    th.set_defaults(fn=cmd_themes_list)
    ts = th.add_subparsers(dest="action")
    ts.add_parser("list", help="list the themes you can use by name (the default)").set_defaults(fn=cmd_themes_list)

    t = ts.add_parser("show", help="print a theme as complete JSON")
    t.add_argument("theme", help="theme name or .json path")
    t.set_defaults(fn=cmd_themes_show)

    t = ts.add_parser("init", help="start a new theme file from the documented template")
    t.add_argument("path", help="file to create, such as my-theme.json")
    t.add_argument("--from", dest="base", metavar="THEME", help="copy the colors of an existing theme instead")
    t.add_argument("--force", action="store_true", help="overwrite the file if it exists")
    t.set_defaults(fn=cmd_themes_init)

    t = ts.add_parser("check", help="validate theme files (exit status 1 on errors)")
    t.add_argument("paths", nargs="+", help="theme files, or folders of them")
    t.add_argument("--strict", action="store_true", help="treat warnings (contrast, naming) as errors")
    t.set_defaults(fn=cmd_themes_check)

    t = ts.add_parser("preview", help="render a sample terminal in a theme")
    t.add_argument("theme", help="theme name or .json path")
    t.add_argument("-o", "--output", help="SVG to write (default: NAME.svg)")
    t.add_argument("--animated", action="store_true", help="animate the typing instead of a still")
    t.set_defaults(fn=cmd_themes_preview)

    t = ts.add_parser("gallery", help="write previews of every built-in theme and a README table")
    t.add_argument("output", nargs="?", default="docs/themes", help="folder (default: docs/themes)")
    t.set_defaults(fn=cmd_themes_gallery)

    args = p.parse_args(argv)
    if os.name == "nt":
        os.system("")  # enable VT escape processing in the Windows console
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    try:
        args.fn(args)
    except (OSError, ValueError) as e:
        sys.exit(f"termreel: {e}")
