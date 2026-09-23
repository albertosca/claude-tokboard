"""Covers dashboard_html.py's page assembly (render_html) and CLI entry
point (main): argument handling, per-period block construction, and
main's file-writing/flag paths.

tests/test_dashboard_rendering.py already drives a fully generated page
through jsdom and asserts on computed visibility - do not duplicate that
here. tests/test_dashboard_html_backfill_note.py already covers
render_backfill_note/render_period_filter. This file covers only the
Python-side branches those two suites can't reach: render_html's own
per-record/per-period orchestration, and main's argument handling and
file-writing paths.

Every expected value below was derived by running the function under
test (not inferred from reading it) - see the task brief's rule 1.

Locators use a small stdlib-only HTML tree (parse_html below), which
turns each element's attributes into an order-independent dict, rather
than a regex anchored on a literal attribute sequence - this phase's
recurring failure mode (Task 2 shipped a regex anchored on
`data-period="tudo">`; Task 3 shipped an `_svg_block` helper anchored on
`<svg viewBox=... class=... id=...>` in that literal order, and a
reviewer proved a cosmetic attribute reorder broke 8 of 25 of its
tests). See test_period_block_locator_survives_attribute_reordering_and_
an_added_attribute below for the *executed* counterfactual proving this
one does not share that defect.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import metrics_core as mr
import metrics_core.labels as labels_module

import dashboard_html
import dashboard_html_assets

# Captured at import time, before any test monkeypatches HOME - main()'s
# tests below DO monkeypatch HOME (main resolves its output path and
# get_open_projects()'s sessions dir from it at call time), unlike
# tests/test_labels.py which never touches HOME at all and can safely
# call Path("~").expanduser() from inside a test. Recomputing "~" after
# HOME has been monkeypatched would silently resolve to the fake home
# instead of the real one, defeating the isolation proof below.
_REAL_HOME = Path(os.environ.get("HOME", str(Path.home())))


# ---------------------------------------------------------------------------
# A minimal, stdlib-only HTML tree: every element's attributes become an
# order-independent dict, and elements are queryable by tag+class - so a
# harmless attribute reorder or addition in production can never break a
# locator built on top of this.
# ---------------------------------------------------------------------------

_VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str | None]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[Any] = []

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def find_all(self, tag: str | None = None, css_class: str | None = None) -> list["_Node"]:
        results: list[_Node] = []
        for child in self.children:
            if isinstance(child, str):
                continue
            matches_tag = tag is None or child.tag == tag
            classes = (child.attrs.get("class") or "").split()
            matches_class = css_class is None or css_class in classes
            if matches_tag and matches_class:
                results.append(child)
            results.extend(child.find_all(tag, css_class))
        return results

    def has_attr(self, name: str) -> bool:
        return name in self.attrs


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.root = _Node("[root]", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, dict(attrs))
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def parse_html(html_out: str) -> _Node:
    builder = _TreeBuilder()
    builder.feed(html_out)
    return builder.root


# ---------------------------------------------------------------------------
# render_html fixtures
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 8, 20, 15, 0, 0).astimezone()


def _period_windows(now: datetime | None = None) -> dict[str, tuple[datetime, datetime] | None]:
    now = now or _now()
    return {period.id: mr.resolve_period(period.id, now) for period in mr.PERIODS}


def _empty_render_html_kwargs() -> dict[str, Any]:
    return {
        "records": [],
        "subagents_by_session": {},
        "chart_records": [],
        "daily_chart_records": [],
        "period_windows": _period_windows(),
        "project_first_seen": {},
        "project_last_seen": {},
        "open_projects": set(),
        "now": _now(),
    }


def _session_record(
    session_id: str, project_path: str | None, tokens: int = 100, **overrides: Any
) -> dict[str, Any]:
    base = {
        "session_id": session_id,
        "project_path": project_path,
        "timestamp_start": "2026-08-10T10:00:00Z",
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "duration_seconds": 60,
        "turn_count": 1,
        "tool_call_count": 0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


def test_render_html_wires_style_and_script_from_assets() -> None:
    # If render_html ever stops threading these two constants through, the
    # page silently ships with no CSS/JS - assert the literal constants
    # appear so an accidental unwiring fails loudly.
    out = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="pt")
    assert dashboard_html_assets.STYLE in out
    assert dashboard_html_assets.SCRIPT in out


def test_render_html_empty_record_set_produces_a_valid_page_without_raising() -> None:
    out = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="pt")
    assert out.startswith("<!DOCTYPE html>")
    assert out.rstrip().endswith("</html>")


def test_render_html_default_highlight_opens_with_every_series_and_project_color_selected() -> None:
    chart_records = [
        {"project_path": "/work/proj-a", "input_tokens": 10},
        {"project_path": "/work/proj-b", "input_tokens": 10},
    ]
    # Derived by running build_group_colors with the exact same inputs
    # render_html itself passes to it, not inferred from reading the
    # comment at 940-948.
    group_colors = dashboard_html.build_group_colors(chart_records, [])
    kwargs = _empty_render_html_kwargs()
    kwargs["chart_records"] = chart_records
    out = dashboard_html.render_html(**kwargs, lang="pt")

    body_tags = parse_html(out).find_all("body")
    assert len(body_tags) == 1
    highlight = body_tags[0].attrs.get("data-highlight")
    assert highlight is not None
    tokens = set(highlight.split(" "))
    expected = {"series-1", "series-2", "series-3", "series-4"} | set(group_colors.values())
    assert tokens == expected


def test_render_html_marks_exactly_one_default_period_active_per_block_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # render_html builds THREE independent families of `.period-block` divs
    # (trend/panorama/legend - see the module's own comment at 862-884): a
    # bug marking the wrong number of periods "active" (non-hidden) in any
    # one of them is exactly named mutation 1. The leaf renderers are
    # stubbed to embed which family+period they were called for, so the
    # assembly logic under test is render_html's own hidden_attr decision,
    # not chart internals (already covered by test_dashboard_html_charts.py).
    call_order: list[int] = []

    def fake_trend(
        records: Any, group_colors: Any, period_id: str, now: Any = None, *, lang: str
    ) -> str:
        return f"TREND::{period_id}"

    def fake_panorama(
        records: Any, pfs: Any, pls: Any, op: Any, period_id: str, *, lang: str
    ) -> str:
        return f"PANORAMA::{period_id}"

    def fake_legend(totals: Any, *, lang: str) -> str:
        idx = len(call_order)
        call_order.append(idx)
        return f"LEGEND::{mr.PERIODS[idx].id}"

    monkeypatch.setattr(dashboard_html, "render_trend_chart", fake_trend)
    monkeypatch.setattr(dashboard_html, "render_project_totals_chart", fake_panorama)
    monkeypatch.setattr(dashboard_html, "render_composition_legend", fake_legend)

    out = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="pt")
    blocks = parse_html(out).find_all("div", "period-block")
    # The module's own comment (862-884) measured this shape on the test
    # fixture's real page: 3 families x 5 periods = 15 period-block divs.
    assert len(blocks) == 3 * len(mr.PERIODS)

    by_family: dict[str, dict[str, _Node]] = {"TREND": {}, "PANORAMA": {}, "LEGEND": {}}
    for block in blocks:
        family, _, period_id = block.text().partition("::")
        assert period_id == block.attrs.get("data-period")
        by_family[family][period_id] = block

    for family, period_map in by_family.items():
        assert set(period_map) == {p.id for p in mr.PERIODS}
        active_ids = [pid for pid, block in period_map.items() if not block.has_attr("hidden")]
        assert active_ids == [mr.DEFAULT_PERIOD_ID], (
            f"{family}: expected only the default active, got {active_ids}"
        )


def test_period_block_locator_survives_attribute_reordering_and_an_added_attribute() -> None:
    # The executed counterfactual required by this phase's standing
    # instruction: prove parse_html/find_all locate by class alone,
    # independent of attribute order or an extra attribute, by actually
    # feeding it markup where those things vary - not by asserting it in
    # a docstring (Task 3's exact defect).
    reordered = '<div data-period="tudo" hidden class="period-block">x</div>'
    node = parse_html(reordered).find_all("div", "period-block")[0]
    assert node.attrs.get("data-period") == "tudo"
    assert node.has_attr("hidden")

    with_extra_attribute = '<div class="period-block" id="whatever" data-period="tudo">y</div>'
    node2 = parse_html(with_extra_attribute).find_all("div", "period-block")[0]
    assert node2.attrs.get("data-period") == "tudo"
    assert not node2.has_attr("hidden")

    multi_class = '<div class="group-count period-block" data-period="tudo">z</div>'
    node3 = parse_html(multi_class).find_all("div", "period-block")[0]
    assert node3.text() == "z"


def test_render_html_omits_a_group_whose_every_session_is_zero_token() -> None:
    # Alberto: omit zero-token sections entirely (2026-07-20 comment at
    # 805-810) - a session that never recorded tokens should not leave a
    # row, and a group whose every session is zero-token should not leave
    # a header either.
    zero_record = _session_record("zero1", "/work/proj-a", tokens=0)
    live_record = _session_record("live1", "/work/proj-b", tokens=100)
    kwargs = _empty_render_html_kwargs()
    kwargs["records"] = [zero_record, live_record]
    out = dashboard_html.render_html(**kwargs, lang="pt")
    assert 'id="row-zero1"' not in out
    assert 'id="row-live1"' in out


def test_render_html_numbers_a_task_label_only_when_shared_by_two_or_more_sessions() -> None:
    r1 = _session_record("dup1", "/work/proj-a", tokens=10, task_label="same label")
    r2 = _session_record("dup2", "/work/proj-a", tokens=10, task_label="same label")
    r3 = _session_record("uniq1", "/work/proj-a", tokens=10, task_label="different label")
    r4 = _session_record("nolabel1", "/work/proj-a", tokens=10)  # no task_label key at all
    kwargs = _empty_render_html_kwargs()
    kwargs["records"] = [r1, r2, r3, r4]
    out = dashboard_html.render_html(**kwargs, lang="pt")

    label_spans = [n.text() for n in parse_html(out).find_all("span", "label-col")]
    assert label_spans.count("same label (1)") == 1
    assert label_spans.count("same label (2)") == 1
    assert "different label" in label_spans
    assert "different label (1)" not in label_spans


def test_render_html_computes_each_records_own_period_membership_and_passes_it_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # periods_for() is render_html's own nested closure - the only way to
    # observe it is through what it hands render_session_row/
    # render_detail_panel. now/window derivation and the record's
    # membership were both verified by actually running resolve_period and
    # filter_sessions_by_window before writing this assertion (rule 1):
    # a session started 10 days before `now` overlaps "15-dias"/"mes"/
    # "tudo" but not "ontem"/"desde-segunda".
    captured: dict[str, Any] = {}
    real_row = dashboard_html.render_session_row
    real_panel = dashboard_html.render_detail_panel

    def spy_row(*args: Any, **kwargs: Any) -> str:
        captured["row_period_ids"] = kwargs.get("period_ids")
        return real_row(*args, **kwargs)

    def spy_panel(*args: Any, **kwargs: Any) -> str:
        captured["panel_period_ids"] = kwargs.get("period_ids")
        return real_panel(*args, **kwargs)

    monkeypatch.setattr(dashboard_html, "render_session_row", spy_row)
    monkeypatch.setattr(dashboard_html, "render_detail_panel", spy_panel)
    record = _session_record(
        "mid-age", "/work/proj-a", tokens=10, timestamp_start="2026-08-10T10:00:00Z"
    )
    kwargs = _empty_render_html_kwargs()
    kwargs["records"] = [record]
    kwargs["period_windows"] = _period_windows(_now())
    dashboard_html.render_html(**kwargs, lang="pt")

    assert captured["row_period_ids"] == ("15-dias", "mes", "tudo")
    assert captured["panel_period_ids"] == ("15-dias", "mes", "tudo")


def test_render_html_counts_only_nonzero_token_subagents_and_defaults_to_no_subagents() -> None:
    with_subagents = _session_record("has-subs", "/work/proj-a", tokens=10)
    without_subagents = _session_record("no-subs", "/work/proj-a", tokens=10)
    subagents_by_session = {
        "has-subs": [
            {"agent_id": "a1", "input_tokens": 0},  # zero-token: excluded from the count
            {"agent_id": "a2", "input_tokens": 5},  # counted
        ]
    }
    kwargs = _empty_render_html_kwargs()
    kwargs["records"] = [with_subagents, without_subagents]
    kwargs["subagents_by_session"] = subagents_by_session
    out = dashboard_html.render_html(**kwargs, lang="pt")

    rows = {
        n.attrs.get("id"): n
        for n in parse_html(out).find_all("div", "session-row")
        if n.attrs.get("id")
    }
    badge_with = rows["row-has-subs"].find_all("span", "subagent-col")[0].text()
    badge_without = rows["row-no-subs"].find_all("span", "subagent-col")[0].text()
    # render_session_row: subagent_badge = f"🤖{subagent_count}" if subagent_count else ""
    assert badge_with == "🤖1"
    assert badge_without == ""


# ---------------------------------------------------------------------------
# main() - isolation
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(r) for r in records)
    path.write_text(body + "\n" if records else "")


@pytest.fixture(autouse=True)
def isolate_main_from_the_real_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """main() resolves its paths through two different mechanisms, and
    both need isolating:

    - METRICS_FILE/SUBAGENTS_FILE/TASK_LABELS_FILE/AGENT_LABELS_FILE are
      module-level constants resolved from HOME at IMPORT time - see
      tests/test_labels.py's own docstring. monkeypatch.setenv("HOME", ...)
      alone would leave these pointed at the real ~/.claude/metrics files,
      so they are monkeypatched directly instead.
    - main()'s own output_path and mr.get_open_projects()'s sessions_dir
      are computed fresh from HOME at CALL time (Path(...).expanduser()
      inside main()/get_open_projects, not a module constant) - for
      these, monkeypatch.setenv("HOME", ...) is the correct and sufficient
      mechanism.

    Both are applied here so every path main() can touch lands under
    tmp_path, never under the real ~/.claude/metrics/.
    """
    home = tmp_path / "home"
    (home / ".claude" / "metrics").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(mr, "METRICS_FILE", str(home / ".claude" / "metrics" / "sessions.jsonl"))
    monkeypatch.setattr(mr, "SUBAGENTS_FILE", str(home / ".claude" / "metrics" / "subagents.jsonl"))
    monkeypatch.setattr(
        labels_module, "TASK_LABELS_FILE", str(home / ".claude" / "metrics" / "task_labels.json")
    )
    monkeypatch.setattr(
        labels_module, "AGENT_LABELS_FILE", str(home / ".claude" / "metrics" / "agent_labels.json")
    )
    return home


@pytest.fixture(autouse=True)
def forbid_real_label_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every main() test below runs with --label-budget 0 (main's own
    default), which must never reach a real subprocess call. Failing loudly
    here - the same mechanism tests/test_labels.py uses, patching the
    `subprocess` module object both files import - converts a silent real
    `claude -p` call into a hard test failure instead of an expensive,
    network-touching false green."""

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("real subprocess.run must never be called in tests")

    monkeypatch.setattr(subprocess, "run", fail)


