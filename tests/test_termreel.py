import io
import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from termreel import cast, cli, script, themes
from termreel.cast import Recording
from termreel.render import Options, build_frames, render
from termreel.terminal import Style, Terminal


def snap_text(t: Terminal) -> list:
    """What the renderer sees: snapshot rows as plain text."""
    return ["".join(text for text, _ in row) for row in t.snapshot().rows]


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

    def test_overwriting_half_a_wide_char_blanks_the_other_half(self):
        # Writing over one half must not shift the rest of the line.
        self.assertEqual(snap_text(screen("日本x\ra"))[0].rstrip(), "a 本x")
        self.assertEqual(snap_text(screen("日本x\x1b[1;2Hb"))[0].rstrip(), " b本x")

    def test_combining_mark_after_wide_char(self):
        t = screen("日́x")
        self.assertEqual(t.grid[0][0].char, "日́")
        self.assertEqual(t.cx, 3)

    def test_zero_width_joiner_and_variation_selector(self):
        self.assertEqual(screen("a‍b️c").cx, 3)

    def test_underline_color_does_not_leak_into_attributes(self):
        t = screen("\x1b[31m\x1b[58;2;255;0;0mX\x1b[58:5:9mY")
        self.assertEqual(t.grid[0][0].style, Style(fg=1))
        self.assertEqual(t.grid[0][1].style, Style(fg=1))

    def test_colon_sgr_forms(self):
        t = screen("\x1b[38:2:1:2:3mA\x1b[38:2:0:4:5:6mB\x1b[48:5:17mC\x1b[4:3mD\x1b[4:0mE")
        cells = t.grid[0]
        self.assertEqual((cells[0].style.fg, cells[1].style.fg, cells[2].style.bg), ("#010203", "#040506", 17))
        self.assertTrue(cells[3].style.underline)
        self.assertFalse(cells[4].style.underline)

    def test_erase_scrollback_keeps_the_screen(self):
        self.assertEqual(screen("keep\x1b[3J").text(), "keep")

    def test_escape_inside_osc_starts_a_new_sequence(self):
        t = screen("\x1b]0;title\x1b[31mX")
        self.assertEqual((t.title, t.text(), t.grid[0][0].style.fg), ("title", "X", 1))

    def test_dcs_and_apc_strings_are_swallowed(self):
        self.assertEqual(screen("\x1bP1$r0m\x1b\\a\x1b_hidden\x07b").text(), "ab")

    def test_dec_line_drawing(self):
        self.assertEqual(screen("\x1b(0lqqk\x1b(B x").text(), "┌──┐ x")
        self.assertEqual(screen("\x1b)0a\x0eq\x0fq").text(), "a─q")

    def test_c0_controls_inside_csi(self):
        self.assertEqual(screen("ab\x1b[\r2Cx").text(), "abx")

    def test_huge_parameters_are_capped(self):
        t = screen("a\x1b[99999999999Sb\x1b[99999999999bc")
        self.assertIn("c", t.text())

    def test_repeat_and_tabs(self):
        self.assertEqual(screen("-\x1b[4b").text(), "-----")
        self.assertEqual(screen("\t\tx\x1b[2Zy").text(), " " * 8 + "y" + " " * 7 + "x")
        self.assertEqual(screen("a\x1b[2Ib").text(), "a" + " " * 15 + "b")

    def test_insert_mode(self):
        self.assertEqual(screen("abc\x1b[1;2H\x1b[4hXY\x1b[4lZ").text(), "aXYZc")

    def test_autowrap_off(self):
        self.assertEqual(screen("\x1b[?7labcdefg", cols=5).text(), "abcdg")

    def test_hidden_text(self):
        self.assertTrue(screen("\x1b[8mpw").grid[0][0].style.hidden)

    def test_resize_sequence(self):
        t = screen("1\r\n2\r\n3", cols=10, rows=3)
        t.feed("\x1b[8;2;4t")
        self.assertEqual((t.cols, t.rows, t.text()), (4, 2, "2\n3"))
        t.feed("\x1b[8;4;6t")
        self.assertEqual((t.cols, t.rows, len(t.grid), len(t.grid[0])), (6, 4, 4, 6))

    def test_soft_reset(self):
        t = screen("\x1b[?25l\x1b[1mA\x1b[!pB")
        self.assertTrue(t.cursor_visible)
        self.assertEqual(t.grid[0][1].style, Style())

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

    def test_reads_v1(self):
        doc = {"version": 1, "width": 20, "height": 3, "title": "old",
               "stdout": [[0.5, "a"], [0.25, "b"]]}
        rec = cast.loads(json.dumps(doc, indent=2))
        self.assertEqual((rec.cols, rec.title, rec.events), (20, "old", [(0.5, "a"), (0.75, "b")]))

    def test_resize_events_roundtrip_and_render(self):
        text = "\n".join([
            json.dumps({"version": 2, "width": 10, "height": 2}),
            json.dumps([0.0, "r", "12x3"]),
            json.dumps([0.5, "o", "hi"]),
            json.dumps([1.0, "r", "20x4"]),
        ])
        rec = cast.loads(text)
        self.assertEqual((rec.cols, rec.rows), (12, 3))
        self.assertEqual(rec.events[-1], (1.0, "\x1b[8;4;20t"))
        self.assertIn('[1.0, "r", "20x4"]', cast.dumps(rec))
        svg, _ = render(rec, Options(window=False, padding=0))
        self.assertEqual(ET.fromstring(svg).get("width"), "168")  # 20 cols * 8.4px

    def test_malformed_casts_raise_value_error(self):
        for text in ["", "not json", "[1, 2]", '{"version": 9}', '{"version": 2, "width": 0}',
                     '{"version": 2}\n[1, "o"]', '{"version": 2}\n["x", "o", "a"]']:
            with self.assertRaises(ValueError, msg=text):
                cast.loads(text)


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

    def test_cwd_is_relative_to_the_script_folder(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            os.mkdir(os.path.join(root, "docs"))
            rec = script.build("@cwd ..\n$ python -c \"import os; print(os.getcwd())\"\n",
                               run_cwd=os.path.join(root, "docs"))
            out = "".join(d for _, d in rec.events)
            self.assertIn(os.path.basename(root), out)
            self.assertNotIn("docs", out.split("os.getcwd())\"")[-1])

    def test_max_lines_trims_and_says_so(self):
        cmd = 'python -c "print(chr(10).join(str(i) for i in range(10)))"'
        rec = script.build(f"@size 30x20\n@max-lines 3\n$ {cmd}\n")
        t = Terminal(rec.cols, rec.rows)
        for _, data in rec.events:
            t.feed(data)
        self.assertIn("0\n1\n2\n… 7 more lines", t.text())
        self.assertNotIn("\n3\n", t.text())

    def test_typing_is_reproducible(self):
        a = script.build("~ hello world", seed=3).events
        b = script.build("~ hello world", seed=3).events
        self.assertEqual(a, b)

    def test_markup_accepts_uppercase_and_short_hex(self):
        self.assertEqual(script.markup("[#FF8800]x"), "\x1b[38;2;255;136;0mx")
        self.assertEqual(script.markup("[on-#f80]x"), "\x1b[48;2;255;136;0mx")

    def test_indented_lines_and_tabs(self):
        rec = script.build("  @size 20x3\n\t> hi\n    # comment\n~\tls\n")
        t = Terminal(rec.cols, rec.rows)
        for _, data in rec.events:
            t.feed(data)
        self.assertEqual(t.text(), "hi\n$ ls\n$")

    def test_sleep_is_not_capped_by_idle(self):
        rec = script.build("> a\n@sleep 5\n> b\n")
        frames = build_frames(rec, Options(idle_limit=1.5))
        text = lambda snap, y: "".join(run[0] for run in snap.rows[y]).strip()
        t_a = next(t for t, snap in frames if text(snap, 0) == "a")
        t_b = next(t for t, snap in frames if text(snap, 1) == "b")
        self.assertGreater(t_b - t_a, 4.9)

    def test_trailing_sleep_holds_the_final_prompt(self):
        rec = script.build("> a\n@sleep 3\n")
        self.assertEqual(rec.events[-1][1], "")
        prompt_time = next(t for t, d in rec.events if "$" in d)
        self.assertGreater(rec.duration - prompt_time, 2.9)

    def test_errors_name_the_line(self):
        for bad in ["@sleep -1", "@sleep nan", "@sleep inf", "@typing 0", "@size 80", "@size axb",
                    "@max-lines x", "@cwd does-not-exist", "@theme"]:
            with self.assertRaisesRegex(ValueError, r"^line 2: ", msg=bad):
                script.build("> ok\n" + bad)

    def test_build_file_errors_name_the_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "bad.reel")
            Path(path).write_text("@sleep x\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"bad\.reel: line 1: "):
                script.build_file(path)

    def test_theme_directive(self):
        self.assertEqual(script.build("@theme nord").theme, "nord")
        with tempfile.TemporaryDirectory() as root:
            rec = script.build("@theme my.json", base_dir=root)
            self.assertEqual(rec.theme, os.path.join(root, "my.json"))

    def test_commands_get_no_stdin(self):
        rec = script.build('$ python -c "import sys; print(repr(sys.stdin.read()))"\n')
        self.assertIn("''", "".join(d for _, d in rec.events))

    def test_max_lines_counts_a_last_line_without_newline(self):
        cmd = "python -c \"import sys; sys.stdout.write('1\\n2\\n3\\n4')\""
        rec = script.build(f"@max-lines 2\n$ {cmd}\n")
        self.assertIn("2 more lines", "".join(d for _, d in rec.events))

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

    def test_invalid_options_raise_value_error(self):
        for opts in [Options(speed=0), Options(fps=0), Options(idle_limit=0), Options(font_size=-1),
                     Options(loop_delay=-1)]:
            with self.assertRaises(ValueError):
                render(self.rec(), opts)

    def test_hidden_text_is_not_drawn(self):
        svg, _ = render(Recording(20, 1, "", [(0, "\x1b[8msecret\x1b[0m shown")]))
        self.assertNotIn("secret", svg)
        self.assertIn("shown", svg)

    def test_underlined_spaces_are_drawn(self):
        svg, _ = render(Recording(20, 1, "", [(0, "\x1b[4ma  \x1b[0m")]))
        self.assertIn(">a  </text>", svg)

    def test_long_recordings_keep_frames_apart(self):
        events = [(i * 0.5, f"\r{i:04d}") for i in range(1200)]
        svg, _ = render(Recording(10, 1, "", events), Options(idle_limit=1))
        keyframes = re.search(r"@keyframes k\{(.*?)\}\}", svg).group(1)
        percents = re.findall(r"([\d.]+)%", keyframes)
        self.assertEqual(len(percents), len(set(percents)))

    def test_bold_is_bright(self):
        theme = themes.load("dracula")
        svg, _ = render(Recording(10, 1, "", [(0, "\x1b[1;31mX")]),
                        Options(theme=replace(theme, bold_is_bright=True)))
        self.assertIn(theme.ansi[9], svg)

    def test_unknown_theme(self):
        with self.assertRaises(ValueError):
            render(self.rec(), Options(theme="nope"))


def write_file(folder: str, name: str, doc) -> str:
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc if isinstance(doc, str) else json.dumps(doc))
    return path


