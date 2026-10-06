"""Terminal output: a banner, a checklist with one line per step, then an aligned summary.

On a terminal wide enough for it, the run opens with the banner in banner.txt. The running step's line is redrawn in place with a spinner (and a bar
while transcribing), and output is colored unless NO_COLOR is set. Anywhere else (a pipe,
a file) the same lines are printed plainly, with progress as a line every 10%.
"""

import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import i18n
from .i18n import t

LABEL_WIDTH = 11
DETAIL_WIDTH = 31
BAR_WIDTH = 20
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
MARKS = {"ok": ("✓", "32"), "warn": ("⚠", "33"), "fail": ("✗", "31")}
GREEN, DIM, BOLD, CYAN, YELLOW, RED = "32", "2", "1", "36", "33", "31"
BANNER = Path(__file__).with_name("banner.txt")
SUNSET = [(0xff, 0xd7, 0x5f), (0xff, 0x87, 0x5f), (0xff, 0x5f, 0x87), (0xd7, 0x5f, 0xd7)]  # banner rows, top to bottom
SHADOW = 0.45  # the banner's ░ shadow: its row's color, darker


def label_width() -> int:
    """The checklist's label column: LABEL_WIDTH, or wider when a step label of the current language needs it."""
    return max(LABEL_WIDTH, *map(len, i18n.labels("step.")))


