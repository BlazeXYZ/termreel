"""Render a Recording as a self-contained animated SVG.

How it stays small:
  * Events are batched into frames at a fixed rate, and identical consecutive
    frames are merged.
  * Every distinct frame is drawn once into a vertical "filmstrip"; frames that
    reappear later (an idle prompt, say) are referenced again, not redrawn.
  * Every distinct row of styled text is defined once in <defs> and placed with
    <use>, so a line that stays on screen for 200 frames costs one definition.
  * Styles become short CSS classes instead of repeated fill attributes.
The animation is a single CSS keyframe track that slides the filmstrip with
steps(1,end) timing, so no JavaScript is needed and it plays inside an <img>
tag, which is how GitHub READMEs embed SVGs.
"""

from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from .cast import Recording
from .terminal import Style, Terminal, char_width
from .themes import THEMES, palette_color

FONT_STACK = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"


@dataclass
class Options:
    theme: str = "dracula"
    font_size: float = 14.0
    line_height: float = 1.3
    fps: int = 30
    speed: float = 1.0
    idle_limit: float = 1.5  # cap long pauses at this many seconds
    loop_delay: float = 3.0  # hold the last frame before looping
    loop: bool = True
    window: bool = True  # draw macOS-style window chrome
    title: str | None = None
    padding: float = 14.0
    cursor: bool = True
    at: float | None = None  # render a still frame at this time instead
    trim: bool = True  # drop blank rows below the last row ever used


@dataclass
class Stats:
    frames: int
    unique_frames: int
    unique_rows: int
    duration: float
    size: int


def build_frames(rec: Recording, opts: Options) -> list:
    """Replay the recording; return [(time, Snapshot)] with adjusted timing."""
    term = Terminal(rec.cols, rec.rows)
    frames = []
    interval = 1.0 / max(1, opts.fps)
    clock, last_t, bucket_start = 0.0, 0.0, None

    def push(t: float) -> None:
        snap = term.snapshot()
        if frames and frames[-1][1] == snap:
            return
        frames.append((t, snap))

    for t, data in rec.events:
        gap = max(0.0, t - last_t)
        clock += min(gap, opts.idle_limit) / opts.speed
        last_t = t
        if opts.at is not None and clock > opts.at:
            break
        if bucket_start is not None and clock - bucket_start >= interval:
            push(bucket_start)
            bucket_start = None
        if bucket_start is None:
            bucket_start = clock
        term.feed(data)
    if bucket_start is not None:
        push(bucket_start)
    if not frames:
        push(0.0)
    if frames[0][0] > 0:
        # Show an empty terminal until the first output arrives.
        frames.insert(0, (0.0, Terminal(rec.cols, rec.rows).snapshot()))
    return frames


