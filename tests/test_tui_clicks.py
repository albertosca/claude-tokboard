"""Mouse-click handlers: CompositionLegend.on_click, ProjectLegend.on_click,
SessionDetailPanel.show_session, and tree-node selection (Phase 3e, Task 3).

Task 1 made dashboard_tui.py measurable in-process; Task 2 covered the
chart's tokens mode. This file covers the last block that only exists
because Pilot can drive a mouse: `CompositionLegend.on_click` and
`ProjectLegend.on_click` compute a clicked index from `event.x` and post a
Highlight message; `MetricsTUIApp.on_tree_node_selected` and
`SessionDetailPanel.show_session` respond to selecting a leaf in the
session Tree. None of this was reachable through the old subprocess
arrangement, which could drive keys but not a mouse.

WHY EVERY ASSERTION HERE READS THE SPECIFIC THING THE CLICK CHANGED, NOT
"IT DID NOT RAISE"

Same lesson as tests/test_tui_chart.py's module docstring (Phase 3c shipped
build_y_axis at 100% coverage with zero assertions on what it produced).
Each on_click handler computes an index from a click's x-coordinate and
must apply the highlight to THAT series/project and no other - so every
assertion below checks the *specific* series or project key that was
clicked, and a second, distinct click is always driven alongside the first
so a mutation that ignores x (always picks index 0, say) cannot pass by
accident. Tree selection is pinned the same way: SessionDetailPanel must
show the clicked session's OWN subagents, not some other session's, so two
different sessions - one with subagents, one without - are both selected
and their panel contents compared.

WHY GROUP-NODE ROWS AND SET SIZES ARE LOCATED DYNAMICALLY, NOT HARDCODED

Tree row numbers depend on project grouping/ordering that lives in
metrics_core, and legend widget widths depend on the terminal size Pilot
allocates - both are read back from the live widgets after mount rather
than hardcoded, so this file does not silently drift out of sync with
either.
"""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_tui_state import seeded_tui_home  # noqa: E402
from textual.widgets import Tree  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


def seeded_tui_home_with_subagents(mp: pytest.MonkeyPatch, home: Path) -> Path:
    """The same committed fixture as seeded_tui_home, plus:

    - three subagent records under session "week-1" (the busier of the two
      "beta" project sessions) - the committed fixture's own
      subagents.jsonl is empty (see tests/fixtures/build_fixture.py), so it
      can never exercise SessionDetailPanel.show_session's populated
      branch. None sets agent_transcript_path, so get_agent_label's
      cold_summary_source lookup is falsy and caches _NO_LABEL without
      touching the filesystem or `claude -p` - consistent with
      --label-budget 0 never reaching a real subprocess call anywhere else
      in this suite.
    - one extra SESSION record, "zero-dur", whose own duration_seconds is
      0 - not merely absent from the fixture, but a real recorded shape.
      Phase 3d established (see hooks/metrics-log.py's test for a
      single-timestamped-entry transcript) that a session which logs
      exactly once yields timestamp_end == timestamp_start, i.e. a real
      duration_seconds of 0. `duration_seconds:.0f` is falsy for 0 - the
      unnamed-mutation sweep for this task found that
      `if duration is not None else "-"` relaxed to `if duration else "-"`
      survives every other test here and would render "-" for this exact
      shape where the app should render "0s".

    Three subagents, not two: "sub-a" has a real duration (the ">0"
    branch), "sub-b" has duration_seconds=None (the "-" branch), and
    "sub-c" has duration_seconds=0 (the same 0-vs-None distinction as
    "zero-dur" above, but on show_session's own per-row formatting rather
    than build_session_tree's).
    """
    seeded_tui_home(mp, home)
    subagents = [
        {
            "session_id": "week-1",
            "agent_id": "sub-a",
            "agent_type": "general-purpose",
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_tokens": 1,
            "cache_creation_tokens": 1,
            "tool_call_count": 3,
            "duration_seconds": 12.5,
        },
        {
            "session_id": "week-1",
            "agent_id": "sub-b",
            "agent_type": "code-reviewer",
            "input_tokens": 5,
            "output_tokens": 6,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "tool_call_count": 1,
            "duration_seconds": None,
        },
        {
            "session_id": "week-1",
            "agent_id": "sub-c",
            "agent_type": "zero-duration-agent",
            "input_tokens": 2,
            "output_tokens": 3,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "tool_call_count": 1,
            "duration_seconds": 0,
        },
    ]
    subagents_path = home / ".claude" / "metrics" / "subagents.jsonl"
    with subagents_path.open("w") as f:
        for subagent in subagents:
            f.write(json.dumps(subagent) + "\n")

    zero_duration_session = {
        "session_id": "zero-dur",
        "project_path": "/projects/iota",
        "timestamp_start": "2026-08-30T10:00:00+00:00",
        "timestamp_end": "2026-08-30T10:00:00+00:00",
        "duration_seconds": 0,
        "turn_count": 1,
        "tool_call_count": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_tokens": 1,
        "cache_creation_tokens": 1,
    }
    sessions_path = home / ".claude" / "metrics" / "sessions.jsonl"
    with sessions_path.open("a") as f:
        f.write(json.dumps(zero_duration_session) + "\n")

    return home


