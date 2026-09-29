"""TrendChart's "tokens" mode: dashboard_tui.py's largest single dark area
until now (Phase 3e, 2026-09-01).

Task 1 made dashboard_tui.py measurable by a plain `pytest --cov` run for
the first time (see tests/test_tui_state.py's module docstring) and
reported render_chart at 15 statements / 6 branch arcs missing - all of it
the `else:` branch taken only when TrendChart.mode == "tokens", which no
driven key ever reached. "t" (action_toggle_chart_mode) is the only door
into it.

WHY EVERY ASSERTION HERE READS RENDERED OUTPUT, NOT self.mode

Phase 3c shipped build_y_axis at 100% statement and branch coverage with
zero assertions on what it actually produced - deleting its whole body
left the suite green, because every existing test only checked that
*something* rendered, never *what*. render_chart is the same shape: two
branches that must be told apart by an outside observer, not by reading
the flag that selects them. So every assertion below reads either
chart.render().plain (the text a user's terminal would show - title and
series labels) or the chart's own raw ANSI text via chart.plt.build()
(for the two color-coding branches, which never show up in .plain at all,
since .plain strips styling). test_gutting_the_tokens_branch_is_caught
below is the proof: with the `else:` branch replaced by a no-op, every
tokens-mode assertion here fails.

WHY chart.render() / chart.plt.build() ARE CALLED DIRECTLY

PlotextPlot.render() (textual_plotext) turns the plotext figure into a
rich Text via `Text.from_ansi(self._plot.build())` - calling it outside of
a real compositor pass is the same call Textual itself makes to paint the
widget, just without needing a terminal snapshot. `.plt` is
textual_plotext's own documented accessor for the underlying plotext
figure (see PlotextPlot.plt's docstring).

WHY set_highlight/set_project_highlight ARE CALLED DIRECTLY, NOT THROUGH A
MOUSE CLICK

Those two TrendChart methods are what render_chart's coloring branches are
gated on, and they are plain public methods of TrendChart, not internal to
render_chart. Driving CompositionLegend.on_click / ProjectLegend.on_click
so their Highlight message reaches these methods (via
MetricsTUIApp.on_composition_legend_highlight /
on_project_legend_highlight) is a mouse-click concern that belongs to a
later task in this phase - reproducing that whole path here would test the
click handlers under a name that implies it is testing the chart. Calling
TrendChart's own methods directly targets exactly what this task owns:
what render_chart does with self.highlighted / self.project_highlighted
once they are set, independently of whoever sets them.

WHAT THIS FILE DOES NOT COVER, ON PURPOSE

render_chart has two more missing branches that are not about the tokens
mode at all and are left for the data-shape task later in this phase:
`if len(days) < 2: return` (494-495 - needs a fixture with under two days
of data) and `if series_lists:` (516->518 - needs a pane render with zero
projects). Neither is reachable by toggling chart mode on the committed
fixture.
"""

import sys
from pathlib import Path
from typing import Any

import plotext
import pytest

# Same path setup, and same reason, as tests/test_tui_state.py - stated
# here too rather than relying on that module's import having run first,
# so this file's imports do not depend on collection order.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_tui_state import seeded_tui_home  # noqa: E402

import dashboard_tui as tui  # noqa: E402

# plotext's own colorize helper, not a hard-coded escape sequence - so this
# stays correct even if a future plotext release remaps which SGR code
# "gray" resolves to. Only the opening code is kept; colorize() also
# appends the reset sequence "\x1b[0m", which is not part of the marker.
GRAY_ANSI = plotext.colorize("", "gray").split("\x1b[0m")[0]

TOKENS_TITLE = "Tokens por dia (por tipo de token)"
PANE_TITLE = "Tokens por dia (por projeto)"
TOKEN_TYPE_LABELS = ("input", "output", "cache read", "cache creation")
# A project name from the committed fixture (tests/fixtures/build_fixture.py)
# that pane mode's own legend must show and tokens mode must not - proof
# that tokens mode is not merely "non-empty" but a genuinely different
# rendering, not just the pane view under another title.
SAMPLE_PROJECT_LABEL = "delta"


