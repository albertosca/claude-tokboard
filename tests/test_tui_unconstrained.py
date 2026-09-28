"""Findings the pre-task ternary/slice mutation sweep surfaced that reaching
100% statement/branch coverage will NOT sweep up (Phase 3e, Task 5).

coverage.py tracks statements and control-flow arcs. It has no concept of a
conditional expression's two OPERANDS, or of a slice bound's specific
number - `x if cond else y` and `seq[:N]` each read as one fully-covered
line the moment they execute once, however wrong the operand or the bound
is. The plan's controller swept all 28 ternaries and all 6 slices in
dashboard_tui.py by hand (see the plan's "Both sweeps were run by the
controller" section) and found several that survive the full 557-test
suite with coverage staying byte-identical - meaning the file can already
report 100% while these are silently broken. Each test below targets one
survivor, and each was proven by re-injecting the exact named mutation into
a throwaway copy of dashboard_tui.py and confirming the test fails (see the
task report for the per-item canary transcript).

Two survivors named by the sweep are deliberately NOT tested here, per the
plan's own judgement call:

- line 347 (`"on #202020" if i % 2 == 1 else ""`, subagent-row zebra
  striping) - a one-row shift in alternating background color. Cosmetic:
  no information is lost, only which rows get the (already decorative)
  stripe.
- line 678 (`"█" if is_open else "▓"`, the bar character for an open vs.
  historical project) - real but low-impact: inverted, an open project's
  bar looks historical and vice versa, on a widget whose primary signal is
  the bar's LENGTH (token total), not its fill character. Low severity
  relative to the four items below.

FINDING 6 - the final whole-branch review's I1 (2026-09-02): four rendered
VALUES with no test constraining them, found by the same mechanism as
Finding 2 above but on the numbers rather than the highlight guards. The
existing tests for these three widgets/functions all assert on labels,
tags, or which rows are present/absent - never on whether the NUMBER next
to a label is the right one:

- `CompositionLegend.render` (~406): `self.totals.get(series, 0)` keyed by
  the wrong dict key (`name` instead of `series`) renders every chip's
  number as 0.
- `ProjectRankingList.render` (~713): `self.mr.format_tokens(total)`
  swapped for `format_tokens(max_total)` makes every row show the largest
  project's total.
- `ProjectRankingList.render` (~710): `bar_len` replaced by a constant
  makes every bar the same length regardless of the row's own total.
- `build_session_tree` (~317): the group header's `total_tokens = sum(...)`
  replaced by a literal makes every group's header lie about its total.

Deliberately NOT tested here, per the final review's own ruling: the
`duration:.0f` -> `.1f` precision survivor (cosmetic - the `is not None`
guard beside it is what actually matters and is already pinned) and the
`bar_len` multiplier `30` -> `29` survivor (cosmetic below the `:<32` pad
width; `* 40` is already caught by the column-alignment test above).
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def _daily(project: str, day: int, tokens: int) -> dict[str, Any]:
    return {
        "session_id": f"{project}-{day}",
        "project_path": project,
        "timestamp_start": f"2026-01-{day:02d}T09:00:00",
        "timestamp_end": f"2026-01-{day:02d}T09:30:00",
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


# ---------------------------------------------------------------------------
# Finding 1 - the stacked bars. This section used to pin
# floor_stacked_bottom_series, a workaround that set a zero bottom series to
# 1 token and so painted a series under days it had no tokens on. It was
# replaced on 2026-09-22 by draw_stacked_bars; one requirement those tests
# carried is kept below - an empty day draws nothing.
# ---------------------------------------------------------------------------


class _RecordingPlt:
    """Stands in for plotext: records what was drawn, in order. The real
    rendering is checked in test_tui_data_shapes.py's zero-row tests; what
    is under test here is which bars reach plotext at all."""

    def __init__(self) -> None:
        self.bars: list[tuple[list[int], list[int], str | None, str | None]] = []
        self.ticks: tuple[list[int], list[str]] | None = None

    def bar(
        self,
        bar_positions: list[int],
        bar_values: list[int],
        label: str | None,
        color: str | None,
        width: float,
    ) -> None:
        self.bars.append((bar_positions, bar_values, label, color))

    def xticks(self, bar_positions: list[int], labels: list[str]) -> None:
        self.ticks = (bar_positions, labels)

    @property
    def layers(self) -> list[tuple[list[int], list[int], str | None, str | None]]:
        """The bars that belong to a series - everything except the
        invisible, unlabelled zero layer that pins the x axis."""
        return [bar for bar in self.bars if bar[2] is not None]


def test_stacked_bars_are_cumulative_layers_drawn_from_the_top_down():
    plt = _RecordingPlt()
    tui.draw_stacked_bars(
        plt, ["d1", "d2"], [[10, 20], [5, 5]], ["low", "high"], ["g", "b"], canvas_width=None
    )
    # First, an unlabelled zero-height layer at every position: it is what
    # keeps a zero-token bucket at either edge on the axis.
    assert plt.bars[0] == ([1, 2], [0, 0], None, None)
    # Then the top layer, at the full stack height, so the lower layer drawn
    # after it paints over its bottom part.
    assert plt.layers == [([1, 2], [15, 25], "high", "b"), ([1, 2], [10, 20], "low", "g")]
    assert plt.ticks == ([1, 2], ["d1", "d2"])


def test_a_series_is_not_drawn_on_a_day_it_has_no_tokens():
    plt = _RecordingPlt()
    tui.draw_stacked_bars(
        plt, ["d1", "d2"], [[0, 20], [5, 5]], ["low", "high"], ["g", "b"], canvas_width=None
    )
    # "low" is 0 on d1: no bar there at all - neither a phantom strip in its
    # colour nor a zero-height bar, which plotext would draw as a blank cell
    # over whatever is under it.
    assert ([1], [5]) not in [
        (bar_positions, bar_values) for bar_positions, bar_values, *_ in plt.layers
    ]
    assert plt.layers[1] == ([2], [20], "low", "g")


def test_a_day_with_no_data_at_all_draws_nothing():
    """Kept from the replaced tests: an empty day must not get a bar."""
    plt = _RecordingPlt()
    tui.draw_stacked_bars(
        plt, ["d1", "d2"], [[0, 7], [0, 3]], ["low", "high"], ["g", "b"], canvas_width=None
    )
    assert all(1 not in bar_positions for bar_positions, *_ in plt.layers)


def test_a_series_with_no_tokens_anywhere_gets_no_legend_entry():
    # Nothing to draw means nothing to label: a legend entry for a series
    # absent from the whole chart would name a colour that appears nowhere.
    plt = _RecordingPlt()
    tui.draw_stacked_bars(
        plt, ["d1", "d2"], [[3, 4], [0, 0]], ["low", "high"], ["g", "b"], canvas_width=None
    )
    assert [label for *_, label, _ in plt.layers] == ["low"]


# ---------------------------------------------------------------------------
# Finding 2 - series highlighting, unconstrained in four places (treated as
# one finding, per the task brief): the STYLE guards (382, 611) and the
# COLOUR guards (525, 553).
# ---------------------------------------------------------------------------


def test_composition_legend_bolds_only_the_highlighted_chip():
    """dashboard_tui.py:382. Existing click tests (test_tui_clicks.py) only
    ever assert on `.highlighted` (the state), never on `.render()` (what
    the user actually sees) - so an inverted guard, which bolds every OTHER
    chip and leaves the clicked one plain, would pass every test in the
    suite before this one."""
    legend = tui.CompositionLegend(
        {"series-1": 10, "series-2": 20, "series-3": 30, "series-4": 40}, mr, lang="pt"
    )
    legend.highlighted = {"series-2"}
    text = legend.render()
    assert "[bold reverse]output" in text, "the highlighted chip must carry the bold-reverse tag"
    for other in ("input", "cache read", "cache creation"):
        assert f"[bold reverse]{other}" not in text, (
            f"{other!r} is not highlighted and must not carry the bold-reverse tag"
        )


def test_project_legend_bolds_only_the_highlighted_chip():
    """dashboard_tui.py:611, the pane-mode twin of the test above."""
    records = [_daily(f"/projects/p{index:02d}", day=1, tokens=100 - index) for index in range(3)]
    legend = tui.ProjectLegend(mr, records, lang="pt")
    keys = legend._series_keys()
    assert all(key is not None for key in keys), "this fixture yields no 'outros' bucket"
    legend.highlighted = {keys[1]}
    text = legend.render()
    assert f"[bold reverse]{str(keys[1]).split('/')[-1]}" in text
    for key in (keys[0], keys[2]):
        label = str(key).split("/")[-1]
        assert f"[bold reverse]{label}" not in text, (
            f"{label!r} is not the highlighted project and must not be bolded"
        )


def _capture_stacked_bar_color(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The per-series colour list, as the chart hands it to plotext. Since
    2026-09-22 that goes through draw_stacked_bars (it used to be
    plt.stacked_bar's `color=`), still one entry per series, in series
    order."""
    captured: dict[str, Any] = {}
    original = tui.draw_stacked_bars

    def spy(
        plt: Any,
        xlabels: Any,
        series: Any,
        labels: Any,
        colors: list[str],
        **keyword_arguments: Any,
    ) -> None:
        captured["color"] = colors
        original(plt, xlabels, series, labels, colors, **keyword_arguments)

    monkeypatch.setattr(tui, "draw_stacked_bars", spy)
    return captured