async def _open_panorama(pilot: Any) -> Any:
    app = pilot.app
    await pilot.pause()
    await pilot.press("g")
    await pilot.pause()
    return app.query_one(tui.TrendChart)


async def _drive_composition_legend_clicks() -> dict[str, Any]:
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    async with tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args).run_test(
        size=(140, 45)
    ) as pilot:
        chart = await _open_panorama(pilot)
        await pilot.press("t")  # tokens mode: CompositionLegend is the visible legend
        await pilot.pause()
        app = pilot.app
        legend = app.query_one(tui.CompositionLegend)
        assert legend.display, "CompositionLegend must be visible in tokens mode to be clickable"

        series = [key for key, _ in tui.CompositionLegend.SERIES]
        width: int = legend.size.width
        chip_width: int = width // len(series)

        def x_for(index: int) -> int:
            return index * chip_width + 1

        results: dict[str, Any] = {"series": series}

        # Rightmost pixel of the widget. The production clamp
        # (min(x // chip_width, 3)) is already correct here - verified: it
        # holds at every width the review swept. What the unnamed-mutation
        # sweep found is that nothing in this suite CONSTRAINED that clamp:
        # relaxing it to min(..., 4) survived every other test. That is a
        # test gap, not a shipped bug - this test closes it, so a future
        # edit that relaxes the clamp cannot ship silently.
        await pilot.click(legend, offset=(width - 1, 0))
        await pilot.pause()
        results["after_rightmost_click"] = legend.highlighted

        # Plain click on index 1, then a DIFFERENT plain click on index 2 -
        # each must land on its OWN series, proving the index tracks x
        # rather than being fixed.
        await pilot.click(legend, offset=(x_for(1), 0))
        await pilot.pause()
        results["after_click_1"] = legend.highlighted
        results["chart_after_click_1"] = chart.highlighted

        await pilot.click(legend, offset=(x_for(2), 0))
        await pilot.pause()
        results["after_click_2"] = legend.highlighted
        results["chart_after_click_2"] = chart.highlighted

        # Clicking the SAME chip again (plain) toggles it off entirely.
        await pilot.click(legend, offset=(x_for(2), 0))
        await pilot.pause()
        results["after_repeat_click_2"] = legend.highlighted
        results["chart_after_repeat_click_2"] = chart.highlighted

        # Shift-click builds a union: index 0, then shift+index 3.
        await pilot.click(legend, offset=(x_for(0), 0))
        await pilot.pause()
        await pilot.click(legend, offset=(x_for(3), 0), shift=True)
        await pilot.pause()
        results["after_shift_union"] = legend.highlighted
        results["chart_after_shift_union"] = chart.highlighted

        # Ctrl-click removes just the clicked member from that union.
        await pilot.click(legend, offset=(x_for(0), 0), control=True)
        await pilot.pause()
        results["after_ctrl_remove_one"] = legend.highlighted
        results["chart_after_ctrl_remove_one"] = chart.highlighted

        # Ctrl-click the last remaining member empties the set to None,
        # not to an empty set.
        await pilot.click(legend, offset=(x_for(3), 0), control=True)
        await pilot.pause()
        results["after_ctrl_remove_last"] = legend.highlighted
        results["chart_after_ctrl_remove_last"] = chart.highlighted

        return results


