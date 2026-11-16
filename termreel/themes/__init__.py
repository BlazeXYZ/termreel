"""Color themes: the built-in set, plus custom themes loaded from JSON files.

A theme file is a small JSON document; ``template.json`` next to this module
documents every key, and ``termreel themes init`` copies it. A theme reference
(``--theme``, ``@theme``, ``"extends"``) resolves in this order:

1. a path to a ``.json`` file (anything ending in .json or containing a slash);
2. a built-in theme (the files in ``builtin/``), or ``template``;
3. ``NAME.json`` in each directory listed in ``TERMREEL_THEME_PATH``, then in
   the user theme directory (``~/.config/termreel/themes``, or
   ``%APPDATA%\\termreel\\themes`` on Windows).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILTIN_DIR = HERE / "builtin"
TEMPLATE_PATH = HERE / "template.json"
DEFAULT_THEME = "dracula"

WINDOW_BUTTONS = ("#ff5f57", "#febc2e", "#28c840")
COLOR_NAMES = ("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")
PALETTE_NAMES = COLOR_NAMES + tuple("bright-" + n for n in COLOR_NAMES)
KEYS = ("name", "description", "author", "extends", "background", "foreground", "cursor",
        "chrome", "title", "buttons", "bold_is_bright", "palette")
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class ThemeError(ValueError):
    """A theme could not be found, read or validated."""


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str
    fg: str
    ansi: tuple  # the 16 ANSI colors, "#rrggbb"
    cursor: str
    chrome: str
    title: str
    buttons: tuple = WINDOW_BUTTONS
    bold_is_bright: bool = False
    description: str = ""
    author: str = ""
    source: str = "built-in"  # "built-in", or the file it was loaded from

    def to_json(self) -> dict:
        """The theme as a complete, standalone theme document."""
        doc = {"name": self.name}
        if self.description:
            doc["description"] = self.description
        if self.author:
            doc["author"] = self.author
        doc.update(background=self.bg, foreground=self.fg, cursor=self.cursor,
                   chrome=self.chrome, title=self.title, buttons=list(self.buttons),
                   bold_is_bright=self.bold_is_bright,
                   palette=dict(zip(PALETTE_NAMES, self.ansi)))
        return doc


# ------------------------------------------------------------------- colors

def _rgb(color: str) -> tuple:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _mix(color: str, other: str, t: float) -> str:
    a, b = _rgb(color), _rgb(other)
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def luminance(color: str) -> float:
    """WCAG relative luminance of a "#rrggbb" color."""
    def channel(v: int) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(v) for v in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colors, from 1 to 21."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def palette_color(theme: Theme, index: int) -> str:
    """Resolve an xterm 256-color index to a hex color under a theme."""
    if index < 16:
        return theme.ansi[index]
    if index < 232:
        i = index - 16
        steps = [0, 95, 135, 175, 215, 255]
        r, g, b = steps[i // 36], steps[(i // 6) % 6], steps[i % 6]
        return f"#{r:02x}{g:02x}{b:02x}"
    level = 8 + (index - 232) * 10
    return f"#{level:02x}{level:02x}{level:02x}"


# ------------------------------------------------------------------- lookup

def user_theme_dir() -> Path:
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "termreel" / "themes"
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "termreel" / "themes"


def search_dirs() -> list:
    """Directories searched for custom themes by name, in order."""
    dirs = [Path(p) for p in os.environ.get("TERMREEL_THEME_PATH", "").split(os.pathsep) if p]
    return dirs + [user_theme_dir()]


def builtin_names() -> list:
    return sorted(p.stem for p in BUILTIN_DIR.glob("*.json"))


def is_path(ref: str) -> bool:
    return ref.lower().endswith(".json") or "/" in ref or "\\" in ref


def locate(ref: str, relative_to: Path | None = None) -> Path:
    """Find the file behind a theme reference (see the module docstring)."""
    if is_path(ref):
        path = Path(ref).expanduser()
        if relative_to is not None and not path.is_absolute():
            path = relative_to / path
        if not path.is_file():
            raise ThemeError(f"theme file not found: {path}")
        return path
    if (BUILTIN_DIR / f"{ref}.json").is_file():
        return BUILTIN_DIR / f"{ref}.json"
    if ref == "template":
        return TEMPLATE_PATH
    for d in search_dirs():
        if (d / f"{ref}.json").is_file():
            return d / f"{ref}.json"
    raise ThemeError(f"unknown theme {ref!r}; built-in themes are {', '.join(builtin_names())}, "
                     f"or pass the path to a .json theme file (see `termreel themes`)")


def available() -> list:
    """[(name, path, kind)] for every theme that can be used by name."""
    out = [(n, BUILTIN_DIR / f"{n}.json", "built-in") for n in builtin_names()]
    out.append(("template", TEMPLATE_PATH, "template"))
    seen = {n for n, _, _ in out}
    for d in search_dirs():
        for p in sorted(d.glob("*.json")) if d.is_dir() else []:
            if p.stem not in seen:
                seen.add(p.stem)
                out.append((p.stem, p, "custom"))
    return out


# ------------------------------------------------------------------ loading

def _color(value, where: str) -> str:
    if isinstance(value, str):
        m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value.strip())
        if m:
            hexes = m.group(1).lower()
            return "#" + (hexes if len(hexes) == 6 else "".join(c * 2 for c in hexes))
    raise ThemeError(f"{where}: expected a color like \"#1e1e2e\", got {value!r}")


def _palette(value, where: str) -> dict:
    """A palette (list or name -> color mapping) as a partial name -> color dict."""
    if isinstance(value, list):
        if len(value) not in (8, 16):
            raise ThemeError(f"{where}: a palette list needs 8 or 16 colors, got {len(value)}")
        return {PALETTE_NAMES[i]: _color(c, f"{where}[{i}]") for i, c in enumerate(value)}
    if isinstance(value, dict):
        unknown = sorted(k for k in value if k not in PALETTE_NAMES and not k.startswith("_"))
        if unknown:
            raise ThemeError(f"{where}: unknown color name {unknown[0]!r}; "
                             f"use {', '.join(COLOR_NAMES)} and their bright- versions")
        return {k: _color(v, f"{where}.{k}") for k, v in value.items() if not k.startswith("_")}
    raise ThemeError(f"{where}: expected a list of colors or an object of named colors")


def _raw(path: Path, seen: frozenset = frozenset()) -> dict:
    """Read a theme file, apply its "extends" chain and check every key."""
    path = path.resolve()
    if path in seen:
        raise ThemeError(f"{path}: \"extends\" loops back to this file")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ThemeError(f"{path}: {e.strerror or e}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ThemeError(f"{path}: invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}") from None
    if not isinstance(data, dict):
        raise ThemeError(f"{path}: a theme must be a JSON object")
    data = {k: v for k, v in data.items() if not k.startswith(("_", "$"))}
    unknown = sorted(set(data) - set(KEYS))
    if unknown:
        raise ThemeError(f"{path}: unknown key {unknown[0]!r}; allowed keys are {', '.join(KEYS)}")
    if "palette" in data:
        data["palette"] = _palette(data["palette"], f"{path}: palette")

    parent = data.pop("extends", None)
    if parent is None:
        return data
    if not isinstance(parent, str) or not parent:
        raise ThemeError(f"{path}: \"extends\" must be a theme name or a path")
    try:
        base = _raw(locate(parent, relative_to=path.parent), seen | {path})
    except ThemeError as e:
        raise ThemeError(f"{path}: extends {parent!r}: {e}") from None
    for key in ("name", "description", "author"):
        base.pop(key, None)  # identity is not inherited
    palette = {**base.get("palette", {}), **data.get("palette", {})}
    base.update(data)
    if palette:
        base["palette"] = palette
    return base


def _build(data: dict, path: Path, source: str) -> Theme:
    where = str(path)
    for key in ("background", "foreground", "palette"):
        if key not in data:
            raise ThemeError(f"{where}: missing required key {key!r}")
    bg = _color(data["background"], f"{where}: background")
    fg = _color(data["foreground"], f"{where}: foreground")
    palette = data["palette"]
    missing = [n for n in COLOR_NAMES if n not in palette]
    if missing:
        raise ThemeError(f"{where}: palette is missing {', '.join(missing)}")
    ansi = tuple(palette.get(n) or palette[n.removeprefix("bright-")] for n in PALETTE_NAMES)

    default_chrome = _mix(bg, "#000000", 0.2 if luminance(bg) < 0.4 else 0.04)
    buttons = data.get("buttons", list(WINDOW_BUTTONS))
    if not isinstance(buttons, list) or len(buttons) != 3:
        raise ThemeError(f"{where}: buttons must be a list of 3 colors")
    bold_is_bright = data.get("bold_is_bright", False)
    if not isinstance(bold_is_bright, bool):
        raise ThemeError(f"{where}: bold_is_bright must be true or false")
    name = data.get("name", path.stem)
    for key in ("name", "description", "author"):
        if not isinstance(data.get(key, ""), str):
            raise ThemeError(f"{where}: {key} must be a string")
    return Theme(
        name=name,
        bg=bg,
        fg=fg,
        ansi=ansi,
        cursor=_color(data.get("cursor", fg), f"{where}: cursor"),
        chrome=_color(data.get("chrome", default_chrome), f"{where}: chrome"),
        title=_color(data.get("title", fg), f"{where}: title"),
        buttons=tuple(_color(c, f"{where}: buttons[{i}]") for i, c in enumerate(buttons)),
        bold_is_bright=bold_is_bright,
        description=data.get("description", ""),
        author=data.get("author", ""),
        source=source,
    )


def load_file(path) -> Theme:
    path = Path(path)
    source = "built-in" if path.resolve().parent == BUILTIN_DIR else str(path)
    return _build(_raw(path), path, source)


def load(ref, relative_to=None) -> Theme:
    """Resolve a Theme, a built-in or custom theme name, or a .json path."""
    if isinstance(ref, Theme):
        return ref
    if not isinstance(ref, str) or not ref:
        raise ThemeError("a theme name or path is required")
    if ref in THEMES:
        return THEMES[ref]
    return load_file(locate(ref, Path(relative_to) if relative_to else None))


def check(path) -> tuple:
    """Validate a theme file. Returns (theme, warnings); raises ThemeError."""
    path = Path(path)
    theme = load_file(path)
    warnings = []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "name" not in raw:
        warnings.append("no \"name\"; it defaults to the file name")
    elif not NAME_RE.fullmatch(theme.name):
        warnings.append(f"name {theme.name!r} should be lowercase words joined by dashes")
    elif theme.name != path.stem:
        warnings.append(f"name {theme.name!r} does not match the file name {path.name!r}")
    ratio = contrast(theme.fg, theme.bg)
    if ratio < 4.5:
        warnings.append(f"foreground/background contrast is {ratio:.2f}:1; aim for at least 4.5:1")
    if contrast(theme.cursor, theme.bg) < 1.5:
        warnings.append("the cursor is hard to see against the background")
    if theme.ansi[0] == theme.bg and theme.ansi[8] == theme.bg:
        warnings.append("black and bright-black both equal the background, so 'dim' greys vanish")
    return theme, warnings


THEMES = {name: load_file(BUILTIN_DIR / f"{name}.json") for name in builtin_names()}


# ------------------------------------------------------------------ previews

PREVIEW_SCRIPT = """\
@size 62x11
@typing 30
@prompt [bold blue]~/project[/] [magenta]❯[/]
~ termreel themes preview {name}
> [black]████[/][red]████[/][green]████[/][yellow]████[/][blue]████[/][magenta]████[/][cyan]████[/][white]████[/]  normal
> [bright-black]████[/][bright-red]████[/][bright-green]████[/][bright-yellow]████[/][bright-blue]████[/][bright-magenta]████[/][bright-cyan]████[/][bright-white]████[/]  bright
>
> [bold]bold[/]  [dim]dim[/]  [italic]italic[/]  [underline]underline[/]  [strike]strike[/]  [inverse] inverse [/]
> [green]✓[/] 42 passed  [yellow]▲[/] 2 skipped  [red]✗[/] 1 failed  [dim]in 0.84s[/]
> [bold blue]info[/] [cyan]https://example.com[/]  [magenta]v1.2.0[/]  [bright-black]# note[/]
"""


def preview_svg(theme: Theme, animated: bool = False) -> str:
    """A small SVG showing a theme's palette and text styles."""
    from ..render import Options, render
    from ..script import build

    rec = build(PREVIEW_SCRIPT.format(name=theme.name))
    rec.title = theme.name
    svg, _ = render(rec, Options(theme=theme, at=None if animated else 1e9))
    return svg