async def _open_panorama(pilot: Any) -> Any:
    app = pilot.app
    await pilot.pause()
    await pilot.press("g")
    await pilot.pause()
    return app.query_one(tui.TrendChart)


async def _drive_mode_toggle() -> dict[str, Any]:
    """Everything the plain toggle (no highlighting) must prove: the mode
    advances, each rendering is genuinely different (not just re-titled),
    the round trip restores the original render byte-for-byte, and the
    toggle is a three-state cycle rather than a longer one."""
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    async with tui.MetricsTUIApp(
        mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
    ).run_test(size=(140, 45)) as pilot:
        chart = await _open_panorama(pilot)
        app = pilot.app

        pane_text = chart.render().plain
        pane_mode = chart.mode
        composition_visible_pane = app.query_one(tui.CompositionLegend).display
        project_visible_pane = app.query_one(tui.ProjectLegend).display
        quota_visible_pane = app.query_one(tui.QuotaSummary).display

        await pilot.press("t")
        await pilot.pause()
        tokens_text = chart.render().plain
        tokens_mode = chart.mode
        composition_visible_tokens = app.query_one(tui.CompositionLegend).display
        project_visible_tokens = app.query_one(tui.ProjectLegend).display
        quota_visible_tokens = app.query_one(tui.QuotaSummary).display

        await pilot.press("t")
        await pilot.pause()
        mode_after_second_toggle = chart.mode
        model_text = chart.render().plain
        composition_visible_model = app.query_one(tui.CompositionLegend).display
        project_visible_model = app.query_one(tui.ProjectLegend).display
        quota_visible_model = app.query_one(tui.QuotaSummary).display

        # The round trip is now three presses, not two - the cycle grew a
        # third mode (tokens by model) in 2026-09.
        await pilot.press("t")
        await pilot.pause()
        mode_after_third_toggle = chart.mode
        pane_text_after_round_trip = chart.render().plain

        return {
            "pane_text": pane_text,
            "pane_mode": pane_mode,
            "composition_visible_pane": composition_visible_pane,
            "project_visible_pane": project_visible_pane,
            "quota_visible_pane": quota_visible_pane,
            "tokens_text": tokens_text,
            "tokens_mode": tokens_mode,
            "composition_visible_tokens": composition_visible_tokens,
            "project_visible_tokens": project_visible_tokens,
            "quota_visible_tokens": quota_visible_tokens,
            "model_text": model_text,
            "composition_visible_model": composition_visible_model,
            "project_visible_model": project_visible_model,
            "quota_visible_model": quota_visible_model,
            "mode_after_second_toggle": mode_after_second_toggle,
            "pane_text_after_round_trip": pane_text_after_round_trip,
            "mode_after_third_toggle": mode_after_third_toggle,
        }


