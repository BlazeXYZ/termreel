"""Live recording of an interactive shell session into a Recording.

On Linux/macOS this uses the standard-library pty module. On Windows it uses
pywinpty (``pip install pywinpty``) when installed; without it, use scripted
demos (see script.py), which work everywhere.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

from .cast import Recording


def default_shell() -> str:
    if os.name == "nt":
        return shutil.which("pwsh") or shutil.which("powershell") or "cmd.exe"
    return os.environ.get("SHELL", "/bin/sh")


def record(command: str | None = None, title: str = "") -> Recording:
    cols, rows = shutil.get_terminal_size((80, 24))
    command = command or default_shell()
    rec = Recording(cols, rows, title)
    start = time.monotonic()

    def on_output(data: str) -> None:
        rec.events.append((time.monotonic() - start, data))

    if os.name == "nt":
        _record_windows(command, cols, rows, on_output)
    else:
        _record_posix(command, cols, rows, on_output)
    return rec


def _record_posix(command: str, cols: int, rows: int, on_output) -> None:
    import codecs
    import fcntl
    import pty
    import select
    import struct
    import termios
    import tty

    pid, master = pty.fork()
    if pid == 0:
        os.environ["TERMREEL"] = "1"
        os.execvp("/bin/sh", ["/bin/sh", "-c", command])

    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    stdin = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(stdin) if os.isatty(stdin) else None
    try:
        if old_attrs:
            tty.setraw(stdin)
        while True:
            readable, _, _ = select.select([master, stdin], [], [])
            if master in readable:
                try:
                    chunk = os.read(master, 65536)
                except OSError:  # EIO: child closed the pty
                    break
                if not chunk:
                    break
                os.write(sys.stdout.fileno(), chunk)
                text = decoder.decode(chunk)
                if text:
                    on_output(text)
            if stdin in readable:
                data = os.read(stdin, 1024)
                if not data:
                    break
                os.write(master, data)
    finally:
        if old_attrs:
            termios.tcsetattr(stdin, termios.TCSAFLUSH, old_attrs)
        os.close(master)
        os.waitpid(pid, 0)


def _record_windows(command: str, cols: int, rows: int, on_output) -> None:
    try:
        from winpty import PtyProcess  # type: ignore
    except ImportError:
        sys.exit(
            "termreel: live recording on Windows needs pywinpty:\n"
            "    pip install pywinpty\n"
            "or describe the session as a script and use `termreel script`."
        )
    import msvcrt
    import threading

    proc = PtyProcess.spawn(command, dimensions=(rows, cols))
    done = threading.Event()

    def pump_output() -> None:
        while proc.isalive():
            try:
                text = proc.read(65536)
            except EOFError:
                break
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
                on_output(text)
        done.set()

    threading.Thread(target=pump_output, daemon=True).start()
    # Map the special keys msvcrt reports as two-part codes to VT sequences.
    special = {"H": "\x1b[A", "P": "\x1b[B", "M": "\x1b[C", "K": "\x1b[D",
               "G": "\x1b[H", "O": "\x1b[F", "S": "\x1b[3~"}
    while not done.is_set():
        if msvcrt.kbhit():
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                ch = special.get(msvcrt.getwch(), "")
            if ch:
                proc.write(ch)
        else:
            time.sleep(0.01)
