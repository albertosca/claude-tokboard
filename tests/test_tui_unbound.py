"""`main()`, the lazy-import fallback, and the actions no driven key ever
reached (Phase 3e, Task 4).

Tasks 1-3 covered the state-restoration fix, the chart's tokens mode, and
the mouse-click handlers. This file covers what is left in the "unbound
actions" bucket plus the two areas no test file has touched yet because
nothing in the suite ever ran the module as a script:

  main()                          only ever called by running the file
                                   directly - every other test constructs
                                   MetricsTUIApp itself and passes mr=
                                   explicitly.
  import_metrics_report()         the `mr or import_metrics_report()`
                                   fallback exists for exactly the same
                                   reason - every test so far always
                                   supplies mr=mr.
  action_toggle_more_projects     bound to "m", never pressed.
  the reverse tab switch          action_toggle_tab's `else:` branch -
                                   every existing test presses "g" once
                                   (Sessões -> Panorama) and never a
                                   second time.
  ProjectLegend "Sem dados" and   both gated on an empty chart_records
  its on_click no-op              list, which the committed 7-project
                                   fixture never produces.
  ProjectRankingList's "hidden"   gated on more than PANE_MAX_PROJECTS-
  branch (684-686)                sized... no, more than 10 projects
                                   with tokens - the committed fixture has
                                   7.
  build_session_tree's            a project group whose every session is
  all-zero-token skip (287)       zero-token, so it must not appear as an
                                   empty, expandable node.
  SessionDetailPanel.clear()      never called by ANY code path in
                                   dashboard_tui.py - see the note on the
                                   test below.

WHY main() IS TESTED BY LOADING THE MODULE FRESH WITH __name__="__main__"

Same technique Phase 3d used four times for hooks/metrics-log.py and
hooks/subagent-metrics-log.py (see tests/test_metrics_log_hook.py), and
the only one that exercises `if __name__ == "__main__": main()` honestly:
importing dashboard_tui normally (as every other test file does) leaves
that guard false forever. A second, independent copy of the module is
loaded via importlib with `__name__` pre-set to "__main__" - this does
not edit dashboard_tui.py, it loads the same file under a different name.
`textual.app.App.run` is monkeypatched to capture `self` instead of
launching a real terminal app (which would block the test, or fail
outright with no real terminal attached); everything else - sys.argv,
HOME, the metrics_core module attributes - is exactly what
tests/test_tui_state.py's isolate_tui_from_the_real_filesystem already
sets up, so the fresh module's own `import metrics_core` call (inside
import_metrics_report) resolves to the SAME, already-patched module
object metrics_core.py's import machinery caches in sys.modules - never
the user's real ~/.claude/metrics.

WHY THIS PROVES import_metrics_report() TOO, NOT JUST main()

main() calls `mr = import_metrics_report()` directly and passes the
result into load_data(args, mr=mr) - so a test that captures the
constructed app and asserts `app.mr is metrics_core` (the top-level
import in THIS file, which is the exact same singleton) proves
import_metrics_report actually imported the real metrics_core module
and returned it, not a mock or a fabricated stand-in. A canary run
during development (mutating import_metrics_report to return an unrelated
throwaway module) is reported in the task's report rather than kept as a
permanent test - the plan asks for a canary demonstration, not a second
copy of the assertion.

WHY ProjectLegend/ProjectRankingList/build_session_tree ARE CONSTRUCTED
DIRECTLY, NOT THROUGH A RUNNING APP

Same technique tests/test_tui_chart.py already established for TrendChart
(module docstring there explains why: PlotextPlot.render() and plain
public methods do not need a live compositor pass). Static and Tree
widgets behave identically - .render() and their own plain methods work
before mount, confirmed by hand before writing these tests. Building a
full app run just to reach a 12-project fixture would need its own
throwaway HOME and its own JSONL file; constructing the widget directly
with an in-memory records list is the same fixture, without the extra
machinery.

ON SessionDetailPanel.clear()

Grepping dashboard_tui.py for "`.clear(`" finds exactly one call site:
the method's own definition. No key, click, or lifecycle hook in the
module ever calls it - it is genuinely dead code from the running app's
point of view. It remains a plain public method of a widget under test
elsewhere in this suite, so it is tested directly like any other public
method (construct the panel, populate it via show_session, call clear(),
assert the body is empty) - but this is disclosed here as a finding, not
silently treated as reachable production behavior. See the task's report
for the severity call.
"""

import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_tui_state import isolate_tui_from_the_real_filesystem, seeded_tui_home  # noqa: E402
from textual.widgets import TabbedContent  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# main() and the lazy-import fallback
# ---------------------------------------------------------------------------