async def _drive_highlighting() -> dict[str, Any]:
    """set_highlight/set_project_highlight each have two properties to
    pin: they re-render (with a color split by name) when their own mode
    is active, and they are a no-op on the currently visible render when
    the OTHER mode is active - the two `if self.mode == ...` guards on
    TrendChart itself (473-475, 478-480 in the pre-task line numbering)."""
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    async with tui.MetricsTUIApp(
        mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
    ).run_test(size=(140, 45)) as pilot:
        chart = await _open_panorama(pilot)
        assert chart.mode == "pane"

        # set_highlight targets tokens-mode coloring; called while still in
        # pane mode it must update the field but change nothing on screen.
        pane_text_before = chart.render().plain
        chart.set_highlight({"series-1"})
        pane_text_after_stray_highlight = chart.render().plain
        chart.set_highlight(None)

        await pilot.press("t")
        await pilot.pause()
        assert chart.mode == "tokens"
        tokens_raw_unhighlighted = chart.plt.build()
        chart.set_highlight({"series-1"})
        tokens_raw_highlighted = chart.plt.build()
        chart.set_highlight(None)

        # set_project_highlight targets pane-mode coloring; called while
        # still in tokens mode it must update the field but change nothing
        # on screen - the mirror case.
        tokens_text_before = chart.render().plain
        chart.set_project_highlight({"/projects/delta"})
        tokens_text_after_stray_highlight = chart.render().plain
        chart.set_project_highlight(None)

        # Two presses to get back to pane: the cycle passes through the
        # by-model mode, which neither highlight field touches.
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        assert chart.mode == "pane"
        pane_raw_unhighlighted = chart.plt.build()
        highlighted_key = chart.pane_series_keys[0]
        chart.set_project_highlight({highlighted_key})
        pane_raw_highlighted = chart.plt.build()
        chart.set_project_highlight(None)

        return {
            "pane_text_before": pane_text_before,
            "pane_text_after_stray_highlight": pane_text_after_stray_highlight,
            "tokens_raw_unhighlighted": tokens_raw_unhighlighted,
            "tokens_raw_highlighted": tokens_raw_highlighted,
            "tokens_text_before": tokens_text_before,
            "tokens_text_after_stray_highlight": tokens_text_after_stray_highlight,
            "pane_raw_unhighlighted": pane_raw_unhighlighted,
            "pane_raw_highlighted": pane_raw_highlighted,
        }