class _StyleSheet:
    """Maps terminal styles to compact CSS classes."""

    def __init__(self, theme: dict):
        self.theme = theme
        self.fg_classes: dict = {}
        self.bg_classes: dict = {}

    def color(self, c, default: str) -> str:
        if c is None:
            return default
        if isinstance(c, int):
            return palette_color(self.theme, c)
        return c

    def resolve(self, s: Style) -> tuple:
        fg = self.color(s.fg, self.theme["fg"])
        bg = self.color(s.bg, None) if s.bg is not None else None
        if s.inverse:
            fg, bg = (bg or self.theme["bg"]), fg
        return fg, bg

    def text_class(self, s: Style, fg: str) -> str:
        key = (fg, s.bold, s.dim, s.italic, s.underline, s.strike)
        if key == (self.theme["fg"], False, False, False, False, False):
            return ""
        if key not in self.fg_classes:
            self.fg_classes[key] = f"f{len(self.fg_classes):x}"
        return self.fg_classes[key]

    def bg_class(self, bg: str) -> str:
        if bg not in self.bg_classes:
            self.bg_classes[bg] = f"b{len(self.bg_classes):x}"
        return self.bg_classes[bg]

    def css(self) -> str:
        rules = []
        for (fg, bold, dim, italic, underline, strike), name in self.fg_classes.items():
            decl = [f"fill:{fg}"]
            if bold:
                decl.append("font-weight:bold")
            if dim:
                decl.append("opacity:.6")
            if italic:
                decl.append("font-style:italic")
            deco = " ".join(d for d, on in (("underline", underline), ("line-through", strike)) if on)
            if deco:
                decl.append(f"text-decoration:{deco}")
            rules.append(f".{name}{{{';'.join(decl)}}}")
        for bg, name in self.bg_classes.items():
            rules.append(f".{name}{{fill:{bg}}}")
        return "".join(rules)


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def render(rec: Recording, opts: Options | None = None) -> tuple[str, Stats]:
    opts = opts or Options()
    if opts.theme not in THEMES:
        raise ValueError(f"unknown theme {opts.theme!r}; choose from {', '.join(THEMES)}")
    theme = THEMES[opts.theme]
    sheet = _StyleSheet(theme)

    frames = build_frames(rec, opts)
    if opts.at is not None:
        frames = frames[-1:]

    cw = opts.font_size * 0.6
    lh = opts.font_size * opts.line_height
    baseline = lh * 0.5 + opts.font_size * 0.36

    rows = rec.rows
    if opts.trim:
        used = 0
        for _, snap in frames:
            for y, runs in enumerate(snap.rows):
                if any(text.strip() or style.bg is not None or style.inverse for text, style in runs):
                    used = max(used, y + 1)
            if snap.cursor_visible:
                used = max(used, snap.cursor[0] + 1)
        rows = max(1, min(rec.rows, used))

    # ---- rows -> <defs>
    row_ids: dict = {}
    defs: list = []

    recent: list = []  # (glyphs, id) of the last few rows defined

    def draw_blocks(col: int, text: str, fg: str) -> list:
        """Block elements as shapes: fonts leave seams between █ glyphs."""
        out, k = [], 0
        cls = sheet.bg_class(fg)
        while k < len(text):
            n = 1
            while k + n < len(text) and text[k + n] == text[k]:
                n += 1
            bx, by, bw, bh, alpha = BLOCKS[text[k]]
            attrs = f'x="{_num((col + k + bx) * cw)}" width="{_num((bw + n - 1) * cw + 0.3)}" height="{_num(bh * lh + 0.3)}"'
            if by:
                attrs += f' y="{_num(by * lh)}"'
            if alpha < 1:
                attrs += f' opacity="{alpha}"'
            out.append(f'<rect {attrs} class="{cls}"/>')
            k += n
        return out

    def draw(glyphs: tuple) -> list:
        """SVG elements for a sequence of (col, char, style) glyphs."""
        parts, i = [], 0
        while i < len(glyphs):
            col, _, style = glyphs[i]
            j = i
            while j < len(glyphs) and glyphs[j][2] == style:
                j += 1
            text = "".join(g[1] for g in glyphs[i:j])
            width = _width(text)
            fg, bg = sheet.resolve(style)
            x = _num(col * cw)
            if bg is not None:
                parts.append(f'<rect x="{x}" width="{_num(width * cw + 0.5)}" height="{_num(lh + 0.5)}" '
                             f'class="{sheet.bg_class(bg)}"/>')
            for seg_col, seg, is_block in _segments(glyphs[i:j]):
                if is_block:
                    parts.extend(draw_blocks(seg_col, seg, fg))
                    continue
                shown = seg.rstrip()
                if not shown.strip():
                    continue
                cls = sheet.text_class(style, fg)
                cls_attr = f' class="{cls}"' if cls else ""
                # textLength pins each run to the cell grid even if the
                # viewer's monospace font is slightly wider or narrower.
                parts.append(f'<text x="{_num(seg_col * cw)}" y="{_num(baseline)}"{cls_attr} '
                             f'textLength="{_num(_width(shown) * cw)}">{escape(shown)}</text>')
            i = j
        return parts

    def row_ref(runs: tuple) -> str | None:
        if runs in row_ids:
            return row_ids[runs]
        glyphs = _glyphs(runs)
        if not glyphs:
            row_ids[runs] = None
            return None
        # Prefix chaining: while a command is being typed, each new row is an
        # earlier row plus a few characters. Reuse that row and draw only the
        # new tail, instead of redrawing the whole line on every keystroke.
        base = max((r for r in recent if len(r[0]) < len(glyphs) and glyphs[:len(r[0])] == r[0]),
                   key=lambda r: len(r[0]), default=None)
        if base:
            parts = [f'<use href="#{base[1]}"/>'] + draw(glyphs[len(base[0]):])
        else:
            parts = draw(glyphs)
        ref = _ident(len(defs))
        defs.append(f'<g id="{ref}">{"".join(parts)}</g>')
        row_ids[runs] = ref
        recent.append((glyphs, ref))
        del recent[:-16]
        return ref

    # ---- frames -> filmstrip. The cursor is not part of a frame: it lives on
    # its own animation track, so frames that differ only by cursor position
    # are drawn once.
    frame_index: dict = {}
    strip: list = []
    timeline: list = []  # (time, strip index, cursor position or None)
    fh = rows * lh
    for t, snap in frames:
        key = snap.rows[:rows]
        if key not in frame_index:
            idx = len(strip)
            frame_index[key] = idx
            uses = []
            for y, runs in enumerate(key):
                ref = row_ref(runs)
                if ref:
                    uses.append(f'<use href="#{ref}" y="{_num(y * lh)}"/>')
            strip.append(f'<g transform="translate(0 {_num(idx * fh)})">{"".join(uses)}</g>')
        visible = opts.cursor and snap.cursor_visible and snap.cursor[0] < rows
        timeline.append((t, frame_index[key], snap.cursor if visible else None))

    # ---- geometry
    pad = opts.padding
    bar = 30.0 if opts.window else 0.0
    tw, th = rec.cols * cw, fh
    width, height = tw + 2 * pad, th + 2 * pad + bar
    title = opts.title if opts.title is not None else (rec.title or frames[-1][1].title)

    # ---- animation
    def strip_pos(idx: int) -> str:
        return f"translateY({_num(-idx * fh)}px)"

    def cursor_pos(cur) -> str:
        if cur is None:
            return "translate(-99px,-99px)"  # parked outside the clip
        return f"translate({_num(cur[1] * cw)}px,{_num(cur[0] * lh)}px)"

    def track(name: str, values: list) -> str:
        keys, prev = [], None
        for t, value in values:
            if value != prev:
                keys.append(f"{_num(t / total * 100)}%{{transform:{value}}}")
                prev = value
        return f"@keyframes {name}{{{''.join(keys)}}}"

    total = max(frames[-1][0] + opts.loop_delay, 0.1)
    animated = len({(i, c) for _, i, c in timeline}) > 1 and opts.at is None
    _, last_idx, last_cur = timeline[-1]
    css = [
        f"svg{{font-family:{FONT_STACK};font-size:{_num(opts.font_size)}px}}",
        f"text{{fill:{theme['fg']};white-space:pre}}",
        f".cur{{fill:{theme['cursor']};opacity:.75}}",
        sheet.css(),
        f".s{{transform:{strip_pos(last_idx)}}}.c{{transform:{cursor_pos(last_cur)}}}",
    ]
    if animated:
        iteration = "infinite" if opts.loop else "1 forwards"
        css.append(f".s{{animation:k {_num(total)}s steps(1,end) {iteration}}}")
        css.append(f".c{{animation:kc {_num(total)}s steps(1,end) {iteration}}}")
        css.append(track("k", [(t, strip_pos(i)) for t, i, _ in timeline]))
        css.append(track("kc", [(t, cursor_pos(c)) for t, _, c in timeline]))
        css.append(".cur{animation:blink 1.06s step-end infinite}@keyframes blink{50%{opacity:0}}")
        # Viewers who ask for reduced motion get the final frame as a still.
        css.append("@media (prefers-reduced-motion:reduce){.s,.c,.cur{animation:none}}")

    chrome = []
    chrome.append(f'<rect width="{_num(width)}" height="{_num(height)}" rx="9" fill="{theme["bg"]}"/>')
    if opts.window:
        chrome.append(f'<path d="M0 9a9 9 0 0 1 9-9h{_num(width - 18)}a9 9 0 0 1 9 9v{_num(bar - 9)}'
                      f'h-{_num(width)}z" fill="{theme["chrome"]}"/>')
        for i, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            chrome.append(f'<circle cx="{_num(pad + 6 + i * 20)}" cy="{_num(bar / 2)}" r="6" fill="{color}"/>')
        if title:
            chrome.append(f'<text x="{_num(width / 2)}" y="{_num(bar / 2 + 4.5)}" text-anchor="middle" '
                          f'style="font-size:12px;opacity:.65">{escape(title)}</text>')

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}" height="{_num(height)}" '
        f'viewBox="0 0 {_num(width)} {_num(height)}" xml:space="preserve" role="img" '
        f'aria-label="{escape(title or "Terminal recording", {chr(34): "&quot;"})}">'
        f'<style>{"".join(css)}</style>'
        f'{"".join(chrome)}'
        f'<defs>{"".join(defs)}</defs>'
        f'<svg x="{_num(pad)}" y="{_num(pad + bar)}" width="{_num(tw)}" height="{_num(th)}">'
        f'<g class="s">{"".join(strip)}</g>'
        f'<g class="c"><rect class="cur" width="{_num(cw)}" height="{_num(lh)}"/></g></svg></svg>\n'
    )
    stats = Stats(len(timeline), len(strip), len(defs), total if animated else 0.0, len(svg.encode()))
    return svg, stats


