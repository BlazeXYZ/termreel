import json
import re
import unittest
import xml.etree.ElementTree as ET

from termreel import cast, script
from termreel.cast import Recording
from termreel.render import Options, build_frames, render
from termreel.terminal import Style, Terminal


def screen(data: str, cols: int = 20, rows: int = 5) -> Terminal:
    t = Terminal(cols, rows)
    t.feed(data)
    return t


class TerminalTests(unittest.TestCase):
    def test_plain_text_and_newlines(self):
        self.assertEqual(screen("hello\r\nworld").text(), "hello\nworld")

    def test_carriage_return_overwrites(self):
        self.assertEqual(screen("progress 10%\rprogress 99%").text(), "progress 99%")

    def test_wraps_at_right_margin(self):
        self.assertEqual(screen("abcdefgh", cols=5).text(), "abcde\nfgh")

    def test_exact_fit_line_does_not_add_blank_row(self):
        # Writing exactly `cols` chars then CRLF must not leave an empty line.
        self.assertEqual(screen("abcde\r\nxy", cols=5).text(), "abcde\nxy")

    def test_scrolls_when_full(self):
        self.assertEqual(screen("1\r\n2\r\n3\r\n4", rows=3).text(), "2\n3\n4")

    def test_cursor_movement_and_erase(self):
        t = screen("aaaa\r\nbbbb\x1b[1A\x1b[2G\x1b[K")
        self.assertEqual(t.text(), "a\nbbbb")

    def test_clear_screen(self):
        self.assertEqual(screen("junk\x1b[2J\x1b[Hok").text(), "ok")

    def test_sgr_colors(self):
        t = screen("\x1b[1;31mA\x1b[38;5;200mB\x1b[38;2;1;2;3mC\x1b[0mD")
        styles = [t.grid[0][i].style for i in range(4)]
        self.assertEqual(styles[0], Style(fg=1, bold=True))
        self.assertEqual(styles[1].fg, 200)
        self.assertEqual(styles[2].fg, "#010203")
        self.assertEqual(styles[3], Style())

    def test_colon_truecolor(self):
        t = screen("\x1b[38:2::255:0:16mX")
        self.assertEqual(t.grid[0][0].style.fg, "#ff0010")

    def test_bright_and_background(self):
        t = screen("\x1b[92;44mX")
        self.assertEqual((t.grid[0][0].style.fg, t.grid[0][0].style.bg), (10, 4))

    def test_wide_characters_take_two_cells(self):
        t = screen("日本x")
        self.assertEqual(t.cx, 5)
        self.assertEqual(t.text(), "日本x")

    def test_wide_char_wraps_instead_of_splitting(self):
        self.assertEqual(screen("abcd日", cols=5).text(), "abcd\n日")

    def test_osc_title(self):
        t = screen("\x1b]0;my title\x07visible")
        self.assertEqual((t.title, t.text()), ("my title", "visible"))

    def test_alternate_screen_restores(self):
        t = screen("main\x1b[?1049hfullscreen app\x1b[?1049l")
        self.assertEqual(t.text(), "main")

    def test_hide_cursor(self):
        self.assertFalse(screen("\x1b[?25l").cursor_visible)

    def test_insert_and_delete_chars(self):
        self.assertEqual(screen("abcdef\x1b[1;3H\x1b[2P").text(), "abef")
        self.assertEqual(screen("abef\x1b[1;3H\x1b[2@cd").text(), "abcdef")

    def test_scroll_region(self):
        t = screen("top\r\n1\r\n2\r\n3\r\nbottom", rows=5)
        t.feed("\x1b[2;4r\x1b[4;1H\nnew")
        self.assertEqual(t.text(), "top\n2\n3\nnew\nbottom")

    def test_split_escape_sequences_across_feeds(self):
        t = Terminal(10, 2)
        for ch in "\x1b[31mred":
            t.feed(ch)
        self.assertEqual((t.text(), t.grid[0][0].style.fg), ("red", 1))


