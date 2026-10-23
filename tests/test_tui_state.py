"""Cycling the period must not disturb UI state it did not change.

Regression coverage for two defects found by hand (2026-08-18), both side
effects of the perf fix in test_tui_responsiveness.py: action_cycle_period
now ends in self.refresh(recompose=True), which rebuilds the whole widget
tree and, unless something restores it, drops state the rebuild has no
business dropping.

  Defect 1 - Footer goes blank after "p", Tab brings it back.
  Defect 2 - pressing "p" while on the "Panorama" tab bounced back to
             "Sessões".

Defect 2 did NOT reproduce from the app's default tab (Sessões) - a fresh,
un-recomposed TabbedContent already defaults to its first pane, so that
starting point could not tell "state preserved" from "state defaulted".
It DID reproduce reliably starting from Panorama (toggle with "g", then
press "p"), which is also the sequence the user actually hit. Both starting
points are driven below so a regression in either branch of the fix (the
Sessões case restores Tree focus, the Panorama case restores TrendChart
focus) is caught.

WHY THIS RUNS IN-PROCESS (2026-09-01)

This file used to hand tests/tui_state_driver.py to .venv/bin/python3 and
read a JSON result back over a pipe. That was never a test-isolation
decision - it was a workaround for an environment split: pytest ran on the
system interpreter, textual existed only in the repo's .venv, and neither
could import the other. The cost was that coverage.py could not see into
the child, so dashboard_tui.py - the repo's second-largest module - was
excluded from measurement entirely.

textual is now a declared test dependency (requirements-dev.txt) installed
into whatever interpreter runs pytest, and the app is driven here through
Textual's own testing API. The alternative - COVERAGE_PROCESS_START plus a
coverage.process_startup() .pth in the venv's site-packages - was rejected
because it is environment-level, does not travel with the repository, and
yields ZERO coverage without erroring wherever the .pth is absent. A
measurement that can silently return nothing is worse than a known gap.

There is deliberately no METRICS_SKIP_TUI_TESTS escape hatch any more. If
textual is missing the import below fails at collection, loudly, which is
the only acceptable behaviour for a dependency this suite genuinely has.
"""

import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