async def _drive_project_legend_clicks() -> dict[str, Any]:
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    async with tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args).run_test(
        size=(140, 45)
    ) as pilot:
        chart = await _open_panorama(pilot)  # pane mode is the default: ProjectLegend is visible
        app = pilot.app
        legend = app.query_one(tui.ProjectLegend)
        assert legend.display, "ProjectLegend must be visible in pane mode to be clickable"

        keys = legend._series_keys()
        assert len(keys) >= 4, (
            "this test needs at least 4 distinct project series from the fixture to "
            "prove clicks land on distinct indices - got fewer than expected"
        )
        width: int = legend.size.width
        chip_width: int = width // len(keys)

        def x_for(index: int) -> int:
            return index * chip_width + 1

        results: dict[str, Any] = {"keys": keys}

        # Same rightmost-pixel boundary as CompositionLegend, mirrored here
        # since ProjectLegend has its own independent, and already-correct,
        # clamp expression (min(..., len(series_keys) - 1)). The sweep
        # found the same test gap here - nothing constrained it, so a
        # relaxation to min(..., len(series_keys)) would have shipped
        # silently. This test closes that gap.
        await pilot.click(legend, offset=(width - 1, 0))
        await pilot.pause()
        results["after_rightmost_click"] = legend.highlighted

        await pilot.click(legend, offset=(x_for(1), 0))
        await pilot.pause()
        results["after_click_1"] = legend.highlighted
        results["chart_after_click_1"] = chart.project_highlighted

        await pilot.click(legend, offset=(x_for(2), 0))
        await pilot.pause()
        results["after_click_2"] = legend.highlighted
        results["chart_after_click_2"] = chart.project_highlighted

        await pilot.click(legend, offset=(x_for(2), 0))
        await pilot.pause()
        results["after_repeat_click_2"] = legend.highlighted
        results["chart_after_repeat_click_2"] = chart.project_highlighted

        await pilot.click(legend, offset=(x_for(0), 0))
        await pilot.pause()
        await pilot.click(legend, offset=(x_for(3), 0), shift=True)
        await pilot.pause()
        results["after_shift_union"] = legend.highlighted
        results["chart_after_shift_union"] = chart.project_highlighted

        await pilot.click(legend, offset=(x_for(0), 0), control=True)
        await pilot.pause()
        results["after_ctrl_remove_one"] = legend.highlighted
        results["chart_after_ctrl_remove_one"] = chart.project_highlighted

        await pilot.click(legend, offset=(x_for(3), 0), control=True)
        await pilot.pause()
        results["after_ctrl_remove_last"] = legend.highlighted
        results["chart_after_ctrl_remove_last"] = chart.project_highlighted

        return results


async def _drive_tree_selection() -> dict[str, Any]:
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    async with tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args).run_test(
        size=(140, 45)
    ) as pilot:
        app = pilot.app
        await pilot.pause()
        tree = app.query_one(Tree)
        panel = app.query_one(tui.SessionDetailPanel)

        results: dict[str, Any] = {"panel_before_any_click": str(panel._body.render())}

        beta_node = next(c for c in tree.root.children if "beta" in str(c.label))
        beta_row = next(i for i, line in enumerate(tree._tree_lines) if line.path[-1] is beta_node)

        # Clicking a GROUP row (event.node.data is None) must be a no-op:
        # the guard clause `if not session_data: return` in
        # on_tree_node_selected.
        await pilot.click(tree, offset=(2, beta_row))
        await pilot.pause()
        results["panel_after_group_click"] = str(panel._body.render())

        beta_node.expand()
        await pilot.pause()
        week_row = next(
            i
            for i, line in enumerate(tree._tree_lines)
            if line.path[-1].data == {"session_id": "week-1"}
        )
        yest_row = next(
            i
            for i, line in enumerate(tree._tree_lines)
            if line.path[-1].data == {"session_id": "yest-1"}
        )

        # "week-1" has two subagents seeded above - the populated branch.
        await pilot.click(tree, offset=(4, week_row))
        await pilot.pause()
        results["panel_after_week_1_click"] = str(panel._body.render())

        # "yest-1" has none - the empty branch. Clicking a SECOND, DIFFERENT
        # leaf must replace the panel's content with THIS session's own
        # data, not leave week-1's behind.
        await pilot.click(tree, offset=(4, yest_row))
        await pilot.pause()
        results["panel_after_yest_1_click"] = str(panel._body.render())

        # "zero-dur" pins build_session_tree's own duration_str, a
        # SEPARATE `duration is not None` expression (line 300) from
        # show_session's per-subagent one exercised above via "sub-c" -
        # its recorded duration_seconds is 0, a real shape (see
        # seeded_tui_home_with_subagents's docstring), and must render
        # "0s", not "-".
        zero_dur_node = next(c for c in tree.root.children if "iota" in str(c.label))
        results["zero_dur_group_label"] = str(zero_dur_node.label)
        zero_dur_node.expand()
        await pilot.pause()
        zero_dur_leaf = next(
            line.path[-1]
            for line in tree._tree_lines
            if line.path[-1].data == {"session_id": "zero-dur"}
        )
        results["zero_dur_leaf_label"] = str(zero_dur_leaf.label)

        return results