def test_set_highlight_colors_the_highlighted_series_and_greys_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dashboard_tui.py:553 (`self.SERIES_COLORS[i] if key in
    self.highlighted else "gray"`). tests/test_tui_chart.py's own
    highlighting tests only check THAT gray appears somewhere in the
    render, which is exactly the assertion an operand swap survives (gray
    still appears - just on the wrong series). This reads the actual
    `color` list handed to plotext and checks it by series
    identity."""
    records = [_daily("/projects/a", 1, 10), _daily("/projects/a", 2, 20)]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.toggle_mode()  # into "tokens" mode, where set_highlight applies
    captured = _capture_stacked_bar_color(monkeypatch)
    chart.set_highlight({"series-1"})
    color = captured["color"]
    assert color[0] == chart.SERIES_COLORS[0], (
        "the highlighted series (series-1) must keep its own color, not go gray"
    )
    for index in (1, 2, 3):
        assert color[index] == "gray", (
            f"series index {index} is not highlighted and must be grayed out"
        )


def test_set_project_highlight_colors_the_highlighted_project_and_greys_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dashboard_tui.py:525, the pane-mode twin - and the same class of gap
    as line 520's boundary just below it: computed with more projects than
    PANE_COLORS so the "outros" bucket is exercised at the same time."""
    records = [
        _daily(f"/projects/p{index:02d}", day, 100 - index) for index in range(3) for day in (1, 2)
    ]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.render_chart()
    captured = _capture_stacked_bar_color(monkeypatch)
    highlighted_key = chart.pane_series_keys[1]
    chart.set_project_highlight({highlighted_key})
    color = captured["color"]
    assert color[1] == tui.PANE_COLORS[1], (
        "the highlighted project must keep its own palette color, not go gray"
    )
    assert color[0] == "gray"
    assert color[2] == "gray"


