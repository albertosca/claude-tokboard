"""Data-shape edges the committed fixture never reaches (Phase 3e, Task 5).

Tasks 1-4 covered measurement, the chart's tokens mode, mouse clicks, and
main()/unbound actions. What is left after those is not a widget behaviour
but a handful of guard clauses and one early return that only fire on a
SHAPE of input data the 8-session/7-project committed fixture never
produces: an explicit `--limit`, a subagent record with no `agent_id`, a
session record with no `session_id`, and a chart with fewer than two days
of data.

Each test below builds its own tiny, throwaway HOME (via
tests/test_tui_state.py's isolate_tui_from_the_real_filesystem) rather than
reusing the committed fixture, because the shape under test is exactly what
that fixture was built to avoid needing.

THREE ARCS THIS FILE DELIBERATELY DOES NOT CLOSE

The pre-task missing-branch report named seven arcs. Four are closed here.
The other three were investigated and found genuinely unreachable given
the code as written - not "hard to construct a fixture for", but
structurally impossible, because an earlier step already enforces the
invariant the branch exists to check:

- dashboard_tui.py:124->119 (`unique_agents`'s "already has a transcript,
  don't overwrite" branch) - `subagent_records` at this point is the
  output of `mr.dedupe_subagents`, which already guarantees at most one
  record per non-None `agent_id` (ties broken by `duration_seconds`,
  `>=` so the later record wins). A record with no `agent_id` hits the
  `continue` on the line above and never reaches this branch at all. So
  by the time this loop runs, `existing` is always None for the one and
  only occurrence of any given non-None key - proven empirically: two
  seed records sharing one `agent_id` produce exactly one call to
  `mr.get_agent_label`, not two, for the record `dedupe_subagents` itself
  chose to keep.
- dashboard_tui.py:179->174 (`unique_sessions`'s sibling branch) - same
  shape, one level up: `records` here is the output of `mr.dedupe_sessions`
  keyed on the same field (`session_id`) this loop re-checks. Proven the
  same way: two seed records sharing one `session_id` produce exactly one
  call to `mr.get_task_label`.
- dashboard_tui.py:516->518 (`if series_lists:` in `render_chart`'s pane
  branch) - `series_lists` is built from `series_keys`, which comes from
  `compute_pane_projects(self.mr, self.chart_records)` - the SAME
  `self.chart_records` that `aggregate_by_day(self.chart_records)` (a few
  lines above) used to produce `days`. `compute_pane_projects` puts every
  record's `color_group_key(project_path)` into `totals` unconditionally
  (never raises, never excludes - confirmed by reading `color_group_key`,
  which returns `project_path` unchanged, including None, whenever it is
  falsy or has no worktree marker). So any record that contributed a day
  bucket to `days` necessarily contributed at least one key to `totals`,
  which means `top_keys` (`ordered[:PANE_MAX_PROJECTS]`) cannot be empty
  whenever `days` is non-trivial. Proven empirically: two records with
  `project_path: None` on two different days still produce
  `top_keys == [None]`, not `[]`.

No pragma is added for any of the three - this is disclosure for the
controller to rule on, per the phase's Global Constraints, not a unilateral
call to exclude them.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_tui_state import isolate_tui_from_the_real_filesystem  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def _write_sessions(home: Path, records: list[dict[str, Any]]) -> None:
    import json

    metrics_dir = home / ".claude" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    with (metrics_dir / "sessions.jsonl").open("w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    (metrics_dir / "subagents.jsonl").touch()


def _write_subagents(home: Path, records: list[dict[str, Any]]) -> None:
    import json

    metrics_dir = home / ".claude" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    (metrics_dir / "sessions.jsonl").touch()
    with (metrics_dir / "subagents.jsonl").open("w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def _session(session_id: str, day: int, tokens: int = 1) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "project_path": "/projects/p",
        "timestamp_start": f"2026-01-{day:02d}T09:00:00",
        "timestamp_end": f"2026-01-{day:02d}T10:00:00",
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


# ---------------------------------------------------------------------------
# --limit (dashboard_tui.py:99)
# ---------------------------------------------------------------------------


def test_limit_keeps_only_the_tail_of_the_session_list(monkeypatch, tmp_path):
    """`records[-args.limit:]` is a slice, not a filter - it must keep the
    LAST n sessions in load order, not merely n of them (a bug that picked
    the first n, or an arbitrary n, would pass a test that only checks the
    count)."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    _write_sessions(
        tmp_path,
        [_session(f"s{i}", day=i + 1) for i in range(1, 5)],  # s1..s4, day 2..5
    )
    args = tui.build_arg_parser().parse_args(["--label-budget", "0", "--limit", "2"])
    base = tui.load_base_data(args, mr=mr)
    assert [r["session_id"] for r in base["records"]] == ["s3", "s4"], (
        "--limit 2 must keep the two MOST RECENT (last, in load order) "
        "sessions, not the first two or any other pair"
    )


