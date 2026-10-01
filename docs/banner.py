#!/usr/bin/env python3
"""Draw rec2notes/banner.txt as the README's banner: docs/banner-dark.svg and docs/banner-light.svg.

Each █ becomes a block in its row's sunset color and each ░ a faded one, as in the
terminal. The dark variant uses the terminal's colors; the light one a deeper sunset,
since pale yellow disappears on white. Rerun after editing the banner.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rec2notes import ui  # noqa: E402

CELL_W, CELL_H = 8, 16  # a terminal cell is about twice as tall as it is wide
VARIANTS = {
    "dark": (ui.SUNSET, (0x0d, 0x11, 0x17)),  # GitHub's dark background
    "light": ([(0xe0, 0xa1, 0x00), (0xe8, 0x69, 0x2e), (0xe0, 0x45, 0x7b), (0xb0, 0x4c, 0xc0)], (0xff, 0xff, 0xff)),
}
SHADOW_FADE = 0.6  # how far ░ fades toward the background


def svg(lines: list[str], stops, background) -> str:
    width, height = max(map(len, lines)) * CELL_W, len(lines) * CELL_H
    rects = []
    for row, line in enumerate(lines):
        color = ui.sunset(row / max(len(lines) - 1, 1), stops)
        shadow = tuple(round(c + (b - c) * SHADOW_FADE) for c, b in zip(color, background))
        for run in re.finditer(r"░+|[^\s░]+", line):
            fill = "#{:02x}{:02x}{:02x}".format(*(shadow if run.group()[0] == "░" else color))
            rects.append(f'<rect x="{run.start() * CELL_W}" y="{row * CELL_H}" '
                         f'width="{len(run.group()) * CELL_W}" height="{CELL_H}" fill="{fill}"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}" shape-rendering="crispEdges" role="img" aria-label="rec2notes">\n'
            + "\n".join(rects) + "\n</svg>\n")


def main() -> None:
    lines = ui.load_banner()
    for name, (stops, background) in VARIANTS.items():
        out = Path(__file__).with_name(f"banner-{name}.svg")
        out.write_text(svg(lines, stops, background), encoding="utf-8")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