# Block elements (U+2580-U+2590, shades) -> (x, y, width, height, opacity) in cells.
BLOCKS = {
    "█": (0, 0, 1, 1, 1), "▀": (0, 0, 1, .5, 1), "▐": (.5, 0, .5, 1, 1),
    "░": (0, 0, 1, 1, .25), "▒": (0, 0, 1, 1, .5), "▓": (0, 0, 1, 1, .75),
}
for _k in range(1, 8):
    BLOCKS[chr(0x2580 + _k)] = (0, 1 - _k / 8, 1, _k / 8, 1)  # ▁ ▂ ... ▇ lower eighths
    BLOCKS[chr(0x2590 - _k)] = (0, 0, _k / 8, 1, 1)  # ▏ ▎ ... ▉ left eighths


def _segments(glyphs: tuple):
    """Split same-style glyphs into (col, text, is_block) segments."""
    seg_col, text, kind = None, "", None
    for col, ch, _ in glyphs:
        is_block = ch in BLOCKS
        if is_block != kind and text:
            yield seg_col, text, kind
            text = ""
        if not text:
            seg_col = col
        text += ch
        kind = is_block
    if text:
        yield seg_col, text, kind


def _width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def _invisible(ch: str, style: Style) -> bool:
    return ch == " " and style.bg is None and not style.inverse


def _glyphs(runs: tuple) -> tuple:
    """Flatten runs into (col, char, style) glyphs, minus trailing blank cells."""
    out, col = [], 0
    for text, style in runs:
        for ch in text:
            w = char_width(ch)
            if w == 0 and out:  # combining mark joins the previous glyph
                c, prev, s = out[-1]
                out[-1] = (c, prev + ch, s)
                continue
            out.append((col, ch, style))
            col += w
    while out and _invisible(out[-1][1], out[-1][2]):
        out.pop()
    return tuple(out)


def _ident(n: int) -> str:
    """Short element ids: a, b, ... z, ba, bb ... (never starting with a digit)."""
    letters = "abcdefghijklmnopqrstuvwxyz"
    out = letters[n % 26]
    n //= 26
    while n:
        out = letters[n % 26] + out
        n //= 26
    return out