@pytest.fixture(scope="session")
def composition_legend_clicks(tmp_path_factory: pytest.TempPathFactory) -> Any:
    mp = pytest.MonkeyPatch()
    try:
        seeded_tui_home(mp, tmp_path_factory.mktemp("tui-composition-clicks-home"))
        return _run(_drive_composition_legend_clicks())
    finally:
        mp.undo()


@pytest.fixture(scope="session")
def project_legend_clicks(tmp_path_factory: pytest.TempPathFactory) -> Any:
    mp = pytest.MonkeyPatch()
    try:
        seeded_tui_home(mp, tmp_path_factory.mktemp("tui-project-clicks-home"))
        return _run(_drive_project_legend_clicks())
    finally:
        mp.undo()


@pytest.fixture(scope="session")
def tree_selection(tmp_path_factory: pytest.TempPathFactory) -> Any:
    mp = pytest.MonkeyPatch()
    try:
        seeded_tui_home_with_subagents(mp, tmp_path_factory.mktemp("tui-tree-selection-home"))
        return _run(_drive_tree_selection())
    finally:
        mp.undo()


# --- CompositionLegend.on_click ------------------------------------------


def test_composition_legend_click_highlights_only_the_clicked_series(composition_legend_clicks):
    r = composition_legend_clicks
    assert r["after_click_1"] == {"series-2"}
    assert r["chart_after_click_1"] == {"series-2"}, (
        "the click must reach TrendChart via on_composition_legend_highlight, not "
        "just update the legend's own reactive"
    )


def test_composition_legend_rightmost_pixel_clamps_to_the_last_series(composition_legend_clicks):
    """The widget's own right edge must resolve to a valid index (the last
    series). The production clamp already does this correctly; this test
    pins it so a future relaxation cannot ship silently (see the driving
    coroutine's comment above)."""
    r = composition_legend_clicks
    assert r["after_rightmost_click"] == {r["series"][-1]}


def test_composition_legend_second_click_replaces_the_first_not_unions_it(
    composition_legend_clicks,
):
    """A plain click on a DIFFERENT chip while one is already highlighted
    must select only the new one - proves the index is computed fresh from
    THIS click's x, not accumulated from the previous one."""
    r = composition_legend_clicks
    assert r["after_click_2"] == {"series-3"}
    assert r["chart_after_click_2"] == {"series-3"}


def test_composition_legend_click_on_the_already_highlighted_chip_toggles_it_off(
    composition_legend_clicks,
):
    r = composition_legend_clicks
    assert r["after_repeat_click_2"] is None
    assert r["chart_after_repeat_click_2"] is None


def test_composition_legend_shift_click_unions_instead_of_replacing(composition_legend_clicks):
    r = composition_legend_clicks
    assert r["after_shift_union"] == {"series-1", "series-4"}
    assert r["chart_after_shift_union"] == {"series-1", "series-4"}


def test_composition_legend_ctrl_click_removes_only_the_clicked_member(composition_legend_clicks):
    r = composition_legend_clicks
    assert r["after_ctrl_remove_one"] == {"series-4"}
    assert r["chart_after_ctrl_remove_one"] == {"series-4"}


def test_composition_legend_ctrl_click_emptying_the_set_yields_none_not_empty_set(
    composition_legend_clicks,
):
    r = composition_legend_clicks
    assert r["after_ctrl_remove_last"] is None
    assert r["chart_after_ctrl_remove_last"] is None


# --- ProjectLegend.on_click ------------------------------------------------


def test_project_legend_click_highlights_only_the_clicked_project(project_legend_clicks):
    r = project_legend_clicks
    expected = r["keys"][1]
    assert r["after_click_1"] == {expected}
    assert r["chart_after_click_1"] == {expected}


def test_project_legend_rightmost_pixel_clamps_to_the_last_project(project_legend_clicks):
    r = project_legend_clicks
    assert r["after_rightmost_click"] == {r["keys"][-1]}


def test_project_legend_second_click_replaces_the_first_not_unions_it(project_legend_clicks):
    r = project_legend_clicks
    expected = r["keys"][2]
    assert r["after_click_2"] == {expected}
    assert r["chart_after_click_2"] == {expected}
    assert r["keys"][1] not in r["after_click_2"], (
        "the previously-highlighted project must not linger in the new set"
    )