def test_no_limit_keeps_every_session(monkeypatch, tmp_path):
    """The default (no --limit) branch, for contrast - unbounded stays
    unbounded, per the comment above the flag's definition."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    _write_sessions(tmp_path, [_session(f"s{i}", day=i + 1) for i in range(1, 5)])
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(args, mr=mr)
    assert [r["session_id"] for r in base["records"]] == ["s1", "s2", "s3", "s4"]


# ---------------------------------------------------------------------------
# missing agent_id (dashboard_tui.py:121-122)
# ---------------------------------------------------------------------------


def test_subagent_with_no_agent_id_is_skipped_without_crashing(monkeypatch, tmp_path):
    """A subagent record with no agent_id cannot be looked up in
    unique_agents by any key - the guard exists so it is dropped from label
    resolution instead of crashing on `unique_agents[None]` collisions
    (dedupe_by_key's own no_id_records path preserves EVERY such record
    verbatim, so there can be more than one). Proven by two levers: no
    exception propagates, and mr.get_agent_label is never called for a key
    of None (only for the one real agent_id also present)."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    _write_sessions(tmp_path, [_session("s1", day=1)])
    _write_subagents(
        tmp_path,
        [
            {
                "session_id": "s1",
                "input_tokens": 1,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            },  # no agent_id at all
            {
                "agent_id": "a1",
                "session_id": "s1",
                "agent_transcript_path": "/t",
                "input_tokens": 1,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            },
        ],
    )
    calls: list[dict[str, Any]] = []
    original = mr.get_agent_label

    def spy(record: dict[str, Any], cache: Any, budget: Any) -> Any:
        calls.append(record)
        return original(record, cache, budget)

    monkeypatch.setattr(mr, "get_agent_label", spy)
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(args, mr=mr)  # must not raise
    assert len(calls) == 1, (
        f"expected exactly one label lookup (for the real agent_id) - got "
        f"{len(calls)}; the agent_id-less record must never reach "
        "unique_agents"
    )
    assert calls[0]["agent_id"] == "a1"
    # The malformed record is still present in the per-session grouping
    # (that grouping has no id-based dedup at all) - it just carries no
    # label, which is the observable consequence of being skipped above.
    grouped = base["subagents_by_session"]["s1"]
    assert len(grouped) == 2
    labelless = [s for s in grouped if s.get("agent_id") is None]
    assert len(labelless) == 1
    assert labelless[0]["agent_label"] is None


# ---------------------------------------------------------------------------
# missing session_id (dashboard_tui.py:176-177)
# ---------------------------------------------------------------------------


