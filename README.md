# termreel

**Record terminal sessions and turn them into lightweight animated SVGs for your README.**

<p align="center"><img src="examples/demo.svg" alt="termreel demo: a build with live progress bars, a file table and a git push"></p>

GIFs of terminal demos are blurry, heavy and can't be selected or zoomed.
termreel replays your session through its own terminal emulator and writes a
single self-contained SVG: crisp at any zoom, themeable, usually tens of
kilobytes, and it animates inside a plain `<img>` tag, which is how GitHub
renders README images. No JavaScript, no runtime dependencies.

```bash
pip install .
termreel render examples/demo.reel -o demo.svg
```

## Features

- **Two ways to capture.** Record a live shell (`termreel record`), or write a
  *demo script*: a small text file that types commands at a human pace,
  actually runs them, and can fake output. Scripts give you reproducible demos
  that you can edit and re-render instead of re-recording.
- **A real terminal emulator.** Escape sequences are interpreted, not
  stripped: 16, 256 and truecolor SGR, cursor movement, erase, insert and
  delete, scroll regions, the alternate screen, `\r` progress bars,
  multi-line redraws, wide CJK and emoji characters, and OSC window titles.
- **Small output.** See [How it stays small](#how-it-stays-small).
- **Looks right.** Block characters (`█▓▒░▁▂▃`) are drawn as shapes so
  progress bars have no seams. Text runs are pinned to the cell grid so columns
  line up whatever monospace font the viewer has. The window chrome is
  optional.
- **Accessible.** It has `role="img"` and an `aria-label`. Viewers with
  `prefers-reduced-motion` see the final frame as a still image.
- **Six themes:** `dracula`, `one-dark`, `nord`, `monokai`, `solarized-dark`
  and `github-light`.
- **Compatible.** It reads asciinema `.cast` files (v2 and v3) and writes v2,
  so it works with recordings from other tools.

## Usage

```bash
termreel record session.cast                 # record your shell; exit to stop
termreel record demo.cast -c "npm test"      # record a single command
termreel render session.cast -o demo.svg     # .cast -> animated SVG
termreel render demo.reel -t nord            # script -> SVG in one step
termreel render demo.reel --at 4.5 -o still.svg   # a still frame at 4.5s
termreel script demo.reel -o demo.cast       # script -> .cast
termreel play demo.cast --speed 2            # replay in your terminal
termreel themes                              # preview the themes
```

Useful `render` options:

| option | default | |
|---|---|---|
| `-t, --theme` | `dracula` | color theme |
| `--speed` | `1.0` | playback speed multiplier |
| `--idle` | `1.5` | cap any pause at this many seconds |
| `--fps` | `30` | maximum frame rate; output arriving faster is batched |
| `--loop-delay` | `3.0` | how long to hold the last frame before looping |
| `--no-loop` | | play once and stop on the last frame |
| `--no-window` | | no title bar, just the terminal |
| `--at SECONDS` | | render a single still frame |
| `--font-size` | `14` | |
| `--cols / --rows` | from recording | override the terminal size |

## Demo scripts

```text
# examples/demo.reel
@size 64x18
@title termreel demo
@prompt [bold #61afef]~/site[/] [#c678dd]❯[/]
@typing 20

$ python build.py                 # typed, then really executed
~ git push                        # typed, but not executed...
> [dim]To github.com:you/site.git[/]    # ...so we print its output
>    [yellow]a1f9e3c..7bd20c4[/]  main -> main
@sleep 2
```

| line | meaning |
|---|---|
| `$ cmd` | type `cmd`, run it, record its output in real time |
| `~ cmd` | type `cmd` without running it |
| `> text` | print a line of output |
| `@prompt text` | the prompt shown before each command |
| `@size COLSxROWS`, `@title`, `@typing CPS`, `@sleep S`, `@clear` | setup and pacing |

Markup in `>` and `@prompt` lines: `[bold]`, `[dim]`, `[italic]`,
`[underline]`, `[strike]`, color names (`[red]`, `[bright-cyan]`), backgrounds
(`[on-blue]`), hex colors (`[#ff8800]`), combinations such as
`[bold red on-black]`, and `[/]` to reset. Write `[[` for a literal `[`.

Commands run by `$` get `FORCE_COLOR=1` and `CLICOLOR_FORCE=1`, so most tools
keep their colors even though their output goes to a pipe. Typing rhythm is
randomized but seeded, so re-rendering produces an identical file.

## How it works

```
 .cast file ─┐
             ├─► events (t, bytes) ─► Terminal emulator ─► snapshots ─► SVG renderer
 .reel script┘   idle-capped, sped up      (terminal.py)     per frame     (render.py)
```

1. **Emulation.** `Terminal` is a character-at-a-time state machine (ground,
   escape, CSI, OSC) that updates a grid of cells, each with a character and a
   style. Escape sequences split across chunks are handled because the state
   persists between `feed()` calls.
2. **Framing.** Events are bucketed at `--fps`, long pauses are capped, and
   each bucket produces one immutable, hashable `Snapshot`.
3. **Rendering.** The snapshots become SVG elements, using the
   deduplication described below.
4. **Animation.** All the distinct frames are stacked vertically into a
   *filmstrip* inside a clipping viewport. One CSS `@keyframes` track moves the
   strip to each frame using `steps(1,end)` timing, which holds every frame
   until the next keyframe. The cursor has its own track and its own blink
   animation.

### How it stays small

| technique | effect |
|---|---|
| **Frame dedup**: identical screens map to one filmstrip position | an idle prompt that reappears costs nothing |
| **Row dedup**: each distinct line of styled text is a `<defs>` entry placed with `<use>` | a line on screen for 200 frames is drawn once |
| **Prefix chaining**: a row that only extends a recent row is `<use>` of that row plus the new characters | typing a command adds about one glyph per frame, not a whole line |
| **Cursor on its own track** | frames that differ only in cursor position merge |
| **Styles as CSS classes**: `.f3{fill:#…;font-weight:bold}` | no repeated attributes |
| **Trimming**: rows that are never used are cropped | no empty space under short demos |

On `examples/hello.reel` (112 frames), prefix chaining and the cursor track
together took the output from 53.8 KB to 26.8 KB.

## Development

```bash
python -m unittest discover -s tests -v
```

The 35 tests cover the emulator (wrapping, scrolling, scroll regions, insert
and delete, SGR parsing including colon-form truecolor, wide characters, the
alternate screen, sequences split across chunks), cast v2 and v3 parsing,
script markup and timing, and the renderer's guarantees: valid XML, escaping,
deduplication, prefix chaining, idle capping, still frames and themes.

Project layout:

```
termreel/
  terminal.py   VT/xterm emulator -> Snapshots
  render.py     Snapshots -> animated SVG (dedup, filmstrip, CSS animation)
  script.py     .reel demo scripts and their markup
  record.py     live pty recording (pty on POSIX, pywinpty on Windows)
  cast.py       asciinema v2/v3 reader and v2 writer
  themes.py     color themes and the xterm 256-color palette
  cli.py        command-line entry point
```

Live recording on Windows needs `pip install pywinpty` (or
`pip install .[windows]`). Demo scripts work everywhere with no extra
packages.

## License

MIT