def test_project_legend_click_on_the_already_highlighted_chip_toggles_it_off(
    project_legend_clicks,
):
    r = project_legend_clicks
    assert r["after_repeat_click_2"] is None
    assert r["chart_after_repeat_click_2"] is None


def test_project_legend_shift_click_unions_instead_of_replacing(project_legend_clicks):
    r = project_legend_clicks
    expected = {r["keys"][0], r["keys"][3]}
    assert r["after_shift_union"] == expected
    assert r["chart_after_shift_union"] == expected


def test_project_legend_ctrl_click_removes_only_the_clicked_member(project_legend_clicks):
    r = project_legend_clicks
    expected = {r["keys"][3]}
    assert r["after_ctrl_remove_one"] == expected
    assert r["chart_after_ctrl_remove_one"] == expected


def test_project_legend_ctrl_click_emptying_the_set_yields_none_not_empty_set(
    project_legend_clicks,
):
    r = project_legend_clicks
    assert r["after_ctrl_remove_last"] is None
    assert r["chart_after_ctrl_remove_last"] is None


# --- tree-node selection / SessionDetailPanel.show_session -----------------


def test_clicking_a_group_node_does_not_populate_the_detail_panel(tree_selection):
    r = tree_selection
    assert r["panel_after_group_click"] == r["panel_before_any_click"] == "", (
        "a group node's event.node.data is None - on_tree_node_selected's guard "
        "clause must make this a no-op, not clear or fabricate panel content"
    )


def test_selecting_a_session_with_subagents_shows_its_own_subagent_table(tree_selection):
    text = tree_selection["panel_after_week_1_click"]
    assert "week-1" in text
    assert "Subagentes despachados (3)" in text
    assert "general-purpose" in text
    assert "code-reviewer" in text
    assert "zero-duration-agent" in text
    assert "10/20" in text, "input/output tokens for the first subagent must appear"
    assert "5/6" in text, "input/output tokens for the second subagent must appear"
    assert "2/3" in text, "input/output tokens for the third subagent must appear"
    assert "12s" in text, "a real duration must be formatted with a trailing 's'"
    assert (
        text.index("general-purpose")
        < text.index("code-reviewer")
        < text.index("zero-duration-agent")
    ), "subagents must render in the order they were dispatched"


def test_show_session_renders_a_real_zero_duration_as_0s_not_a_dash(tree_selection):
    """show_session's own `duration is not None else "-"` (dashboard_tui.py
    around line 345) must tell a genuinely-recorded duration_seconds=0
    apart from an ABSENT duration_seconds - the unnamed-mutation sweep
    found relaxing this to a truthiness check survives every other test
    here, and Phase 3d established a single-timestamped-entry transcript
    produces exactly this shape (see
    seeded_tui_home_with_subagents's docstring)."""
    text = tree_selection["panel_after_week_1_click"]
    rows = text.splitlines()
    zero_duration_row = next(row for row in rows if "zero-duration-agent" in row)
    none_duration_row = next(row for row in rows if "code-reviewer" in row)
    assert zero_duration_row.rstrip().endswith("0s"), (
        f"a real duration_seconds=0 must render as '0s', not '-': {zero_duration_row!r}"
    )
    assert none_duration_row.rstrip().endswith("-"), (
        f"a MISSING duration_seconds must still render as '-': {none_duration_row!r}"
    )


def test_selecting_a_session_without_subagents_shows_the_empty_message(tree_selection):
    text = tree_selection["panel_after_yest_1_click"]
    assert "yest-1" in text
    assert "Nenhum subagente nesta sessão." in text
    assert "Subagentes despachados" not in text


def test_selecting_a_different_session_replaces_the_panel_not_appends(tree_selection):
    """The two assertions above already pin each session's own content;
    this pins that selecting the SECOND session actually replaced the
    FIRST session's content rather than leaving it visible alongside."""
    before = tree_selection["panel_after_week_1_click"]
    after = tree_selection["panel_after_yest_1_click"]
    assert "week-1" not in after
    assert "Subagentes despachados" not in after
    assert before != after


def test_session_tree_row_renders_a_real_zero_duration_as_0s_not_a_dash(tree_selection):
    """build_session_tree's own duration_str expression (dashboard_tui.py
    around line 300) is a SEPARATE piece of code from show_session's, with
    its own copy of the same `duration is not None else "-"` guard - pinned
    independently rather than assumed identical."""
    label = tree_selection["zero_dur_leaf_label"]
    assert label.rstrip().endswith("0s"), (
        f"session 'zero-dur' has a real duration_seconds=0 and must render "
        f"'0s' in its own tree row, not '-': {label!r}"
    )
