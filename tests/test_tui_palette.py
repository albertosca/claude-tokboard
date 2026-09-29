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

MINIMUM_COLOR_DISTANCE = 20.0


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
    fills: set[str] = {styles[class_name] for class_name in bar_classes}
    assert len(fills) == 1, f"{color!r}: bar glyphs rendered in {sorted(fills)}, not one fill"
    return fills.pop()


def rendered_colors(colors: list[Any]) -> list[str]:
    """What each colour looks like on screen once Textual has drawn it."""

    async def measure() -> list[str]:
        return [await _bar_fill(color) for color in colors]

    return asyncio.run(measure())


def _lab(hex_rgb: str) -> tuple[float, float, float]:
    def linear(channel_value: float) -> float:
        return (
            channel_value / 12.92
            if channel_value <= 0.04045
            else ((channel_value + 0.055) / 1.055) ** 2.4
        )

    red, green, blue = (linear(int(hex_rgb[offset : offset + 2], 16) / 255) for offset in (1, 3, 5))
    # CIE XYZ tristimulus values, D65 white point - traditionally called X, Y, Z
    # in that order; named by position here since a bare X/Y/Z is too short.
    first_component = (0.4124 * red + 0.3576 * green + 0.1805 * blue) / 0.95047
    second_component = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    third_component = (0.0193 * red + 0.1192 * green + 0.9505 * blue) / 1.08883

    def f(value: float) -> float:
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    transformed_first, transformed_second, transformed_third = (
        f(first_component),
        f(second_component),
        f(third_component),
    )
    return (
        116 * transformed_second - 16,
        500 * (transformed_first - transformed_second),
        200 * (transformed_second - transformed_third),
    )


def delta_e(first_color: str, second_color: str) -> float:
    return float(
        sum(
            (first_value - second_value) ** 2
            for first_value, second_value in zip(_lab(first_color), _lab(second_color), strict=True)
        )
        ** 0.5
    )


def test_delta_e_measures_what_it_claims() -> None:
    # The metric itself, on known values: identical is 0, black/white is
    # the full lightness range, and it is symmetric.
    assert delta_e("#f4005f", "#f4005f") == 0
    assert round(delta_e("#000000", "#ffffff")) == 100
    assert delta_e("#98e024", "#9d65ff") == delta_e("#9d65ff", "#98e024")


def test_the_palette_has_room_for_every_model_seen_so_far_and_then_some() -> None:
    # The model chart gives each model its own PANE_COLORS slot. The
    # author's history held 8 models on 2026-09-29; 12 leaves headroom
    # for the next few before the overflow marker comes back.
    assert len(tui.PANE_COLORS) >= 12


def test_every_project_colour_is_distinct_on_screen() -> None:
    palette = [*tui.PANE_COLORS, tui.PANE_OTHER_COLOR]
    shown = rendered_colors(palette)
    too_close = [
        (palette[first_index], palette[second_index], round(delta_e(first_color, second_color), 1))
        for (first_index, first_color), (second_index, second_color) in itertools.combinations(
            enumerate(shown), 2
        )
        if delta_e(first_color, second_color) < MINIMUM_COLOR_DISTANCE
    ]
    assert not too_close, (
        f"series that render alike (delta E < {MINIMUM_COLOR_DISTANCE}): {too_close}"
    )
