"""The TUI chart palette, measured as the reader sees it - after Textual
has rendered it - not as the names in the source.

WHY THE COLOURS ARE READ BACK FROM A RENDERED SCREEN

PANE_COLORS used to hold seven plotext colour NAMES, which look like
seven distinct colours in the source. They are not seven on screen:
plotext emits a name as an ANSI colour, and Textual repaints ANSI colours
through its theme, which folds several of them together. Measured
2026-09-23: "red" and "magenta" both came out #f4005f, so in the README
demo "eta" and "zeta" were the same colour, and "yellow" drew exactly
like another series too. Any assertion on the source list would have passed over that. So this
test draws each palette entry through the same widget the chart uses,
exports the screen, and compares what came out.

WHY A FLOOR OF 20 (CIE76 DELTA E)

About ten just-noticeable differences (~2.3 each), and it is where the
HTML dashboard's own dark palette puts its closest pair among the first
seven groups (#3987e5 / #9085e9, 20.7). So both dashboards meet the same
floor. The old TUI palette had pairs at 0.
"""

import asyncio
import itertools
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

from textual.app import App, ComposeResult  # noqa: E402
from textual_plotext import PlotextPlot  # noqa: E402

import dashboard_tui as tui  # noqa: E402

MIN_DELTA_E = 20.0


class _Bars(PlotextPlot):
    def __init__(self, color: Any) -> None:
        super().__init__()
        self.color = color

    def on_mount(self) -> None:
        self.plt.clear_figure()
        self.plt.bar([1, 2], [5, 10], color=self.color)


class _Probe(App[None]):
    def __init__(self, color: Any) -> None:
        super().__init__()
        self.color = color

    def compose(self) -> ComposeResult:
        yield _Bars(self.color)


async def _bar_fill(color: Any) -> str:
    """The fill of the bar glyphs themselves - not of the axes, ticks or
    title around them, which also get fills and would muddle a plain
    "every fill in the SVG" reading."""
    app = _Probe(color)
    async with app.run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
    styles = dict(re.findall(r"\.([\w-]+) \{ fill: (#[0-9a-f]{6}) \}", svg))
    bar_classes = set(re.findall(r'<text class="([\w-]+)"[^>]*>\u2588+</text>', svg))
    fills: set[str] = {styles[c] for c in bar_classes}
    assert len(fills) == 1, f"{color!r}: bar glyphs rendered in {sorted(fills)}, not one fill"
    return fills.pop()


def rendered_colors(colors: list[Any]) -> list[str]:
    """What each colour looks like on screen once Textual has drawn it."""

    async def measure() -> list[str]:
        return [await _bar_fill(color) for color in colors]

    return asyncio.run(measure())


def _lab(hex_rgb: str) -> tuple[float, float, float]:
    def linear(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(int(hex_rgb[i : i + 2], 16) / 255) for i in (1, 3, 5))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: str, b: str) -> float:
    return float(sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b), strict=True)) ** 0.5)


def test_delta_e_measures_what_it_claims() -> None:
    # The metric itself, on known values: identical is 0, black/white is
    # the full lightness range, and it is symmetric.
    assert delta_e("#f4005f", "#f4005f") == 0
    assert round(delta_e("#000000", "#ffffff")) == 100
    assert delta_e("#98e024", "#9d65ff") == delta_e("#9d65ff", "#98e024")


def test_every_project_colour_is_distinct_on_screen() -> None:
    palette = [*tui.PANE_COLORS, tui.PANE_OTHER_COLOR]
    shown = rendered_colors(palette)
    too_close = [
        (palette[i], palette[j], round(delta_e(a, b), 1))
        for (i, a), (j, b) in itertools.combinations(enumerate(shown), 2)
        if delta_e(a, b) < MIN_DELTA_E
    ]
    assert not too_close, f"series that render alike (delta E < {MIN_DELTA_E}): {too_close}"
