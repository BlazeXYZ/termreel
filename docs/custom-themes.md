# Custom themes

This guide shows how to make your own termreel theme, try it out, and use it in your demos. It also covers how to add a theme to termreel itself. You don't need to know any Python. A theme is just a small JSON file.

If you only want to see what is already there, the [theme gallery](themes/README.md) has a preview of every built-in theme.

## 1. Start from the template

To begin with, create a new theme file:

```bash
termreel themes init my-theme.json
```

This copies [`template.json`](../termreel/themes/template.json), the tutorial theme that ships with termreel. Every setting in it has a comment next to it. Comments are keys that start with an underscore (`"_background"`, `"_palette"` and so on). JSON has no real comment syntax, so termreel ignores any key that starts with `_` or `$`. You can delete the comments once you know what each setting does, or keep them.

If you would rather tweak a theme you already like, copy its colors instead:

```bash
termreel themes init my-theme.json --from nord
```

## 2. Change the colors

Open the file in any editor. Colors are written as `"#rrggbb"`, or the short form `"#rgb"` (so `"#f80"` is the same as `"#ff8800"`).

These are the keys a theme can have:

| Key | Required | What it controls |
| --- | --- | --- |
| `name` | for themes added to termreel | the theme's name, which should match the file name |
| `description` | no | one line shown by `termreel themes` |
| `author` | no | who made it |
| `extends` | no | another theme to start from (see step 5) |
| `background` | yes | the terminal background |
| `foreground` | yes | text that has no color of its own |
| `cursor` | no | the block cursor. Defaults to the foreground. |
| `chrome` | no | the window title bar. Defaults to a slightly darker background. |
| `title` | no | the window title text. Defaults to the foreground. |
| `buttons` | no | the three window buttons, left to right. Defaults to the usual red, yellow and green. |
| `bold_is_bright` | no | when `true`, bold text in colors 0 to 7 uses the bright version, like many terminals do |
| `palette` | yes | the 16 ANSI colors |

The palette is the part that matters most. Programs don't ask for exact colors most of the time. They ask for "red" or "bright blue" by number, and the theme decides what that looks like. You can write the palette with names:

```json
"palette": {
  "black": "#2a303b",
  "red": "#ef6b73",
  "green": "#8fd18c",
  "bright-black": "#59627a"
}
```

or as a list of 16 colors in this order: black, red, green, yellow, blue, magenta, cyan, white, then the bright version of each. A list of 8 also works. Any bright color you leave out copies the normal one.

The names are `black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan` and `white`, plus `bright-black` through `bright-white`. Colors 16 to 255 are the standard xterm palette and are the same in every theme.

A few tips that make a theme easier to read:

- Keep the foreground well away from the background. `check` warns below a contrast ratio of 4.5:1, which is the WCAG level for normal text.
- Make `bright-black` visible on the background. Many tools use it for dim text and comments.
- Make the cursor stand out. A cursor the same color as the background disappears.

## 3. Preview it

```bash
termreel themes preview my-theme.json
```

This writes `my-theme.svg`, a small terminal showing all 16 colors, the text styles (bold, dim, italic and so on) and some typical command output. Open it in a browser. Add `--animated` if you want to see the typing too.

The quickest way to tune a theme is to keep the SVG open in a browser tab, edit the JSON, run `preview` again, and refresh.

## 4. Check it

```bash
termreel themes check my-theme.json
```

`check` loads the theme the same way `render` does, so if it passes, the theme works. It reports two kinds of problems:

- **Errors** stop the theme from loading. Examples are invalid JSON, a misspelled key (`"pallete"`), a color like `"red"` instead of `"#ff0000"`, or a palette that is missing a color. The message names the file and the key.
- **Warnings** are things that work but probably aren't what you want. Examples are low contrast, an invisible cursor, or a `name` that doesn't match the file name.

Add `--strict` to treat warnings as errors. termreel's own CI does this for every built-in theme.

## 5. Build on another theme with `extends`

If you only want to change a few colors, you don't have to copy a whole theme. Point `extends` at a built-in theme name or at another theme file, then list only what is different:

```json
{
  "name": "nord-warm",
  "extends": "nord",
  "background": "#2b2a33",
  "palette": { "red": "#e06c75", "yellow": "#f2c879" }
}
```

Palette colors merge, so this keeps the other 14 Nord colors. A path in `extends` is relative to the file it is written in, so `"extends": "base.json"` means the `base.json` next to it. A theme can extend a theme that extends another one, and termreel stops with an error if the chain loops back on itself.

## 6. Use it

There are three ways to pick your theme when rendering.

First, pass the file to `-t`:

```bash
termreel render demo.reel -t my-theme.json
```

Second, name it in the demo script, so anyone who renders the script gets the same look. The path is relative to the script:

```text
@theme my-theme.json
```

Third, install it so it works by name from anywhere. Put the file in your user theme folder:

- Linux and macOS: `~/.config/termreel/themes/` (or `$XDG_CONFIG_HOME/termreel/themes/`)
- Windows: `%APPDATA%\termreel\themes\`

You can also list extra folders in the `TERMREEL_THEME_PATH` environment variable, separated by `:` (or `;` on Windows). After that, `termreel render demo.reel -t my-theme` works, and `termreel themes` lists it with a `[custom]` tag. The built-in themes always win a name clash, so a custom theme called `nord` is never picked by name. Use the path for that one.

If both `-t` and `@theme` are given, `-t` wins.

## 7. Add your theme to termreel

Built-in themes live in [`termreel/themes/builtin/`](../termreel/themes/builtin), one file per theme, in exactly the same format. To suggest one:

1. Add `termreel/themes/builtin/your-theme.json`. Set `"name"` to `your-theme` (lowercase words joined by dashes) and write a one-line `description`. Credit the original palette's author if it is based on someone else's work.
2. Run `termreel themes check --strict termreel/themes/builtin` and `python -m unittest discover -s tests`.
3. Open a pull request.

The [Themes workflow](../.github/workflows/themes.yml) then runs on the pull request. It checks every theme with `--strict`, runs the theme tests and renders the whole gallery. You can download the previews from the run's **theme-previews** artifact to see your theme next to the others. Once the pull request is merged into `main`, the same workflow regenerates [`docs/themes`](themes/README.md) and commits it, so the gallery always matches the theme files. Because of this, there is no need to commit preview SVGs yourself.

To regenerate the gallery locally, run:

```bash
termreel themes gallery docs/themes
```