# lib/ holds metrics_core and the repo root holds dashboard_tui; neither is
# installed as a package, so both are put on the path the same way the
# production entry points do it. Every import below is therefore
# necessarily below this line - hence the noqa: E402 on each.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
import metrics_core.backfill as backfill_module  # noqa: E402
import metrics_core.labels as labels_module  # noqa: E402
import metrics_core.paths as paths_module  # noqa: E402
from fixtures.build_fixture import build_fixture  # noqa: E402
from textual.css.query import NoMatches  # noqa: E402
from textual.widgets import Footer, TabbedContent, Tree  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def isolate_tui_from_the_real_filesystem(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Point every path dashboard_tui can reach at a throwaway HOME.

    The subprocess this file replaced got isolation for free by passing
    HOME in the child's environment, before the child imported anything.
    In-process that is not enough, and the difference is the single most
    dangerous part of this rewrite: METRICS_FILE, SUBAGENTS_FILE,
    TASK_LABELS_FILE, AGENT_LABELS_FILE and BACKFILL_STATE_FILE are
    module-level constants resolved from HOME at IMPORT time, and
    metrics_core was already imported (by conftest) long before any test
    ran. setenv("HOME") alone would leave all five aimed at the user's
    real ~/.claude/metrics - which other live sessions are appending to
    right now, and which is irreplaceable. They are patched directly.

    setenv("HOME") is still needed, and is the correct mechanism, for the
    paths computed fresh at CALL time - mr.get_open_projects()'s
    ~/.claude/sessions is the one that matters here.
    """
    metrics = home / ".claude" / "metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(mr, "METRICS_FILE", str(metrics / "sessions.jsonl"))
    monkeypatch.setattr(mr, "SUBAGENTS_FILE", str(metrics / "subagents.jsonl"))
    monkeypatch.setattr(labels_module, "TASK_LABELS_FILE", str(metrics / "task_labels.json"))
    monkeypatch.setattr(labels_module, "AGENT_LABELS_FILE", str(metrics / "agent_labels.json"))
    monkeypatch.setattr(
        backfill_module, "BACKFILL_STATE_FILE", str(metrics / "backfill_state.json")
    )
    # Same reasoning, added 2026-09-21 with the declared project roots: the
    # reader's REAL ~/.claude/metrics/config.json would otherwise rename
    # projects underneath every test that builds its own HOME.
    monkeypatch.setattr(paths_module, "CONFIG_FILE", str(metrics / "config.json"))

    # The language is pinned twice, both ways a run can resolve it, so the
    # suite never depends on the locale of the machine running it: config
    # first (the real mechanism), and the locale variables too, for the
    # tests that write their own config.json without a "language".
    (metrics / "config.json").write_text(json.dumps({"language": "pt"}))
    monkeypatch.setenv("LC_ALL", "pt_BR.UTF-8")
    monkeypatch.setenv("LC_MESSAGES", "")
    monkeypatch.setenv("LANG", "")

    # --label-budget 0 must never reach a real `claude -p` call. Failing
    # loudly here - the same mechanism tests/test_dashboard_html_page.py
    # uses - turns an expensive, network-touching false green into a hard
    # failure.
    def fail(*arguments: Any, **keyword_arguments: Any) -> Any:
        raise AssertionError("real subprocess.run must never be called in tests")

    monkeypatch.setattr(subprocess, "run", fail)


def seeded_tui_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> Path:
    """An isolated HOME carrying the same committed eight-record fixture
    the rest of the suite measures."""
    isolate_tui_from_the_real_filesystem(monkeypatch, home)
    build_fixture(str(home), datetime.now().astimezone())
    return home


def _state(app: Any) -> dict[str, Any]:
    """The state report the former driver printed as JSON.

    active                       TabbedContent.active id
    focused_type                 type name of app.focused, or None
    focused_inside_active_pane   is app.focused a descendant of the TabPane
                                 named by `active`
    footer_keys                  FooterKey children the Footer actually
                                 rendered - the direct, visible proof the
                                 footer did not stay blank
    """
    active = app.query_one(TabbedContent).active
    pane = app.query_one(f"#{active}")
    focused = app.focused
    footer = app.query_one(Footer)
    return {
        "active": active,
        "focused_type": type(focused).__name__ if focused is not None else None,
        "focused_inside_active_pane": (focused in pane.walk_children())
        if focused is not None
        else False,
        "footer_keys": len(footer.query("FooterKey")),
    }


async def _drive_both_cases() -> dict[str, Any]:
    """Two cases are driven because the fix has a branch per tab:

    sessions_case   presses "p" from the app's default tab (Sessões) and
                    checks the Tree stays the focused, in-pane widget.
    panorama_case   toggles to Panorama with "g" first, THEN presses "p" -
                    this is the sequence the user actually hit, and the one
                    defect 2 depended on (it did not reproduce starting
                    from Sessões, since that is also where a fresh,
                    un-recomposed TabbedContent already defaults to).
    """
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])

    results: dict[str, Any] = {}
    async with tui.MetricsTUIApp(
        mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
    ).run_test(size=(140, 45)) as pilot:
        app = pilot.app
        await pilot.pause()
        before = _state(app)
        await pilot.press("p")
        await pilot.pause()
        after = _state(app)
        results["sessions_case"] = {"active_before": before["active"], **after}

    async with tui.MetricsTUIApp(
        mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
    ).run_test(size=(140, 45)) as pilot:
        app = pilot.app
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        before = _state(app)
        await pilot.press("p")
        await pilot.pause()
        after = _state(app)
        results["panorama_case"] = {"active_before": before["active"], **after}

    return results


@pytest.fixture(scope="session")
def state_after_period_press(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Drives the app against a throwaway HOME seeded with the committed
    fixture, so this measures the fixture and never the user's real data.

    Session-scoped, and therefore driving its own MonkeyPatch instead of
    the function-scoped `monkeypatch` fixture: both app runs happen once
    and every assertion below reads the same captured report, exactly as
    they did when one subprocess produced one JSON document.
    """
    monkeypatch = pytest.MonkeyPatch()
    try:
        seeded_tui_home(monkeypatch, tmp_path_factory.mktemp("tui-state-home"))
        return asyncio.run(_drive_both_cases())
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize("case", ["sessions_case", "panorama_case"])
def test_active_tab_survives_a_period_cycle(state_after_period_press, case):
    result = state_after_period_press[case]
    assert result["active"] == result["active_before"], (
        f"[{case}] pressing 'p' changed the active tab from "
        f"{result['active_before']!r} to {result['active']!r} - a period "
        "cycle must not touch which tab is open"
    )


@pytest.mark.parametrize("case", ["sessions_case", "panorama_case"])
def test_focus_is_not_dropped_after_a_period_cycle(state_after_period_press, case):
    result = state_after_period_press[case]
    assert result["focused_type"] is not None, (
        f"[{case}] app.focused is None after 'p' - the Footer has nothing "
        "focused to show bindings for, so it renders blank until the user "
        "presses Tab by hand"
    )


@pytest.mark.parametrize("case", ["sessions_case", "panorama_case"])
def test_focused_widget_lives_in_the_active_pane(state_after_period_press, case):
    result = state_after_period_press[case]
    assert result["focused_inside_active_pane"], (
        f"[{case}] focus landed on a {result['focused_type']} that is not a "
        f"descendant of the active pane ({result['active']!r}) - it must be "
        "focus that actually belongs to what the user is looking at"
    )


@pytest.mark.parametrize("case", ["sessions_case", "panorama_case"])
def test_footer_is_not_blank_after_a_period_cycle(state_after_period_press, case):
    result = state_after_period_press[case]
    assert result["footer_keys"] > 0, (
        f"[{case}] the Footer rendered 0 FooterKey children after 'p' - "
        "this is the exact symptom reported by hand: shortcuts vanish "
        "until Tab is pressed"
    )


def test_sessions_case_restores_the_tree(state_after_period_press):
    assert state_after_period_press["sessions_case"]["focused_type"] == "Tree"


def test_panorama_case_restores_the_trend_chart(state_after_period_press):
    assert state_after_period_press["panorama_case"]["focused_type"] == "TrendChart"


def test_tree_holds_focus_immediately_after_mount(monkeypatch, tmp_path):
    """dashboard_tui.py:837, App.on_mount's `self.query_one(Tree).focus()`.

    Every focus assertion above runs AFTER a 'p' keypress; none of them
    covers mount time itself. Textual's own default focus does not land on
    the Tree - the final whole-branch review (2026-09-02) probed the
    mounted app directly and found `app.focused` is `ContentTabs` (the tab
    bar) without this call, `Tree` (the session list) with it. Dropping the
    call is real and user-facing: at startup the arrow keys would drive the
    tab bar instead of the session list."""
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])

    async def _focused_type_at_mount() -> str:
        async with tui.MetricsTUIApp(
            mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
        ).run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            return type(pilot.app.focused).__name__

    focused_type = asyncio.run(_focused_type_at_mount())
    assert focused_type == "Tree", (
        f"app.focused was {focused_type!r} immediately after mount - "
        "on_mount's self.query_one(Tree).focus() must land focus on the "
        "session Tree rather than leaving Textual's own default"
    )


