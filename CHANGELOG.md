# Changelog

## 0.2.0 (2026-11-16)

### Added

- Custom themes from JSON files. Use one with `-t my-theme.json`, with `@theme` in a demo script, or by name from your user theme folder or `TERMREEL_THEME_PATH`. Themes can `extend` another theme and change only a few colors.
- A documented `template` theme, and a guide at [docs/custom-themes.md](docs/custom-themes.md).
- New `themes` commands: `init`, `check`, `preview`, `show` and `gallery`. Plain `termreel themes` still lists the themes.
- Four new built-in themes: `gruvbox-dark`, `catppuccin-mocha`, `tokyo-night` and `solarized-light`. The built-in themes are now JSON files in the same format as custom ones.
- Themes can set the title text color, the window buttons, and `bold_is_bright`.
- A theme gallery in [docs/themes](docs/themes/README.md).
- An `@theme` script directive.
- Reading asciicast v1 files.
- Window resizes, from `r` events in cast files and from resizing the window during a live recording on Linux and macOS. The SVG is sized for the largest screen.
- Emulator support for the DEC line-drawing character set, insert mode, autowrap off, tabs forward and back (`CSI I`, `CSI Z`), repeat (`CSI b`), soft reset (`CSI ! p`) and hidden text (`SGR 8`).
- GitHub Actions workflows for tests, theme checks with an auto-updated gallery, and PyPI releases with trusted publishing.
- Theme pull requests from forks get problems marked on the exact line of the theme file, a run summary of the themes they add or edit, and previews of just those themes. After merging, the gallery is committed to `main`, or offered as a pull request if `main` is protected.
- `termreel themes check` catches two themes with the same name, and a built-in theme that takes the reserved name `template`.

### Fixed

- Overwriting half of a wide character (CJK or emoji) shifted the rest of the line one column to the left. The other half is now blanked, the same way xterm does it.
- An underline color (`58;2;r;g;b`) was read as ordinary attributes, so it could turn on dim text or reset the colors.
- `CSI 3 J` (clear scrollback) cleared the whole visible screen.
- An escape sequence right after an unfinished OSC string was printed as text, and DCS, APC and PM strings leaked onto the screen.
- Control characters inside a CSI sequence broke it instead of being carried out.
- Very large numbers in escape sequences could make the emulator loop for a very long time.
- Combining marks after a wide character were drawn in the wrong cell, and zero-width joiners and variation selectors took up a cell.
- `@sleep` pauses longer than `--idle` were shortened, and a `@sleep` at the end of a script delayed the final prompt instead of holding it.
- Uppercase hex colors in markup (`[#FF8800]`) were printed literally.
- Indented script lines and tab separators were rejected as unknown directives.
- Bad script values (`@sleep -1`, `@typing 0`, `@size 80`, a missing `@cwd` folder) crashed or misbehaved. They now give an error with the file and line number.
- Commands run by `$` could wait forever for keyboard input. Their input is now closed.
- `@max-lines` did not count a hidden last line that had no newline.
- `--speed 0`, `--fps 0` and other invalid options crashed with a traceback.
- A cast file with a missing or invalid header field crashed with a traceback.
- Underlined or struck-through spaces were not drawn.
- In long recordings, neighboring frames could round to the same animation keyframe.
- Live recording: a failed command could leave a second copy of termreel running, input reaching its end stopped the recording, and on Windows the last output before the program exited could be lost.
- `pyproject.toml` needed setuptools 77 or newer for its license field but asked for 68.

## 0.1.0

- First release: live recording, demo scripts, the terminal emulator, the animated SVG renderer and six themes.
