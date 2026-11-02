"""A fake build tool whose output exercises the trickier parts of the emulator:
in-place progress bars, multi-line redraws, spinners, truecolor and wide glyphs."""

import sys
import time

out = sys.stdout


def w(s):
    out.write(s)
    out.flush()


steps = [("compile", "#61afef", 0.9), ("bundle", "#c678dd", 0.7), ("minify", "#e5c07b", 0.5)]

w("\x1b[1mbuilding\x1b[0m \x1b[2mtermreel-site v0.1.0\x1b[0m\n\n")
w("\n" * len(steps))
for n in range(0, 55):
    w(f"\x1b[{len(steps)}A")  # jump back up and redraw every bar
    for name, color, rate in steps:
        done = min(40, int(n * rate * 1.5))
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        bar = f"\x1b[38;2;{r};{g};{b}m{'█' * done}\x1b[38;5;238m{'░' * (40 - done)}\x1b[0m"
        mark = "\x1b[32m✓\x1b[0m" if done == 40 else "\x1b[33m" + "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[n % 10] + "\x1b[0m"
        w(f"\r {mark} {name:<9}{bar} {done * 100 // 40:>3}%\x1b[K\n")
    time.sleep(0.05)

w("\n\x1b[1m output        size    gzip\x1b[0m\n")
rows = [("index.html", "4.2 KB", "1.6 KB"), ("app.js", "88.0 KB", "27.9 KB"), ("日本語.css", "6.1 KB", "1.9 KB")]
for f, size, gz in rows:
    hl = "\x1b[33m" if f == "app.js" else ""  # flag the heavy bundle
    w(f"{hl} {f:<{14 - sum(1 for c in f if ord(c) > 0x2e80)}}{size:>7} {gz:>7} \x1b[0m\n")

w("\n")
for x in range(56):
    r, g, b = int(255 * x / 55), int(120 + 80 * (1 - x / 55)), 255 - int(200 * x / 55)
    w(f"\x1b[48;2;{r};{g};{b}m ")
w("\x1b[0m\n\x1b[1;32m✨ done\x1b[0m in \x1b[1m2.31s\x1b[0m\n")