def test_main_builds_the_real_app_from_argv_and_the_real_metrics_core(
    monkeypatch, tmp_path_factory
):
    """Loads dashboard_tui.py a second time with __name__="__main__" and
    lets its own `if __name__ == "__main__": main()` guard run main() for
    real - proving both main()'s body and import_metrics_report()'s
    fallback in one honest execution, per this file's module docstring."""
    home = tmp_path_factory.mktemp("tui-main-home")
    seeded_tui_home(monkeypatch, home)
    monkeypatch.setattr(
        sys, "argv", ["dashboard_tui.py", "--label-budget", "0", "--project", "/projects/alpha"]
    )

    captured: dict[str, Any] = {}

    def fake_run(self: Any, *args: Any, **kwargs: Any) -> None:
        captured["app"] = self

    import textual.app as textual_app_module

    monkeypatch.setattr(textual_app_module.App, "run", fake_run)

    spec = importlib.util.spec_from_file_location("__main__", REPO_ROOT / "dashboard_tui.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    app = captured.get("app")
    assert app is not None, "main() never reached App.run() - it built nothing to run"
    # Not isinstance(app, tui.MetricsTUIApp): the fresh copy loaded under
    # "__main__" is a genuinely separate module object with its own class
    # object of the same name, so identity/subclass checks against the
    # normally-imported `tui` module always fail here even when main()
    # behaved correctly - the class NAME and its own module attributes are
    # what this test can honestly assert on.
    assert type(app).__name__ == "MetricsTUIApp"
    # The real metrics_core singleton, not a mock or an unrelated module -
    # this is what proves import_metrics_report() actually imported it,
    # rather than main() having been changed to skip that call entirely.
    assert app.mr is mr
    # args honoured all the way through: --project reached load_data's own
    # filter_records call (the fixture's "long-1" session is the only one
    # under /projects/alpha - a wrong or ignored --project would either
    # return every project or none), and --label-budget 0 reached it too
    # (proven indirectly: no `claude -p` subprocess call happened, or
    # isolate_tui_from_the_real_filesystem's patched subprocess.run would
    # have raised).
    assert app.args.project == "/projects/alpha"
    assert app.args.label_budget == 0
    records = app.data["records"]
    assert len(records) > 0, "the --project filter dropped every record"
    assert {r["session_id"] for r in records} == {"long-1"}
    # The path main() actually took: windowed to the default period
    # ("tudo" - no filter), not some other period fabricated for the test.
    assert app.data["period_id"] == mr.DEFAULT_PERIOD_ID


def test_import_metrics_report_returns_the_real_metrics_core_module(monkeypatch, tmp_path):
    """Called directly, not just as a side effect of main() - the two
    tests together prove both the fallback's wiring (previous test) and
    the function's own body (this one)."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    result = tui.import_metrics_report()
    assert result is mr
    assert result.METRICS_FILE == mr.METRICS_FILE


# ---------------------------------------------------------------------------
# action_toggle_more_projects ("m")
# ---------------------------------------------------------------------------


async def _drive_toggle_more_projects() -> dict[str, Any]:
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    async with tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args).run_test(
        size=(140, 45)
    ) as pilot:
        app = pilot.app
        await pilot.pause()
        await pilot.press("g")  # into Panorama, where ProjectRankingList lives
        await pilot.pause()
        panel = app.query_one(tui.ProjectRankingList)
        before = panel.expanded
        await pilot.press("m")
        await pilot.pause()
        after_one_press = panel.expanded
        await pilot.press("m")
        await pilot.pause()
        after_two_presses = panel.expanded
        return {
            "before": before,
            "after_one_press": after_one_press,
            "after_two_presses": after_two_presses,
        }


def test_m_toggles_project_ranking_expanded_state():
    result = _run(_drive_toggle_more_projects())
    assert result["before"] is False
    assert result["after_one_press"] is True, (
        "pressing 'm' did not flip ProjectRankingList.expanded - "
        "action_toggle_more_projects is not reaching toggle_more()"
    )
    assert result["after_two_presses"] is False, "a second 'm' must flip it back"


# ---------------------------------------------------------------------------
# the reverse tab switch (action_toggle_tab's else branch)
# ---------------------------------------------------------------------------


async def _drive_g_twice() -> dict[str, Any]:
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    async with tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args).run_test(
        size=(140, 45)
    ) as pilot:
        # pilot.app types as the generic App[None] base, which knows
        # nothing about MetricsTUIApp's own active_tab_id attribute - cast
        # to the concrete subclass rather than widen the real type with a
        # blanket ignore.
        app = cast("tui.MetricsTUIApp", pilot.app)
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        active_after_first_g = app.query_one(TabbedContent).active
        await pilot.press("g")
        await pilot.pause()
        return {
            "active_after_first_g": active_after_first_g,
            "active_after_second_g": app.query_one(TabbedContent).active,
            "active_tab_id_after_second_g": app.active_tab_id,
            "focused_type_after_second_g": type(app.focused).__name__
            if app.focused is not None
            else None,
        }


def test_second_g_switches_back_to_sessions_and_refocuses_the_tree():
    result = _run(_drive_g_twice())
    assert result["active_after_first_g"] == "tab-panorama"
    assert result["active_after_second_g"] == "tab-sessions", (
        "a second 'g' must return to the Sessões tab, not stay on or cycle past Panorama"
    )
    assert result["active_tab_id_after_second_g"] == "tab-sessions"
    assert result["focused_type_after_second_g"] == "Tree", (
        "the reverse switch must refocus the session Tree, mirroring what "
        "the forward switch does for the tab it opens"
    )


# ---------------------------------------------------------------------------
# ProjectLegend with zero projects
# ---------------------------------------------------------------------------


def test_project_legend_renders_sem_dados_with_no_chart_records():
    legend = tui.ProjectLegend(mr, [], lang="pt")
    assert legend.render() == "[dim]Sem dados.[/dim]"


def test_project_legend_click_is_a_no_op_with_no_series_keys():
    legend = tui.ProjectLegend(mr, [], lang="pt")
    posted: list[Any] = []
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(legend, "post_message", lambda msg: posted.append(msg))
        # No attribute of the click event is ever read once series_keys is
        # empty - a bare object() proves the early return happens before
        # event.x/.shift/.ctrl are touched.
        legend.on_click(object())
    finally:
        monkeypatch.undo()
    assert legend.highlighted is None
    assert posted == [], "on_click posted a Highlight message with nothing to highlight"


# ---------------------------------------------------------------------------
# ProjectRankingList's "hidden" branch (more than 10 projects)
# ---------------------------------------------------------------------------


def _records_for_n_projects(n: int) -> list[dict[str, Any]]:
    """n distinct projects, each with a distinct nonzero token total so
    compute_project_totals's ordering is unambiguous."""
    return [
        {
            "project_path": f"/projects/p{i:02d}",
            "input_tokens": (n - i) * 100,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        for i in range(n)
    ]


def test_project_ranking_list_hides_projects_past_ten_and_toggle_more_reveals_them():
    records = _records_for_n_projects(12)
    panel = tui.ProjectRankingList(mr, records, {}, {}, set(), lang="pt")

    collapsed = panel.render()
    assert "▸ + 2 projetos históricos" in collapsed
    assert "▾ menos" not in collapsed
    # Only the 10 highest-token projects are named while collapsed - the
    # two smallest (p10, p11 - the last two by descending token total)
    # must not appear at all.
    assert "p11" not in collapsed
    assert "p10" not in collapsed
    assert "p00" in collapsed  # the highest-token project is always shown

    panel.toggle_more()
    expanded = panel.render()
    assert "▾ menos" in expanded
    assert "▸ + 2 projetos históricos" not in expanded
    assert "p11" in expanded
    assert "p10" in expanded


def test_project_ranking_list_renders_sem_dados_with_no_tokens():
    panel = tui.ProjectRankingList(mr, [], {}, {}, set(), lang="pt")
    assert panel.render() == "[dim]Sem dados.[/dim]"


# ---------------------------------------------------------------------------
# build_session_tree's all-zero-token group skip
# ---------------------------------------------------------------------------


def test_build_session_tree_skips_a_project_whose_every_session_is_zero_token():
    records = [
        {
            "project_path": "/projects/all-zero",
            "session_id": "z1",
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "timestamp_start": "2026-01-01T00:00:00",
        },
        {
            "project_path": "/projects/has-tokens",
            "session_id": "r1",
            "input_tokens": 5,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "timestamp_start": "2026-01-01T00:00:00",
        },
    ]
    tree = tui.build_session_tree(records, {}, mr, lang="pt")
    labels = [str(child.label) for child in tree.root.children]
    assert len(labels) == 1, (
        f"expected only the non-zero-token project's group, got {labels!r} - "
        "the all-zero-token project must not appear as an empty node"
    )
    assert "has-tokens" in labels[0]


# ---------------------------------------------------------------------------
# SessionDetailPanel.clear() - dead code, tested directly (see module
# docstring's disclosure)
# ---------------------------------------------------------------------------


def test_session_detail_panel_clear_empties_the_body():
    panel = tui.SessionDetailPanel(mr, lang="pt")
    panel.show_session("some-session", [])
    assert panel._body.render() != ""
    panel.clear()
    assert str(panel._body.render()) == ""