# --- restoring focus before the recomposed Tree exists -------------------
#
# "p" recomposes the app and queues _restore_focus_after_period_cycle with
# call_next. recompose only waits for the widgets it composes directly, not
# for the Tree nested inside TabbedContent > TabPane, so on a loaded machine
# the restore can run before the new Tree is mounted: query_one(Tree)
# raised NoMatches and the exception took the whole app down (seen once in
# 15 runs beside 128 busy loops, 2026-09-23). These tests produce that
# ordering deterministically, by making the Tree absent for the first N
# lookups, instead of by loading the machine.


def _absent_for(app: Any, widget_type: type, lookups: int, calls: list[int]) -> Any:
    real_query_one = app.query_one

    def query_one(selector: Any, *arguments: Any, **keyword_arguments: Any) -> Any:
        if selector is widget_type:
            calls.append(1)
            if len(calls) <= lookups:
                raise NoMatches(f"No nodes match {widget_type.__name__} (test: not mounted yet)")
        return real_query_one(selector, *arguments, **keyword_arguments)

    return query_one


def test_focus_restore_waits_for_a_tree_that_is_not_mounted_yet(monkeypatch, tmp_path):
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])

    async def drive() -> tuple[str, int]:
        app = tui.MetricsTUIApp(
            mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert app.active_tab_id == "tab-sessions"
            app.set_focus(None)
            await pilot.pause()
            calls: list[int] = []
            monkeypatch.setattr(app, "query_one", _absent_for(app, Tree, 2, calls))
            app._restore_focus_after_period_cycle()
            for _ in range(5):
                await pilot.pause()
            return type(app.focused).__name__, len(calls)

    focused, lookups = asyncio.run(drive())
    assert focused == "Tree", f"focus ended on {focused!r} after {lookups} lookups"
    assert lookups == 3, lookups


def test_focus_restore_gives_up_quietly_when_the_tree_never_appears(monkeypatch, tmp_path):
    # Focus is a nicety: a Tree that never shows up must cost a bounded
    # number of retries and no exception, never a crash.
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])

    async def drive() -> int:
        app = tui.MetricsTUIApp(
            mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            calls: list[int] = []
            monkeypatch.setattr(app, "query_one", _absent_for(app, Tree, 10**6, calls))
            app._restore_focus_after_period_cycle()
            for _ in range(2 * tui.FOCUS_RESTORE_ATTEMPTS):
                await pilot.pause()
            return len(calls)

    assert asyncio.run(drive()) == tui.FOCUS_RESTORE_ATTEMPTS


def test_the_app_titles_itself_after_the_product_not_its_class(tmp_path, monkeypatch):
    """Textual falls back to the App subclass's NAME when no TITLE is set,
    so the terminal window announced itself as "MetricsTUIApp" - a class
    name, in the one place a user reads a product name (2026-09-21)."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    assert tui.MetricsTUIApp.TITLE == "tokboard"


# --- the chart keeps its slicing across a period change ----------------
#
# "p" rebuilds every widget (refresh(recompose=True)), so before
# 2026-09-22 the new TrendChart came back in its default "pane" mode and
# the three legend rows all came back visible - whatever the reader had
# picked, and whichever legend belonged to it. Found while re-recording the
# README, where the frame after "p" showed "por projeto" with all three
# legend rows at once.


async def _mode_and_legends_after_p(mode_toggle_presses: int) -> dict[str, Any]:
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    async with tui.MetricsTUIApp(
        mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
    ).run_test(size=(140, 45)) as pilot:
        app = pilot.app
        await pilot.pause()
        await pilot.press("g")
        for _ in range(mode_toggle_presses):
            await pilot.press("t")
        await pilot.pause()
        before = app.query_one(tui.TrendChart).mode
        await pilot.press("p")
        await pilot.pause()
        return {
            "before": before,
            "after": app.query_one(tui.TrendChart).mode,
            "quota": app.query_one(tui.QuotaSummary).display,
            "composition": app.query_one(tui.CompositionLegend).display,
            "projects": app.query_one(tui.ProjectLegend).display,
        }


@pytest.mark.parametrize(
    ("mode_toggle_presses", "mode", "quota", "composition", "projects"),
    [
        (1, "tokens", True, True, False),
        (2, "model", False, False, False),
        # The default mode keeps its mode for free - "pane" is what a fresh
        # chart starts in - so this case isolates the OTHER half of the
        # bug: before the fix it failed on the legends alone, with all
        # three rows visible. Even a reader who never pressed "t" saw it.
        (0, "pane", False, False, True),
    ],
)
def test_the_chart_keeps_its_slicing_and_its_legend_across_a_period_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode_toggle_presses: int,
    mode: str,
    quota: bool,
    composition: bool,
    projects: bool,
) -> None:
    seeded_tui_home(monkeypatch, tmp_path)
    result = asyncio.run(_mode_and_legends_after_p(mode_toggle_presses))
    assert result["before"] == mode, "the setup did not reach the mode under test"
    assert result["after"] == mode
    assert (result["quota"], result["composition"], result["projects"]) == (
        quota,
        composition,
        projects,
    )


def test_the_project_legend_reads_the_same_records_as_the_chart_it_describes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The chart draws per-day deltas (daily_chart_records); the legend used
    # to read sessions by start (chart_records), so a session that began
    # before the window but was active inside it drew a band with no legend
    # entry (2026-10-05 backlog, residual 3). The two sources are told
    # apart by emptying the sessions one: the legend must still name what
    # the chart draws.
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = tui.load_data(arguments, metrics_core_module=mr)
    assert data["daily_chart_records"], "premise: the fixture draws something"
    data["chart_records"] = []

    async def legend_keys() -> list[Any]:
        async with tui.MetricsTUIApp(mr, data, arguments=arguments).run_test(
            size=(140, 45)
        ) as pilot:
            await pilot.pause()
            legend = pilot.app.query_one(tui.ProjectLegend)
            chart = pilot.app.query_one(tui.TrendChart)
            chart.render_chart()
            return [legend._series_keys(), chart.pane_series_keys]

    legend_series, chart_series = asyncio.run(legend_keys())
    assert legend_series
    assert legend_series == chart_series