def _run_main(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["dashboard_html.py", *argv])
    dashboard_html.main()


# ---------------------------------------------------------------------------
# main() - isolation proof
# ---------------------------------------------------------------------------


def test_main_never_writes_the_real_dashboard_html(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    real_path = _REAL_HOME / ".claude" / "metrics" / "dashboard.html"
    before = real_path.stat().st_mtime if real_path.exists() else None

    home = isolate_main_from_the_real_filesystem
    _write_jsonl(
        home / ".claude" / "metrics" / "sessions.jsonl",
        [_session_record("s1", "/work/proj-a", tokens=10)],
    )
    _run_main(monkeypatch, ["--html", "--label-budget", "0"])
    capsys.readouterr()

    if before is None:
        assert not real_path.exists()
    else:
        assert real_path.stat().st_mtime == before


# ---------------------------------------------------------------------------
# main() - --html mode
# ---------------------------------------------------------------------------


def test_main_html_mode_writes_the_dashboard_to_the_expected_path_and_prints_it(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = isolate_main_from_the_real_filesystem
    _write_jsonl(
        home / ".claude" / "metrics" / "sessions.jsonl",
        [_session_record("s1", "/work/proj-a", tokens=10)],
    )
    _run_main(monkeypatch, ["--html", "--label-budget", "0"])
    out = capsys.readouterr().out.strip()

    expected_path = home / ".claude" / "metrics" / "dashboard.html"
    assert out == str(expected_path)
    assert expected_path.exists()
    assert expected_path.read_text().startswith("<!DOCTYPE html>")


def test_main_plain_mode_prints_table_and_summary_and_writes_no_file(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = isolate_main_from_the_real_filesystem
    record = _session_record("s1", "/work/proj-a", tokens=10, tag="mytag", duration_seconds=42)
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", [record])
    _run_main(monkeypatch, [])
    out = capsys.readouterr().out

    # format_table/format_summary's own exact formatting is pinned in
    # tests/test_dashboard_html_formatters.py (Task 1) - this only checks
    # that main() calls and prints both, in plain mode, instead of writing
    # a file.
    assert "s1" in out
    assert "mytag" in out
    assert "1 session(s)" in out
    assert not (home / ".claude" / "metrics" / "dashboard.html").exists()


def test_main_label_budget_zero_suppresses_generation_leaving_the_session_uncached(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = isolate_main_from_the_real_filesystem
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "system", "subtype": "away_summary", "content": "an away summary"})
        + "\n"
    )
    record = _session_record("s1", "/work/proj-a", tokens=10, transcript_path=str(transcript))
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", [record])

    # forbid_real_label_subprocess (autouse) would fail this test outright
    # if main() ever reached compress_to_label's subprocess call - the
    # absence of that failure IS the "no subprocess attempted" assertion.
    _run_main(monkeypatch, ["--html", "--label-budget", "0"])
    capsys.readouterr()

    cache = json.loads((home / ".claude" / "metrics" / "task_labels.json").read_text())
    # get_task_label's own comment: budget-exhausted sessions are left
    # UNCACHED (no "__no_label__" sentinel), not skipped for lack of a
    # source - an empty cache here proves generation was attempted and
    # stopped exactly at the budget check.
    assert cache == {}


def test_main_all_flag_is_a_backward_compatible_no_op(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # args.all is parsed but never read anywhere in main() (confirmed by
    # grepping the module) - its own help text says it "names what already
    # happens by default". Pin that as the real property: identical output
    # with or without the flag, not "narrows what's rendered" (it doesn't).
    home = isolate_main_from_the_real_filesystem
    _write_jsonl(
        home / ".claude" / "metrics" / "sessions.jsonl",
        [_session_record("s1", "/work/proj-a", tokens=10)],
    )

    _run_main(monkeypatch, [])
    without_all = capsys.readouterr().out
    _run_main(monkeypatch, ["--all"])
    with_all = capsys.readouterr().out
    assert with_all == without_all


def test_main_tag_filter_narrows_records_rendered(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = isolate_main_from_the_real_filesystem
    r1 = _session_record("keepme", "/work/proj-a", tokens=10, tag="alpha")
    r2 = _session_record("dropme", "/work/proj-a", tokens=10, tag="beta")
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", [r1, r2])
    _run_main(monkeypatch, ["--tag", "alpha"])
    out = capsys.readouterr().out
    assert "keepme" in out
    assert "dropme" not in out


def test_main_project_filter_narrows_records_rendered(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = isolate_main_from_the_real_filesystem
    r1 = _session_record("keepme", "/work/proj-a", tokens=10)
    r2 = _session_record("dropme", "/work/proj-b", tokens=10)
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", [r1, r2])
    _run_main(monkeypatch, ["--project", "/work/proj-a"])
    out = capsys.readouterr().out
    assert "keepme" in out
    assert "dropme" not in out


def test_main_days_filter_excludes_records_older_than_the_window(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = isolate_main_from_the_real_filesystem
    now = datetime.now().astimezone()
    fresh = _session_record(
        "freshone", "/work/proj-a", tokens=10, timestamp_start=(now - timedelta(days=1)).isoformat()
    )
    stale = _session_record(
        "staleone",
        "/work/proj-a",
        tokens=10,
        timestamp_start=(now - timedelta(days=100)).isoformat(),
    )
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", [stale, fresh])
    _run_main(monkeypatch, ["--days", "30"])
    out = capsys.readouterr().out
    assert "freshone" in out
    assert "staleone" not in out


def test_main_limit_slices_the_tail_not_the_head_of_the_record_list(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Pins the one `[:N]` slice bound in this task's scope: `records[-args.
    # limit:]` at line 1042, only reachable in plain (non --html) mode.
    # session_ids are written in file order sA..sE, which survives dedupe/
    # normalize/filter unchanged (verified: dedupe_by_key preserves
    # first-seen order for distinct keys) - a head slice would keep sA/sB
    # instead of sD/sE, so this discriminates head-vs-tail as well as the
    # exact count.
    home = isolate_main_from_the_real_filesystem
    records = [
        _session_record(sid, "/work/proj-a", tokens=10) for sid in ["sA", "sB", "sC", "sD", "sE"]
    ]
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", records)
    _run_main(monkeypatch, ["-n", "2"])
    out = capsys.readouterr().out
    for excluded in ("sA", "sB", "sC"):
        assert excluded not in out
    for included in ("sD", "sE"):
        assert included in out


def test_main_saves_label_caches_at_exactly_the_save_every_boundary(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Found by the unnamed-mutation sweep: test_main_saves_label_caches_
    # periodically_during_a_large_batch below only asserted the dashboard
    # got written, not WHEN the periodic save happened - an off-by-one on
    # `i % SAVE_EVERY == 0` (SAVE_EVERY == 10) survived the whole suite.
    # main()'s own comment: the cache is saved every SAVE_EVERY completions
    # "not just at the end - a killed/interrupted run keeps whatever it
    # already labeled instead of repeating the whole cold-cache scan next
    # time" - a resilience guard worth its own named test (rule 2).
    #
    # An earlier version used a single batch of 12 keys and asserted 2 saves
    # per cache. That only bounds the modulus, it doesn't pin it: every
    # cadence in 7..12 produces exactly one mid-loop save at 12 keys, so
    # SAVE_EVERY could drift from 10 to 7 undetected (review M2, measured).
    #
    # Pinned instead by straddling the boundary. With a cadence of m, a
    # batch of N keys fires N // m mid-loop saves plus one unconditional
    # post-loop save. Asserting 9 keys -> 1 call and 10 keys -> 2 calls
    # admits exactly m == 10: any smaller m already fires mid-loop at N=9
    # (9 // m >= 1, so 2+ calls), and any larger m still hasn't fired at
    # N=10 (10 // m == 0, so 1 call).
    home = isolate_main_from_the_real_filesystem

    def save_calls_for_batch(batch_size: int) -> tuple[int, int]:
        records = [
            _session_record(f"many-s{i}", "/work/proj-a", tokens=1) for i in range(batch_size)
        ]
        _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", records)
        subagents = [
            {
                "session_id": f"many-s{i}",
                "agent_id": f"many-a{i}",
                "input_tokens": 1,
                "duration_seconds": 1,
            }
            for i in range(batch_size)
        ]
        _write_jsonl(home / ".claude" / "metrics" / "subagents.jsonl", subagents)

        agent_save_calls = 0
        session_save_calls = 0
        real_save_agent = mr.save_agent_label_cache
        real_save_session = mr.save_label_cache

        def counting_save_agent(cache: dict[str, str]) -> None:
            nonlocal agent_save_calls
            agent_save_calls += 1
            real_save_agent(cache)

        def counting_save_session(cache: dict[str, str]) -> None:
            nonlocal session_save_calls
            session_save_calls += 1
            real_save_session(cache)

        with monkeypatch.context() as patch:
            patch.setattr(mr, "save_agent_label_cache", counting_save_agent)
            patch.setattr(mr, "save_label_cache", counting_save_session)
            _run_main(patch, ["--html", "--label-budget", "0"])
        capsys.readouterr()
        return agent_save_calls, session_save_calls

    # One key short of the cadence: only the unconditional post-loop save.
    assert save_calls_for_batch(9) == (1, 1)
    # Exactly on it: the mid-loop save fires once, plus the post-loop one.
    assert save_calls_for_batch(10) == (2, 2)


def test_main_html_mode_is_unbounded_by_limit(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Found by the unnamed-mutation sweep: `if not args.html: records =
    # records[-args.limit:]` at line 1041-1042 was completely unguarded by
    # any test in this file before this one - defeating the `not` (always
    # slicing, even under --html) survived the whole suite. The module's
    # own comment at 1038-1040 documents this as deliberate ("the --html
    # session-list table is unbounded by -n ... sem limite (mostra as
    # 2401)") - pin it directly instead of only relying on the plain-mode
    # slice-bound test above to imply it by omission.
    home = isolate_main_from_the_real_filesystem
    records = [_session_record(f"htmlrow{i}", "/work/proj-a", tokens=10) for i in range(5)]
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", records)
    _run_main(monkeypatch, ["--html", "--label-budget", "0", "-n", "2"])
    out_path = Path(capsys.readouterr().out.strip())
    html_out = out_path.read_text()
    for sid in ("htmlrow0", "htmlrow1", "htmlrow2", "htmlrow3", "htmlrow4"):
        assert f'id="row-{sid}"' in html_out


def test_main_skips_subagent_and_session_records_with_no_id(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # metrics-log.py/subagent-metrics-log.py have no dedup upstream, and a
    # malformed/partial line missing its id field is exactly what
    # `if key is None: continue` guards against, in both the session and
    # subagent id-building loops - assert this doesn't raise, which is the
    # whole point of the guard.
    home = isolate_main_from_the_real_filesystem
    normal_session = _session_record("s1", "/work/proj-a", tokens=10)
    orphan_session = {"input_tokens": 5}  # no session_id at all
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", [normal_session, orphan_session])
    orphan_agent = {"input_tokens": 5}  # no agent_id at all
    real_agent = {"session_id": "s1", "agent_id": "a1", "input_tokens": 5, "duration_seconds": 1}
    _write_jsonl(home / ".claude" / "metrics" / "subagents.jsonl", [orphan_agent, real_agent])

    _run_main(monkeypatch, ["--html", "--label-budget", "0"])
    out = capsys.readouterr().out.strip()
    html_out = Path(out).read_text()
    assert html_out.startswith("<!DOCTYPE html>")
    # Not raising is necessary but not sufficient - confirm the well-formed
    # record among the malformed ones actually made it into the page rather
    # than the guard accidentally skipping it too.
    assert 'id="row-s1"' in html_out


def test_main_saves_label_caches_periodically_during_a_large_batch(
    monkeypatch: pytest.MonkeyPatch,
    isolate_main_from_the_real_filesystem: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # SAVE_EVERY == 10 inside main(): `if i % SAVE_EVERY == 0` is only
    # reachable with >=10 distinct keys in a single run - every other test
    # in this file uses a handful of records and never trips it, leaving
    # that branch's True side unexercised in both the agent-label and
    # session-label loops. 12 distinct sessions/agents guarantees at least
    # one periodic save in each, with headroom past the boundary.
    home = isolate_main_from_the_real_filesystem
    records = [_session_record(f"many-s{i}", "/work/proj-a", tokens=1) for i in range(12)]
    _write_jsonl(home / ".claude" / "metrics" / "sessions.jsonl", records)
    subagents = [
        {
            "session_id": f"many-s{i}",
            "agent_id": f"many-a{i}",
            "input_tokens": 1,
            "duration_seconds": 1,
        }
        for i in range(12)
    ]
    _write_jsonl(home / ".claude" / "metrics" / "subagents.jsonl", subagents)

    _run_main(monkeypatch, ["--html", "--label-budget", "0"])
    out = capsys.readouterr().out.strip()
    assert Path(out).read_text().startswith("<!DOCTYPE html>")
