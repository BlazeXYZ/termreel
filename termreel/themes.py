"""Color themes. Each defines the 16 ANSI colors plus window colors."""

from __future__ import annotations

THEMES = {
    "dracula": {
        "bg": "#282a36", "fg": "#f8f8f2", "cursor": "#f8f8f2", "chrome": "#21222c",
        "ansi": ["#21222c", "#ff5555", "#50fa7b", "#f1fa8c", "#bd93f9", "#ff79c6", "#8be9fd", "#f8f8f2",
                 "#6272a4", "#ff6e6e", "#69ff94", "#ffffa5", "#d6acff", "#ff92df", "#a4ffff", "#ffffff"],
    },
    "one-dark": {
        "bg": "#282c34", "fg": "#abb2bf", "cursor": "#528bff", "chrome": "#21252b",
        "ansi": ["#3f4451", "#e06c75", "#98c379", "#d19a66", "#61afef", "#c678dd", "#56b6c2", "#d7dae0",
                 "#4f5666", "#be5046", "#a5e075", "#e5c07b", "#4dc4ff", "#de73ff", "#4cd1e0", "#e6e6e6"],
    },
    "nord": {
        "bg": "#2e3440", "fg": "#d8dee9", "cursor": "#d8dee9", "chrome": "#272c36",
        "ansi": ["#3b4252", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#88c0d0", "#e5e9f0",
                 "#4c566a", "#bf616a", "#a3be8c", "#ebcb8b", "#81a1c1", "#b48ead", "#8fbcbb", "#eceff4"],
    },
    "monokai": {
        "bg": "#272822", "fg": "#f8f8f2", "cursor": "#f8f8f0", "chrome": "#1e1f1c",
        "ansi": ["#272822", "#f92672", "#a6e22e", "#f4bf75", "#66d9ef", "#ae81ff", "#a1efe4", "#f8f8f2",
                 "#75715e", "#f92672", "#a6e22e", "#f4bf75", "#66d9ef", "#ae81ff", "#a1efe4", "#f9f8f5"],
    },
    "solarized-dark": {
        "bg": "#002b36", "fg": "#839496", "cursor": "#93a1a1", "chrome": "#00212b",
        "ansi": ["#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
                 "#586e75", "#cb4b16", "#93a1a1", "#839496", "#657b83", "#6c71c4", "#93a1a1", "#fdf6e3"],
    },
    "github-light": {
        "bg": "#ffffff", "fg": "#24292f", "cursor": "#044289", "chrome": "#f6f8fa",
        "ansi": ["#24292f", "#cf222e", "#116329", "#4d2d00", "#0969da", "#8250df", "#1b7c83", "#6e7781",
                 "#57606a", "#a40e26", "#1a7f37", "#633c01", "#218bff", "#a475f9", "#3192aa", "#8c959f"],
    },
}


def palette_color(theme: dict, index: int) -> str:
    """Resolve an xterm 256-color index to a hex color under a theme."""
    if index < 16:
        return theme["ansi"][index]
    if index < 232:
        i = index - 16
        steps = [0, 95, 135, 175, 215, 255]
        r, g, b = steps[i // 36], steps[(i // 6) % 6], steps[i % 6]
        return f"#{r:02x}{g:02x}{b:02x}"
    level = 8 + (index - 232) * 10
    return f"#{level:02x}{level:02x}{level:02x}"