class CastTests(unittest.TestCase):
    def test_roundtrip_v2(self):
        rec = Recording(40, 10, "t", [(0.5, "hi"), (1.25, "\x1b[31m!")])
        back = cast.loads(cast.dumps(rec))
        self.assertEqual((back.cols, back.rows, back.title, back.events), (40, 10, "t", rec.events))

    def test_reads_v3_relative_times(self):
        text = "\n".join([
            json.dumps({"version": 3, "term": {"cols": 30, "rows": 5}}),
            "# a comment",
            json.dumps([0.5, "o", "a"]),
            json.dumps([0.25, "i", "x"]),
            json.dumps([1.0, "o", "b"]),
        ])
        rec = cast.loads(text)
        self.assertEqual((rec.cols, rec.events), (30, [(0.5, "a"), (1.75, "b")]))


class ScriptTests(unittest.TestCase):
    def test_markup(self):
        self.assertEqual(script.markup("[bold red]x[/]"), "\x1b[1;31mx\x1b[0m")
        self.assertEqual(script.markup("[on-bright-blue #ff0000]x"), "\x1b[104;38;2;255;0;0mx")
        self.assertEqual(script.markup("[[not markup] [nope]"), "[not markup] [nope]")

    def test_build_types_then_prints(self):
        rec = script.build("@size 30x4\n@prompt $\n~ ls\n> [green]a.txt[/]\n")
        self.assertEqual((rec.cols, rec.rows), (30, 4))
        t = Terminal(rec.cols, rec.rows)
        for _, data in rec.events:
            t.feed(data)
        self.assertEqual(t.text(), "$ ls\na.txt\n$")
        times = [e[0] for e in rec.events]
        self.assertEqual(times, sorted(times))

    def test_runs_real_commands(self):
        rec = script.build("$ python -c \"print('from a subprocess')\"\n")
        self.assertIn("from a subprocess", "".join(d for _, d in rec.events))

    def test_typing_is_reproducible(self):
        a = script.build("~ hello world", seed=3).events
        b = script.build("~ hello world", seed=3).events
        self.assertEqual(a, b)

    def test_unknown_directive(self):
        with self.assertRaises(ValueError):
            script.build("@nonsense 1")


class RenderTests(unittest.TestCase):
    def rec(self):
        return script.build("@size 30x6\n~ echo hi\n> hi\n~ echo hi\n> hi\n")

    def test_output_is_valid_xml(self):
        svg, _ = render(self.rec())
        root = ET.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))

    def test_escapes_markup_characters(self):
        rec = Recording(20, 2, "", [(0, "<a & b>")])
        svg, _ = render(rec)
        ET.fromstring(svg)
        self.assertIn("&lt;a &amp; b&gt;", svg)

    def test_identical_rows_are_defined_once(self):
        svg, stats = render(self.rec())
        # "hi" appears on screen twice for many frames but is defined once.
        self.assertEqual(svg.count(">hi</text>"), 1)
        self.assertLess(stats.unique_rows, stats.frames)

    def test_prefix_chaining_while_typing(self):
        svg, _ = render(script.build("~ abcdefghij"))
        # Each keystroke's row reuses the previous row rather than redrawing it.
        self.assertNotIn(">$ abcdefghij</text>", svg)
        self.assertGreater(len(re.findall(r'<g id="\w+"><use href=', svg)), 5)

    def test_idle_limit_and_speed(self):
        rec = Recording(10, 2, "", [(0, "a"), (60, "b"), (61, "c")])
        frames = build_frames(rec, Options(idle_limit=2, speed=2))
        self.assertAlmostEqual(frames[-1][0], (2 + 1) / 2)

    def test_still_frame(self):
        svg, stats = render(self.rec(), Options(at=0.0001))
        self.assertNotIn("@keyframes k", svg)
        self.assertEqual(stats.unique_frames, 1)

    def test_block_elements_become_rects(self):
        svg, _ = render(Recording(10, 1, "", [(0, "\x1b[32m█████\x1b[0m")]))
        self.assertNotIn("█", svg)
        self.assertEqual(svg.count('<rect x="0" width="42.3"'), 1)  # one merged rect

    def test_trims_unused_rows(self):
        svg, _ = render(Recording(10, 24, "", [(0, "one line")]), Options(window=False))
        height = float(ET.fromstring(svg).get("height"))
        self.assertLess(height, 60)

    def test_every_theme_renders(self):
        from termreel.themes import THEMES
        for name in THEMES:
            ET.fromstring(render(self.rec(), Options(theme=name))[0])

    def test_unknown_theme(self):
        with self.assertRaises(ValueError):
            render(self.rec(), Options(theme="nope"))


if __name__ == "__main__":
    unittest.main()