def test_session_with_no_session_id_is_skipped_without_crashing(monkeypatch, tmp_path):
    """Mirror of the subagent case, one level up: a session record with no
    session_id (a legacy or malformed row) cannot be a unique_sessions key,
    so it is dropped from task-label resolution rather than crashing."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    normal = _session("s1", day=1)
    malformed = _session("s2", day=2)
    del malformed["session_id"]
    _write_sessions(tmp_path, [normal, malformed])

    calls: list[dict[str, Any]] = []
    original = mr.get_task_label

    def spy(record: dict[str, Any], cache: Any, budget: Any) -> Any:
        calls.append(record)
        return original(record, cache, budget)

    monkeypatch.setattr(mr, "get_task_label", spy)
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(args, mr=mr)  # must not raise
    assert len(calls) == 1, (
        f"expected exactly one label lookup (for the real session_id) - "
        f"got {len(calls)}; the session_id-less record must never reach "
        "unique_sessions"
    )
    assert calls[0]["session_id"] == "s1"
    # Both records still flow through to the record list itself - only
    # task-label resolution skips the malformed one.
    assert len(base["records"]) == 2
    malformed_out = next(r for r in base["records"] if r.get("session_id") is None)
    assert malformed_out["task_label"] is None


# ---------------------------------------------------------------------------
# render_chart's `if len(days) < 2: return` (dashboard_tui.py:494-495)
# ---------------------------------------------------------------------------


def test_render_chart_returns_early_with_fewer_than_two_days_of_data():
    """A single day of chart_records must never reach the series-building
    code below the early return - pinned by checking that pane_series_keys
    (only ever assigned past that point) was never set. Constructed
    directly, not through a running app: render_chart() touches self.plt
    (set in TrendChart.__init__, no mount required) and self.refresh()
    (a bare Widget method), neither of which needs a live App context -
    confirmed by hand before writing this test, exactly as
    tests/test_tui_chart.py's docstring did for chart.render()."""
    single_day_records = [_session("only-one", day=1)]
    chart = tui.TrendChart(mr, single_day_records)
    chart.render_chart()
    assert not hasattr(chart, "pane_series_keys"), (
        "render_chart kept going past the len(days) < 2 guard - "
        "pane_series_keys is only ever set inside the code that guard "
        "exists to skip"
    )


def test_render_chart_returns_early_with_zero_days_of_data():
    """The other side of the same boundary - no records at all, not just
    one day's worth."""
    chart = tui.TrendChart(mr, [])
    chart.render_chart()
    assert not hasattr(chart, "pane_series_keys")


def test_render_chart_proceeds_with_two_days_of_data():
    """Negative control for the two tests above: two distinct days is
    enough to clear the guard and reach the series-building code - without
    this, "pane_series_keys never gets set" could just as easily mean the
    guard is permanently (mis)tripped rather than correctly gated on the
    day count."""
    two_day_records = [_session("a", day=1), _session("b", day=2)]
    chart = tui.TrendChart(mr, two_day_records)
    chart.render_chart()
    assert hasattr(chart, "pane_series_keys")


# ---------------------------------------------------------------------------
# render_chart's partial-bucket annotation (backlog, 2026-09-02: "the trend
# chart's last bar is always a partial bucket, and always reads as a
# decline")
# ---------------------------------------------------------------------------


def _august_session(session_id: str, day: int, tokens: int = 1) -> dict[str, Any]:
    """Same shape as _session, but in August - so its buckets can be
    placed relative to a `now` chosen for the tests below, independent of
    _session's own January fixtures elsewhere in this file."""
    return {
        "session_id": session_id,
        "project_path": "/projects/p",
        "timestamp_start": f"2026-08-{day:02d}T09:00:00",
        "timestamp_end": f"2026-08-{day:02d}T10:00:00",
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


_AUGUST_RECORDS = [
    _august_session("a", day=1),
    _august_session("b", day=5),
    _august_session("c", day=10),
]


def test_render_chart_annotates_the_last_label_when_its_bucket_is_still_running():
    # `now` pinned to the exact midpoint of the last bucket's day
    # (2026-08-10 00:00 -> 2026-08-11 00:00), same hand-verified 50% as
    # test_granularity.py's own partial_bucket_fraction tests. Read via
    # chart.plt.build() rather than chart.render(): the latter needs a
    # live App (self.app.theme), which this direct-instantiation style
    # deliberately avoids, per this file's own module docstring precedent.
    chart = tui.TrendChart(mr, _AUGUST_RECORDS)
    chart.render_chart(now=datetime(2026, 8, 10, 12, 0).astimezone())
    assert "10/08 · 50%" in chart.plt.build()


def test_render_chart_does_not_annotate_a_bucket_thats_already_fully_elapsed():
    # Same fixture, `now` moved past the last bucket's end - the common
    # case, and what every other test in this file relies on implicitly
    # by never passing `now` (falling back to the real, always-later
    # clock, since every fixture here uses a fixed January 2026 date).
    chart = tui.TrendChart(mr, _AUGUST_RECORDS)
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())
    raw = chart.plt.build()
    assert "10/08" in raw
    assert "%" not in raw