# ---------------------------------------------------------------------------
# Finding 3 - line 520's boundary: `i < len(PANE_COLORS)` vs `i <=
# len(PANE_COLORS)`. Caught by an operand swap (recoloring every entry),
# but the boundary shift only changes the ONE overflow entry, and survives.
# ---------------------------------------------------------------------------


def test_overflow_projects_beyond_pane_colors_get_the_other_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """9 distinct, distinctly-ranked projects: PANE_MAX_PROJECTS (7) go
    into top_keys, the rest collapse into one "outros" bucket, so
    series_keys has exactly 8 entries - one more than len(PANE_COLORS) (7).
    That 8th entry (index 7) is the only one the `i < len(PANE_COLORS)`
    boundary controls."""
    records = [
        _daily(f"/projects/p{index:02d}", day, (9 - index) * 10)
        for index in range(9)
        for day in (1, 2)
    ]
    chart = tui.TrendChart(mr, records, lang="pt")
    captured = _capture_stacked_bar_color(monkeypatch)
    chart.render_chart()
    color = captured["color"]
    assert len(chart.pane_series_keys) == 8
    assert chart.pane_series_keys[-1] == tui.PANE_OTHER_KEY
    assert color[:7] == tui.PANE_COLORS, (
        "the 7 real top projects must use the named palette in order"
    )
    assert color[7] == tui.PANE_OTHER_COLOR, (
        "the overflow ('outros') bucket must use PANE_OTHER_COLOR - a boundary "
        "shift here (i <= len(PANE_COLORS)) would try PANE_COLORS[7], which "
        "does not exist"
    )