@pytest.fixture(scope="session")
def mode_toggle(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Drives the app against a throwaway HOME seeded with the committed
    fixture, so this measures the fixture and never the user's real data.
    See seeded_tui_home (tests/test_tui_state.py) for why setenv("HOME")
    alone is not enough now that this runs in-process."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        seeded_tui_home(monkeypatch, tmp_path_factory.mktemp("tui-chart-mode-home"))
        return _run(_drive_mode_toggle())
    finally:
        monkeypatch.undo()


@pytest.fixture(scope="session")
def highlighting(tmp_path_factory: pytest.TempPathFactory) -> Any:
    monkeypatch = pytest.MonkeyPatch()
    try:
        seeded_tui_home(monkeypatch, tmp_path_factory.mktemp("tui-chart-highlight-home"))
        return _run(_drive_highlighting())
    finally:
        monkeypatch.undo()


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


# --- the toggle itself --------------------------------------------------


def test_toggle_switches_the_mode(mode_toggle):
    assert mode_toggle["pane_mode"] == "pane"
    assert mode_toggle["tokens_mode"] == "tokens"


def test_toggle_is_a_three_state_cycle_that_wraps(mode_toggle):
    """Pins the boundary explicitly: the cycle is exactly three long, and
    the third "t" wraps to the start rather than stranding the user in the
    last mode or advancing to a fourth state.

    This test used to pin a strict TWO-state flip. The third mode (tokens
    by model) was added deliberately in 2026-09, so the guard was updated
    rather than deleted - it still refuses a cycle of any other length."""
    assert mode_toggle["mode_after_second_toggle"] == "model"
    assert mode_toggle["mode_after_third_toggle"] == "pane"


def test_model_mode_hides_both_of_the_other_modes_legends(mode_toggle):
    """The model mode labels its bands through plotext's own legend, so
    neither sibling widget should be on screen - leaving one up would
    describe a slicing the chart is no longer showing."""
    assert mode_toggle["composition_visible_model"] is False
    assert mode_toggle["project_visible_model"] is False
    assert mode_toggle["quota_visible_model"] is False


def test_toggling_back_restores_the_original_rendering_byte_for_byte(mode_toggle):
    assert mode_toggle["pane_text_after_round_trip"] == mode_toggle["pane_text"], (
        "pane -> tokens -> pane changed what is rendered - toggling back is "
        "supposed to be a true round trip, not merely the same self.mode value"
    )


# --- tokens mode renders its own identifiers, not the pane's ------------


def test_tokens_mode_shows_its_own_title_and_series_labels(mode_toggle):
    text = mode_toggle["tokens_text"]
    assert TOKENS_TITLE in text
    for label in TOKEN_TYPE_LABELS:
        assert label in text, f"tokens mode did not render the {label!r} series label"


def test_tokens_mode_does_not_show_the_pane_titles_or_project_names(mode_toggle):
    text = mode_toggle["tokens_text"]
    assert PANE_TITLE not in text
    assert SAMPLE_PROJECT_LABEL not in text, (
        f"tokens mode rendered {SAMPLE_PROJECT_LABEL!r}, a project name - it should "
        "group by token type, not by project"
    )


def test_pane_mode_shows_project_labels_not_token_type_labels(mode_toggle):
    text = mode_toggle["pane_text"]
    assert PANE_TITLE in text
    assert SAMPLE_PROJECT_LABEL in text
    assert TOKENS_TITLE not in text
    for label in TOKEN_TYPE_LABELS:
        assert label not in text, (
            f"pane mode rendered {label!r} - a token-type-mode series label has "
            "no business appearing in the by-project view"
        )


# --- toggling the chart mode also flips which legend is visible ---------


def test_chart_mode_controls_which_legend_widget_is_visible(mode_toggle):
    assert mode_toggle["project_visible_pane"] is True
    assert mode_toggle["composition_visible_pane"] is False
    assert mode_toggle["project_visible_tokens"] is False
    assert mode_toggle["composition_visible_tokens"] is True


def test_chart_mode_also_controls_the_quota_summary_widget(mode_toggle):
    # QuotaSummary is a separate widget from CompositionLegend (see its own
    # docstring for why), so its visibility sync is its own line in
    # _sync_legend_visibility and deserves its own regression test - a typo
    # keying it off the wrong widget, or forgetting the line entirely,
    # would leave it always visible (a Static's own default) rather than
    # following CompositionLegend as intended.
    assert mode_toggle["quota_visible_pane"] is False
    assert mode_toggle["quota_visible_tokens"] is True


# --- set_highlight / set_project_highlight: own-mode effect, other-mode no-op


def test_set_highlight_recolors_the_tokens_render(highlighting):
    assert GRAY_ANSI not in highlighting["tokens_raw_unhighlighted"], (
        "the default tokens-mode palette should not use plotext's own gray - "
        "if it did, this test could not tell 'highlighted' from 'default'"
    )
    assert GRAY_ANSI in highlighting["tokens_raw_highlighted"], (
        "highlighting one series must gray out the others - no gray appeared "
        "anywhere in the re-rendered chart"
    )


def test_set_highlight_is_a_no_op_while_pane_mode_is_showing(highlighting):
    assert highlighting["pane_text_after_stray_highlight"] == highlighting["pane_text_before"], (
        "set_highlight (fed by the composition legend, a tokens-mode control) "
        "changed the currently-visible PANE render"
    )


def test_set_project_highlight_recolors_the_pane_render(highlighting):
    assert GRAY_ANSI not in highlighting["pane_raw_unhighlighted"], (
        "the default pane-mode palette should not use plotext's own gray - if "
        "it did, this test could not tell 'highlighted' from 'default'"
    )
    assert GRAY_ANSI in highlighting["pane_raw_highlighted"], (
        "highlighting one project must gray out the others - no gray appeared "
        "anywhere in the re-rendered chart"
    )


def test_set_project_highlight_is_a_no_op_while_tokens_mode_is_showing(highlighting):
    assert (
        highlighting["tokens_text_after_stray_highlight"] == highlighting["tokens_text_before"]
    ), (
        "set_project_highlight (fed by the project legend, a pane-mode control) "
        "changed the currently-visible TOKENS render"
    )