def run_cli(*argv) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.main(list(argv))
    return buf.getvalue()


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_builtin_and_template_files_pass_strict_checks(self):
        files = sorted(themes.BUILTIN_DIR.glob("*.json")) + [themes.TEMPLATE_PATH]
        self.assertGreaterEqual(len(files), 11)
        for path in files:
            theme, warnings = themes.check(path)
            self.assertEqual(warnings, [], path.name)
            self.assertEqual(len(theme.ansi), 16)

    def test_load_by_path_with_short_hex_and_list_palette(self):
        path = write_file(self.dir, "mine.json", {
            "name": "mine", "background": "#000", "foreground": "#EEEEEE",
            "palette": ["#100", "#200", "#300", "#400", "#500", "#600", "#700", "#800"],
        })
        theme = themes.load(path)
        self.assertEqual((theme.bg, theme.fg, theme.cursor), ("#000000", "#eeeeee", "#eeeeee"))
        self.assertEqual(theme.ansi[9], "#220000")  # brights copy the normal colors
        ET.fromstring(render(Recording(10, 1, "", [(0, "\x1b[31mhi")]), Options(theme=path))[0])

    def test_extends_merges_palette_and_resolves_relative_paths(self):
        write_file(self.dir, "base.json", {"extends": "nord", "foreground": "#ffffff"})
        child = write_file(self.dir, "child.json", {"name": "child", "extends": "base.json",
                                                    "palette": {"red": "#ff0000"}})
        theme, nord = themes.load(child), themes.load("nord")
        self.assertEqual((theme.name, theme.bg, theme.fg), ("child", nord.bg, "#ffffff"))
        self.assertEqual((theme.ansi[1], theme.ansi[2]), ("#ff0000", nord.ansi[2]))

    def test_errors_are_specific(self):
        base = {"background": "#000", "foreground": "#fff"}
        bad = [
            ("{", "invalid JSON"),
            ("[]", "JSON object"),
            ({**base, "pallete": {}}, "unknown key 'pallete'"),
            ({**base, "background": "black", "palette": {}}, "background"),
            (base, "missing required key 'palette'"),
            ({**base, "palette": {"red": "#f00"}}, "missing black"),
            ({**base, "palette": ["#000"] * 5}, "8 or 16"),
            ({**base, "palette": {"purple": "#f00"}}, "unknown color"),
            ({"extends": "loop.json"}, "loops back"),
            ({"extends": "no-such-theme"}, "unknown theme"),
            ({"extends": "nord", "buttons": ["#fff"]}, "buttons"),
            ({"extends": "nord", "bold_is_bright": "yes"}, "bold_is_bright"),
        ]
        for doc, message in bad:
            path = write_file(self.dir, "loop.json", doc)
            with self.assertRaises(themes.ThemeError, msg=doc) as ctx:
                themes.load(path)
            self.assertIn(message, str(ctx.exception))

    def test_comment_keys_are_ignored(self):
        path = write_file(self.dir, "c.json", {"_note": "hi", "$schema": "x", "extends": "nord"})
        self.assertEqual(themes.load(path).bg, themes.load("nord").bg)

    def test_check_warnings(self):
        palette = dict(zip(themes.COLOR_NAMES, ["#222222", "#f00", "#0f0", "#ff0", "#00f", "#f0f", "#0ff", "#fff"]))
        path = write_file(self.dir, "dim.json", {"name": "other", "background": "#222222",
                                                 "foreground": "#333333", "cursor": "#222222",
                                                 "palette": palette})
        _, warnings = themes.check(path)
        text = " ".join(warnings)
        for part in ("does not match the file name", "contrast", "cursor", "black and bright-black"):
            self.assertIn(part, text)

    def test_theme_path_env_finds_custom_themes_by_name(self):
        write_file(self.dir, "corp.json", {"extends": "nord", "background": "#123456"})
        old = os.environ.get("TERMREEL_THEME_PATH")
        os.environ["TERMREEL_THEME_PATH"] = self.dir
        try:
            self.assertEqual(themes.load("corp").bg, "#123456")
            self.assertIn("corp", [n for n, _, _ in themes.available()])
        finally:
            if old is None:
                del os.environ["TERMREEL_THEME_PATH"]
            else:
                os.environ["TERMREEL_THEME_PATH"] = old

    def test_cli_init_check_preview_show(self):
        path = os.path.join(self.dir, "fresh.json")
        run_cli("themes", "init", path)
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual((doc["name"], doc["author"]), ("fresh", ""))
        self.assertIn("_readme", doc)
        self.assertIn("ok", run_cli("themes", "check", "--strict", path))
        with self.assertRaises(SystemExit):
            run_cli("themes", "init", path)  # refuses to overwrite
        copy = os.path.join(self.dir, "copy.json")
        run_cli("themes", "init", copy, "--from", "nord")
        self.assertEqual(themes.load(copy).ansi, themes.load("nord").ansi)
        out = os.path.join(self.dir, "p.svg")
        run_cli("themes", "preview", path, "-o", out)
        ET.parse(out)
        self.assertEqual(json.loads(run_cli("themes", "show", "nord"))["name"], "nord")

    def test_cli_check_fails_on_bad_files(self):
        bad = write_file(self.dir, "bad.json", {"background": "#000"})
        with self.assertRaises(SystemExit):
            run_cli("themes", "check", bad)

    def test_cli_gallery(self):
        run_cli("themes", "gallery", self.dir)
        readme = Path(self.dir, "README.md").read_text(encoding="utf-8")
        for name in themes.builtin_names() + ["template"]:
            ET.parse(os.path.join(self.dir, f"{name}.svg"))
            self.assertIn(f"`{name}`", readme)

    def test_render_uses_script_theme_unless_overridden(self):
        write_file(self.dir, "pink.json", {"extends": "nord", "background": "#ff00ff"})
        reel = write_file(self.dir, "demo.reel", "@theme pink.json\n> hi\n")
        out = os.path.join(self.dir, "demo.svg")
        run_cli("render", reel, "-o", out)
        self.assertIn('fill="#ff00ff"', Path(out).read_text(encoding="utf-8"))
        run_cli("render", reel, "-o", out, "-t", "nord")
        self.assertNotIn('fill="#ff00ff"', Path(out).read_text(encoding="utf-8"))

    def test_cli_rejects_bad_numbers(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            run_cli("render", "x.reel", "--speed", "0")


class ContributedThemeTests(unittest.TestCase):
    """What a pull request adding a built-in theme runs into in CI.

    The built-in folder is swapped for a temporary copy, so the tests can add
    the kind of files a contributor might send.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.builtin = Path(self.tmp.name, "builtin")
        self.builtin.mkdir()
        for name in ("nord", "dracula"):
            (self.builtin / f"{name}.json").write_text(
                (themes.BUILTIN_DIR / f"{name}.json").read_text(encoding="utf-8"), encoding="utf-8")
        patcher = mock.patch.multiple(themes, BUILTIN_DIR=self.builtin, THEMES=themes._Builtins())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"})
        env.start()
        self.addCleanup(env.stop)

    def add(self, name: str, text: str) -> str:
        path = self.builtin / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def run_check(self, *paths) -> tuple:
        """Run `termreel themes check --strict`. Returns (output, failed)."""
        buf = io.StringIO()
        failed = False
        with redirect_stdout(buf):
            try:
                cli.main(["themes", "check", "--strict", *(paths or [str(self.builtin)])])
            except SystemExit:
                failed = True
        return buf.getvalue(), failed

    def test_a_broken_theme_does_not_break_the_others(self):
        self.add("broken.json", '{\n  "name": "broken",\n  "background": "#000",\n  "pallete": {}\n}\n')
        self.assertEqual(themes.load("nord").name, "nord")
        self.assertEqual(sorted(themes.THEMES), ["broken", "dracula", "nord"])
        with self.assertRaisesRegex(themes.ThemeError, "pallete"):
            themes.load("broken")

    def test_errors_are_reported_on_the_right_line(self):
        path = self.add("broken.json", '{\n  "name": "broken",\n  "background": "#000",\n  "pallete": {}\n}\n')
        out, failed = self.run_check()
        self.assertTrue(failed)
        self.assertIn("unknown key 'pallete'", out)
        self.assertIn(f"::error file={Path(path).as_posix().replace(':', '%3A')},line=4,", out)
        self.assertIn("ok       " + str(self.builtin / "nord.json"), out)  # the others still pass

    def test_invalid_json_points_at_the_bad_line(self):
        self.add("typo.json", '{\n  "name": "typo",\n  "background": "#000"\n  "foreground": "#fff"\n}\n')
        out, failed = self.run_check()
        self.assertTrue(failed)
        self.assertRegex(out, r"::error file=[^\n]*typo\.json,line=4,")

    def test_bad_color_and_warnings_point_at_their_key(self):
        self.add("dull.json", json.dumps({"name": "dull", "extends": "nord", "foreground": "#444b5a"}, indent=2))
        out, failed = self.run_check()
        self.assertTrue(failed)
        self.assertRegex(out, r"::error file=[^\n]*dull\.json,line=4,[^\n]*contrast")
        self.add("dull.json", json.dumps({"name": "dull", "extends": "nord", "cursor": "blue"}, indent=2))
        out, _ = self.run_check()
        self.assertRegex(out, r"::error file=[^\n]*dull\.json,line=4,[^\n]*cursor")

    def test_unknown_extends_points_at_extends(self):
        self.add("child.json", json.dumps({"name": "child", "extends": "nope"}, indent=2))
        out, failed = self.run_check()
        self.assertTrue(failed)
        self.assertRegex(out, r"::error file=[^\n]*child\.json,line=3,[^\n]*unknown theme 'nope'")

    def test_duplicate_names_are_caught(self):
        self.add("nord-copy.json", (self.builtin / "nord.json").read_text(encoding="utf-8"))
        out, failed = self.run_check()
        self.assertTrue(failed)
        self.assertIn("is already used by", out)

    def test_template_name_is_reserved(self):
        doc = json.loads((self.builtin / "nord.json").read_text(encoding="utf-8"))
        self.add("template.json", json.dumps({**doc, "name": "template"}, indent=2))
        out, failed = self.run_check(str(self.builtin), str(themes.TEMPLATE_PATH))
        self.assertTrue(failed)
        self.assertIn("reserved for the template theme", out)

    def test_a_good_contribution_passes(self):
        doc = json.loads((self.builtin / "nord.json").read_text(encoding="utf-8"))
        self.add("nord-warm.json", json.dumps({**doc, "name": "nord-warm", "description": "Warmer Nord."}, indent=2))
        out, failed = self.run_check(str(self.builtin), str(themes.TEMPLATE_PATH))
        self.assertFalse(failed, out)
        self.assertNotIn("::", out)

    def test_annotations_escape_special_characters(self):
        line = cli._annotation("error", "C:/a,b.json", "50% done\nnext", 3)
        self.assertEqual(line, "::error file=C%3A/a%2Cb.json,line=3,title=termreel theme check::50%25 done%0Anext")


if __name__ == "__main__":
    unittest.main()