# ---------------------------------------------------------------------------
# Finding 4 - line 668's slice: `visible = ordered[:10]` surviving becoming
# `ordered[:9]`. tests/test_tui_unbound.py already has a 12-project test
# for this widget, but it only asserts the two SMALLEST projects are hidden
# - it never asserts all ten VISIBLE ones actually are, so a slice shrunk
# to 9 (silently dropping the 10th-ranked project, p09, from both the
# visible and hidden halves) passes it unnoticed.
# ---------------------------------------------------------------------------


def _records_for_n_projects(project_count: int) -> list[dict[str, Any]]:
    return [
        {
            "project_path": f"/projects/p{index:02d}",
            "input_tokens": (project_count - index) * 100,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        for index in range(project_count)
    ]


def test_project_ranking_list_shows_exactly_ten_projects_when_collapsed():
    records = _records_for_n_projects(12)
    panel = tui.ProjectRankingList(mr, records, {}, {}, set(), lang="pt")
    collapsed = panel.render()
    for index in range(10):
        label = f"p{index:02d}"
        assert label in collapsed, (
            f"{label!r} is one of the 10 highest-ranked projects and must be "
            f"visible while collapsed - got:\n{collapsed}"
        )
    for index in (10, 11):
        assert f"p{index:02d}" not in collapsed


def test_project_ranking_list_renders_first_used_today_for_a_project_first_seen_today():
    # ProjectRankingList threads project_first_seen through to
    # mr.project_days_running (backlog #8) - a project first seen a few
    # seconds ago must render the ".today" text, not "0 dias".
    records = _records_for_n_projects(1)
    first_seen = {"/projects/p00": datetime.now().astimezone().isoformat()}
    panel = tui.ProjectRankingList(mr, records, first_seen, {}, set(), lang="pt")
    assert "1º uso hoje" in panel.render()


# ---------------------------------------------------------------------------
# Finding 5 - session-tree column alignment, unconstrained in both
# directions at four truncation/pad pairs (313, 346, 681 - line 313 is
# actually two pairs, session_id[:12]/:<14 and task_label[:28]/:<30). The
# brief asks to pin the ALIGNMENT INVARIANT (a row's columns land at fixed
# offsets) rather than six magic numbers - one test per row-producing
# function below does that, using deliberately over-length input so a
# widened slice (which a format spec's pad never truncates back down)
# would push every later column out of place.
# ---------------------------------------------------------------------------


def test_session_tree_row_columns_land_at_their_fixed_offsets():
    session_id = "Q" * 20  # over session_id[:12]'s pad (:<14)
    task_label_raw = "Z" * 50  # over task_label[:28]'s pad (:<30)
    record = {
        "project_path": "/projects/x",
        "session_id": session_id,
        "task_label": task_label_raw,
        "timestamp_start": "2026-01-01T09:00:00",
        "duration_seconds": 42,
        "input_tokens": 5,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    tree = tui.build_session_tree([record], {}, mr, lang="pt")
    row = str(tree.root.children[0].children[0].label)
    date_text = mr.format_date(str(record["timestamp_start"]), lang="pt")
    tokens = mr.format_tokens(mr.record_token_total(record))
    assert row[14 : 14 + len(date_text)] == date_text, (
        "the date column did not start at its promised fixed offset (14) - "
        "session_id[:12] must never be wide enough to overflow its own "
        ":<14 pad"
    )
    assert row[28 : 28 + 28] == task_label_raw[:28], (
        "the task_label column did not start at its promised fixed offset "
        "(28) - the date column above must not have overflowed either"
    )
    assert row[58 : 58 + 8] == f"{tokens:>8}", (
        "the tokens column did not start at its promised fixed offset (58) "
        "- task_label[:28] must never be wide enough to overflow its own "
        ":<30 pad"
    )


def test_subagent_detail_row_columns_land_at_their_fixed_offsets():
    panel = tui.SessionDetailPanel(mr, lang="pt")
    subagent = {
        "agent_type": "T" * 30,
        "agent_label": "L" * 30,  # combined, over task[:38]'s pad (:<40)
        "input_tokens": 5,
        "output_tokens": 7,
        "tool_call_count": 3,
        "duration_seconds": 12,
    }
    panel.show_session("sess", [subagent])
    row = str(panel._body.render()).split("\n")[-1]
    input_tokens_text = mr.format_tokens(5)
    assert row[40 : 40 + len(input_tokens_text) + 2] == f"{input_tokens_text}/7", (
        "the input/output token column did not start at its promised fixed "
        "offset (40) - task[:38] must never be wide enough to overflow its "
        "own :<40 pad"
    )


def test_project_ranking_row_columns_land_at_their_fixed_offsets():
    records = [
        {
            "project_path": "/projects/" + "x" * 40,  # over label[:24]'s pad (:<26)
            "input_tokens": 100,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
    ]
    panel = tui.ProjectRankingList(mr, records, {}, {}, set(), lang="pt")
    row = panel.render().split("\n")[2]
    formatted_total = mr.format_tokens(100)
    assert row[26:58].strip("▓ ") == "" or "▓" in row[26:58], (
        "sanity check: the bar column must actually be at offset 26"
    )
    assert row[58 : 58 + len(formatted_total)] == formatted_total, (
        "the token-total column did not start at its promised fixed offset "
        "(58) - label[:24] must never be wide enough to overflow its own "
        ":<26 pad"
    )


# ---------------------------------------------------------------------------
# Finding 6 - the final whole-branch review's I1: labels are asserted
# everywhere above, the numbers beside them never were.
# ---------------------------------------------------------------------------


def test_composition_legend_chips_show_their_own_series_total():
    """dashboard_tui.py:406 (`self.totals.get(series, 0)`). The bold/plain
    tests above only assert on the [bold reverse] tag and the label text -
    keying the lookup by `name` (the human label) instead of `series` (the
    key compute_token_type_totals actually produces, "series-1" etc.)
    would render every chip's number as 0 and pass unnoticed, because
    nothing above reads the number."""
    totals = {"series-1": 111, "series-2": 222, "series-3": 333, "series-4": 444}
    legend = tui.CompositionLegend(totals, mr, lang="pt")
    text = legend.render()
    for series, name in tui.CompositionLegend.SERIES:
        formatted = mr.format_tokens(totals[series])
        assert f"{name} · {formatted}" in text, (
            f"chip for {name!r} did not show its own series total ({formatted!r}) - got:\n{text}"
        )


def test_quota_summary_sums_input_and_cache_creation_keeps_output_separate():
    """QuotaSummary.render() must delegate to compute_quota_totals (already
    covered on its own in tests/test_records.py) rather than reimplement the
    weighting inline - this pins the widget's own output against a totals
    dict where getting the weighting wrong (e.g. including cache_read, or
    summing output into the same figure) produces an assertably different
    number."""
    totals = {"series-1": 100, "series-2": 20, "series-3": 999_999_999, "series-4": 5}
    summary = tui.QuotaSummary(totals, mr, lang="pt")
    text = summary.render()
    assert text == "Quota (aprox.): 105 entrada · 20 saída"


def test_project_ranking_rows_show_their_own_token_total_not_the_max():
    """dashboard_tui.py:~713 (`self.mr.format_tokens(total)`). Two projects
    with different totals - if the row rendered `format_tokens(max_total)`
    instead, both rows would show the SAME (largest) number, and the
    existing "shows exactly ten projects" test above would not notice
    since it only checks which project LABELS are present."""
    records = [_daily("/projects/big", 1, 900), _daily("/projects/small", 1, 100)]
    panel = tui.ProjectRankingList(mr, records, {}, {}, set(), lang="pt")
    lines = panel.render().split("\n")
    small_line = next(line for line in lines if "small" in line)
    big_line = next(line for line in lines if "big" in line and "small" not in line)
    assert mr.format_tokens(100) in small_line, (
        f"the 'small' project's row must show its own total (100) - got:\n{small_line}"
    )
    assert mr.format_tokens(900) not in small_line, (
        "the 'small' project's row must not show the largest project's "
        f"total (900) - got:\n{small_line}"
    )
    assert mr.format_tokens(900) in big_line


def test_project_ranking_bar_length_is_proportional_to_each_rows_own_total():
    """dashboard_tui.py:~710 (`bar_len = int((total / max_total) * 30)`).
    A constant `bar_len` (or one derived from the wrong total) makes every
    bar the same length regardless of the row's own data - unnoticed by
    every test above, none of which counts bar characters."""
    records = [_daily("/projects/big", 1, 900), _daily("/projects/small", 1, 100)]
    panel = tui.ProjectRankingList(mr, records, {}, {}, set(), lang="pt")
    lines = panel.render().split("\n")
    big_line = next(line for line in lines if "big" in line and "small" not in line)
    small_line = next(line for line in lines if "small" in line)
    big_bar = big_line[26:58].count("▓")
    small_bar = small_line[26:58].count("▓")
    assert big_bar == 30, (
        f"the largest project's bar must fill the full 30-char field, got {big_bar}"
    )
    assert small_bar == int((100 / 900) * 30), (
        f"expected a bar proportional to 100/900, got {small_bar} chars"
    )
    assert small_bar < big_bar, "a smaller total must produce a shorter bar than a larger one"


def test_session_tree_group_header_shows_the_real_summed_total():
    """dashboard_tui.py:~317 (`total_tokens = sum(...)`). A constant
    (the review's canary used 12345) would show the same total for every
    project group regardless of its sessions' real token totals - and no
    test above reads the header text for its number, only for the session
    count and the project label."""
    alpha_records = [
        {
            "project_path": "/projects/alpha",
            "session_id": "a1",
            "input_tokens": 50,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
    ]
    beta_records = [
        {
            "project_path": "/projects/beta",
            "session_id": "b1",
            "input_tokens": 500,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
    ]
    tree = tui.build_session_tree(alpha_records + beta_records, {}, mr, lang="pt")
    labels = [str(node.label) for node in tree.root.children]
    alpha_label = next(label for label in labels if "alpha" in label)
    beta_label = next(label for label in labels if "beta" in label)
    assert mr.format_tokens(50) in alpha_label, alpha_label
    assert mr.format_tokens(500) in beta_label, beta_label
    assert alpha_label != beta_label, (
        "each project group's header must show its OWN summed token total, not a shared constant"
    )