def duration(seconds: float) -> str:
    minutes, seconds = divmod(round(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def rough(seconds: float) -> str:
    """A duration for estimates, in whole minutes."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "<1m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def load_banner() -> list[str]:
    try:
        lines = [line.rstrip() for line in BANNER.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return []
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines


def sunset(t: float, stops: list[tuple[int, int, int]] = SUNSET) -> tuple[int, int, int]:
    """The gradient through `stops` at t, from 0 (top) to 1 (bottom)."""
    t = min(max(t, 0.0), 1.0) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    return tuple(round(a + (b - a) * (t - i)) for a, b in zip(stops[i], stops[i + 1]))


class Console:
    def __init__(self, out=None, err=None):
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.live = self.out.isatty() and os.environ.get("TERM") != "dumb"
        self.color = self.live and not os.environ.get("NO_COLOR")
        self.truecolor = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
        self._lock = threading.Lock()
        self._live_line = False

    def style(self, text: str, *codes: str) -> str:
        return f"\x1b[{';'.join(codes)}m{text}\x1b[0m" if self.color and codes and text else text

    def line(self, text: str = "", err: bool = False) -> None:
        """Print a permanent line, replacing the live line if one is shown."""
        with self._lock:
            if self._live_line:
                self.out.write("\r\x1b[K")
                self.out.flush()
                self._live_line = False
            print(text, file=self.err if err else self.out, flush=True)

    def show(self, segments: list[tuple[str, tuple[str, ...]]]) -> None:
        """Redraw the live line from (text, style) segments, cut to the terminal's width
        (a wrapped line couldn't be redrawn in place). Terminal only."""
        if not self.live:
            return
        width = shutil.get_terminal_size((80, 24)).columns - 1
        with self._lock:
            self.out.write("\r" + self._fit(segments, width) + "\x1b[K")
            self.out.flush()
            self._live_line = True

    def rgb(self, color: tuple[int, int, int]) -> str:
        """A foreground color code: exact on truecolor terminals, else the nearest of 256."""
        if self.truecolor:
            return "38;2;{};{};{}".format(*color)
        r, g, b = (0 if v < 48 else 1 if v < 115 else (v - 35) // 40 for v in color)
        return f"38;5;{16 + 36 * r + 6 * g + b}"

    def banner(self) -> bool:
        """Print the banner, in the sunset gradient, if this is a terminal wide enough for it."""
        lines = load_banner()
        if not (self.live and lines and max(map(len, lines)) < shutil.get_terminal_size((80, 24)).columns):
            return False
        for row, line in enumerate(lines):
            base = sunset(row / max(len(lines) - 1, 1))
            shadow = tuple(round(v * SHADOW) for v in base)
            self.line(re.sub(r"░+|[^\s░]+", lambda m: self.style(
                m.group(), self.rgb(shadow if m.group()[0] == "░" else base)), line))
        return True

    def header(self, course: str, note_name: str) -> None:
        if self.banner():
            self.line()
            self.line(self.style(course, BOLD))
        else:
            self.line(f"{self.style('rec2notes', BOLD)} · {course}")
        self.line(self.style(note_name, DIM))
        self.line()

    def step(self, label: str, detail: str) -> "Step":
        return Step(self, label, detail)

    def mark(self, mark: str, label: str, detail: str, right: str = "", width: int | None = None) -> None:
        """A finished checklist line: `✓ Label       detail   right`; `width` widens the label column."""
        symbol, code = MARKS[mark]
        text = f"{self.style(symbol, code)} {label:<{width or label_width()}} "
        text += f"{detail:<{DETAIL_WIDTH}} {self.style(right, DIM)}" if right else detail
        self.line(text)

    def summary(self, rows: list[tuple[str, list[str], tuple[str, ...]]]) -> None:
        """Aligned `label  value` rows; a label with several values continues on indented lines."""
        width = max(14, *(len(label) for label, _, _ in rows))
        for label, values, codes in rows:
            for i, value in enumerate(values):
                shown = label if i == 0 else ""
                self.line(f"  {self.style(shown.ljust(width), *codes)} {value}")

    def _fit(self, segments, width: int) -> str:
        parts, used = [], 0
        for text, codes in segments:
            if used + len(text) > width:
                parts.append(self.style(text[:max(width - used - 1, 0)] + "…", *codes))
                break
            parts.append(self.style(text, *codes))
            used += len(text)
        return "".join(parts)


class Step:
    """A checklist line for a step that takes a while. On a terminal it shows a spinner and
    the elapsed time, or a bar once `progress` is called; `end` turns it into ✓, ⚠ or ✗.
    Leaving the `with` block on an exception ends it as ✗."""

    def __init__(self, console: Console, label: str, detail: str):
        self.console, self.label, self.detail = console, label, detail
        self.start = time.monotonic()
        self.percent: int | None = None
        self._progress_start = 0.0
        self._next_line = 10
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ended = False

    def __enter__(self) -> "Step":
        if self.console.live:
            self._thread = threading.Thread(target=self._animate, daemon=True)
            self._thread.start()
        else:
            self.console.line(f"  {self.label:<{label_width()}} {self.detail} …")
        return self

    def __exit__(self, kind, error, traceback) -> bool:
        if not self._ended:
            self.end("fail" if kind else "ok", self.detail, duration(self.elapsed))
        return False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start

    def progress(self, percent: int) -> None:
        if self.percent is None:
            self._progress_start = time.monotonic()
        self.percent = percent
        if not self.console.live and self._next_line <= percent < 100:
            self.console.line(f"  {self.label:<{label_width()}} {self._progress_text()}")
            self._next_line = percent // 10 * 10 + 10

    def note(self, mark: str, detail: str) -> None:
        """A permanent line about this step while it keeps running."""
        self.console.mark(mark, self.label, detail)

    def end(self, mark: str, detail: str, right: str = "") -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        self._ended = True
        self.console.mark(mark, self.label, detail, right)

    def _progress_text(self) -> str:
        text = f"{self.percent}%"
        if 0 < self.percent < 100:
            left = (time.monotonic() - self._progress_start) * (100 - self.percent) / self.percent
            text += " · " + t("ui.left", left=rough(left), at=f"{datetime.now() + timedelta(seconds=left):%H:%M}")
        return text

    def _live_segments(self, spinner: str) -> list[tuple[str, tuple[str, ...]]]:
        segments = [(spinner, (CYAN,)), (f" {self.label:<{label_width()}} ", ())]
        if self.percent is None:
            return segments + [(f"{self.detail:<{DETAIL_WIDTH}} ", ()), (duration(self.elapsed), (DIM,))]
        filled = round(self.percent * BAR_WIDTH / 100)
        head = "╸" if filled < BAR_WIDTH else ""
        return segments + [("━" * filled + head, (GREEN,)), ("─" * (BAR_WIDTH - filled - len(head)), (DIM,)),
                           (f"  {self._progress_text()}", ())]

    def _animate(self) -> None:
        frame = 0
        while True:
            self.console.show(self._live_segments(SPINNER[frame % len(SPINNER)]))
            frame += 1
            if self._stop.wait(0.1):
                return
