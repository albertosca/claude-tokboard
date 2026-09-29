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
  `compute_pane_projects(self.metrics_core_module, self.chart_records)` - the SAME
  `self.chart_records` that `aggregate_by_day(self.chart_records)` (a few
  lines above) used to produce `days`. `compute_pane_projects` puts every
  record's `color_group_key(project_path)` into `totals` unconditionally
  (never raises, never excludes - confirmed by reading `color_group_key`,
  which returns `project_path` unchanged, including None, whenever it is
  falsy or has no worktree marker). So any record that contributed a day
  bucket to `days` necessarily contributed at least one key to `totals`,
  which means `top_keys` (`ordered[:PANE_MAXIMUM_PROJECTS]`) cannot be empty
  whenever `days` is non-trivial. Proven empirically: two records with
  `project_path: None` on two different days still produce
  `top_keys == [None]`, not `[]`.

No pragma is added for any of the three - this is disclosure for the
controller to rule on, per the phase's Global Constraints, not a unilateral
call to exclude them.
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
import metrics_core.paths as paths_module  # noqa: E402
from test_tui_state import isolate_tui_from_the_real_filesystem, seeded_tui_home  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual_plotext import PlotextPlot  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def _write_sessions(home: Path, records: list[dict[str, Any]]) -> None:
    import json

    metrics_directory = home / ".claude" / "metrics"
    metrics_directory.mkdir(parents=True, exist_ok=True)
    with (metrics_directory / "sessions.jsonl").open("w") as sessions_file:
        for record in records:
            sessions_file.write(json.dumps(record) + "\n")
    (metrics_directory / "subagents.jsonl").touch()


def _write_subagents(home: Path, records: list[dict[str, Any]]) -> None:
    import json

    metrics_directory = home / ".claude" / "metrics"
    metrics_directory.mkdir(parents=True, exist_ok=True)
    (metrics_directory / "sessions.jsonl").touch()
    with (metrics_directory / "subagents.jsonl").open("w") as subagents_file:
        for record in records:
            subagents_file.write(json.dumps(record) + "\n")


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
    """`records[-arguments.limit:]` is a slice, not a filter - it must keep the
    LAST n sessions in load order, not merely n of them (a bug that picked
    the first n, or an arbitrary n, would pass a test that only checks the
    count)."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    _write_sessions(
        tmp_path,
        [_session(f"s{index}", day=index + 1) for index in range(1, 5)],  # s1..s4, day 2..5
    )
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0", "--limit", "2"])
    base = tui.load_base_data(arguments, metrics_core_module=mr)
    assert [record["session_id"] for record in base["records"]] == ["s3", "s4"], (
        "--limit 2 must keep the two MOST RECENT (last, in load order) "
        "sessions, not the first two or any other pair"
    )


def test_no_limit_keeps_every_session(monkeypatch, tmp_path):
    """The default (no --limit) branch, for contrast - unbounded stays
    unbounded, per the comment above the flag's definition."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    _write_sessions(tmp_path, [_session(f"s{index}", day=index + 1) for index in range(1, 5)])
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(arguments, metrics_core_module=mr)
    assert [record["session_id"] for record in base["records"]] == ["s1", "s2", "s3", "s4"]


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

    def spy(record: dict[str, Any], cache: Any, budget: Any, *, lang: str) -> Any:
        calls.append(record)
        return original(record, cache, budget, lang=lang)

    monkeypatch.setattr(mr, "get_agent_label", spy)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(arguments, metrics_core_module=mr)  # must not raise
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
    labelless = [subagent for subagent in grouped if subagent.get("agent_id") is None]
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

    def spy(record: dict[str, Any], cache: Any, budget: Any, *, lang: str) -> Any:
        calls.append(record)
        return original(record, cache, budget, lang=lang)

    monkeypatch.setattr(mr, "get_task_label", spy)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(arguments, metrics_core_module=mr)  # must not raise
    assert len(calls) == 1, (
        f"expected exactly one label lookup (for the real session_id) - "
        f"got {len(calls)}; the session_id-less record must never reach "
        "unique_sessions"
    )
    assert calls[0]["session_id"] == "s1"
    # Both records still flow through to the record list itself - only
    # task-label resolution skips the malformed one.
    assert len(base["records"]) == 2
    malformed_out = next(record for record in base["records"] if record.get("session_id") is None)
    assert malformed_out["task_label"] is None


# ---------------------------------------------------------------------------
# the resolved language reaches the label call (Task 5, Fix round 2)
# ---------------------------------------------------------------------------


def test_the_resolved_language_reaches_get_task_label(monkeypatch, tmp_path):
    """load_base_data resolves `lang` once (from config.json) and must
    thread that SAME value into every get_task_label call - not a
    hardcoded default that happens to read the same in the one language
    the rest of the suite pins. Proven with a spy that records the `lang`
    kwarg each call actually received, on an English-configured HOME with
    a real transcript (so the call reaches past the cache-hit/internal-name
    shortcuts) and a budget > 0 (so it also reaches compress_to_label, the
    one exercised in the reviewer-named repro)."""
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    (tmp_path / ".claude" / "metrics" / "config.json").write_text(json.dumps({"language": "en"}))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "fix the login bug"}}) + "\n"
    )
    session = _session("s1", day=1)
    session["transcript_path"] = str(transcript)
    _write_sessions(tmp_path, [session])

    captured_langs: list[str] = []
    original = mr.get_task_label

    def spy(record: dict[str, Any], cache: Any, budget: Any, *, lang: str) -> Any:
        captured_langs.append(lang)
        # subprocess.run is already patched to hard-fail by
        # isolate_tui_from_the_real_filesystem - _run_label_prompt's own
        # try/except swallows that as "no result", so delegating to the
        # real function is safe here and still negative-caches instead of
        # raising. The spy has already captured `lang` by the time that
        # happens, which is the only thing this test needs.
        return original(record, cache, budget, lang=lang)

    monkeypatch.setattr(mr, "get_task_label", spy)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "5"])
    tui.load_base_data(arguments, metrics_core_module=mr)
    assert captured_langs == ["en"], (
        "get_task_label must be called with the resolved language ('en' "
        f"here), not a hardcoded default - got {captured_langs}"
    )


def test_load_base_data_reads_the_config_file_exactly_once(monkeypatch, tmp_path):
    """load_base_data used to also call mr.load_project_roots() on top of
    mr.load_config() - two reads of the same file per launch (backlog
    #11). A spy on load_config, patched in BOTH places it can be reached
    from (the direct `mr.load_config()` call load_base_data makes itself,
    and the bare-name call inside paths.py's own load_project_roots, which
    resolves through paths_module's globals rather than metrics_core's -
    patching only `mr` would leave a call routed through
    load_project_roots invisible here) proves the fix: read once, derive
    project_roots from that SAME dict via mr.project_roots_from.
    """
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    session = _session("s1", day=1)
    _write_sessions(tmp_path, [session])

    calls: list[str | None] = []
    original = mr.load_config

    def spy(path: str | None = None) -> Any:
        calls.append(path)
        return original(path)

    monkeypatch.setattr(mr, "load_config", spy)
    monkeypatch.setattr(paths_module, "load_config", spy)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    tui.load_base_data(arguments, metrics_core_module=mr)
    assert len(calls) == 1, f"load_config must be read exactly once per run - got {calls}"


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
    chart = tui.TrendChart(mr, single_day_records, lang="pt")
    chart.render_chart()
    assert not hasattr(chart, "pane_series_keys"), (
        "render_chart kept going past the len(days) < 2 guard - "
        "pane_series_keys is only ever set inside the code that guard "
        "exists to skip"
    )


def test_render_chart_returns_early_with_zero_days_of_data():
    """The other side of the same boundary - no records at all, not just
    one day's worth."""
    chart = tui.TrendChart(mr, [], lang="pt")
    chart.render_chart()
    assert not hasattr(chart, "pane_series_keys")


def test_render_chart_proceeds_with_two_days_of_data():
    """Negative control for the two tests above: two distinct days is
    enough to clear the guard and reach the series-building code - without
    this, "pane_series_keys never gets set" could just as easily mean the
    guard is permanently (mis)tripped rather than correctly gated on the
    day count."""
    two_day_records = [_session("a", day=1), _session("b", day=2)]
    chart = tui.TrendChart(mr, two_day_records, lang="pt")
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
    chart = tui.TrendChart(mr, _AUGUST_RECORDS, lang="pt")
    chart.render_chart(now=datetime(2026, 8, 10, 12, 0).astimezone())
    assert mr.t("partial_bar.day", "pt", pct=50) in chart.plt.build()


def test_render_chart_does_not_annotate_a_bucket_thats_already_fully_elapsed():
    # Same fixture, `now` moved past the last bucket's end - the common
    # case, and what every other test in this file relies on implicitly
    # by never passing `now` (falling back to the real, always-later
    # clock, since every fixture here uses a fixed January 2026 date).
    chart = tui.TrendChart(mr, _AUGUST_RECORDS, lang="pt")
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())
    raw = chart.plt.build()
    assert "10/08" in raw
    assert "%" not in raw


def test_the_in_progress_marker_goes_away_when_its_bucket_ends_on_the_same_chart():
    # plotext's clear_data keeps labels, so a chart redrawn after the last
    # bucket ended - left open past midnight - would keep saying the day
    # is half over unless the redraw clears it.
    chart = tui.TrendChart(mr, _AUGUST_RECORDS, lang="pt")
    chart.render_chart(now=datetime(2026, 8, 10, 12, 0).astimezone())
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())
    assert "%" not in chart.plt.build()


@pytest.mark.parametrize(
    ("canvas_width", "expected"),
    [
        (None, "última barra: 37% da semana até agora"),
        (37, "última barra: 37% da semana até agora"),
        (36, "última barra: 37%"),
        (17, "última barra: 37%"),
        (16, "37%"),
        (3, "37%"),
        (2, None),
    ],
)
def test_partial_marker_is_the_longest_form_that_fits(
    canvas_width: int | None, expected: str | None
) -> None:
    assert tui.partial_marker(mr, "week", 37, canvas_width, lang="pt") == expected


# Every width the backlog item named, plus the two ends of the old failure:
# with the percentage on the last tick label, plotext's left-to-right
# thinning dropped it at every width under 163 columns for 30 days of data.
MARKER_WIDTHS = [60, 80, 100, 120, 150, 200]


def _shown_with_now(
    records: list[dict[str, Any]], widths: list[int], now: datetime, lang: str, mode: str = "pane"
) -> list[str]:
    """The mounted chart's text at each width, with the clock pinned - real
    time would leave these August buckets long finished, and a marker test
    whose bucket is not in progress proves nothing."""

    class ChartOnly(App[None]):
        def compose(self) -> ComposeResult:
            yield tui.TrendChart(mr, records, mode=mode, lang=lang)

    async def drive() -> list[str]:
        shown = []
        async with ChartOnly().run_test(size=(widths[0], 24)) as pilot:
            await pilot.pause()
            chart = pilot.app.query_one(tui.TrendChart)
            chart.render_chart(now=now)
            for width in widths:
                await pilot.resize_terminal(width, 24)
                await pilot.pause()
                shown.append(str(chart.render()))
        return shown

    return asyncio.run(drive())


_THIRTY_AUGUST_DAYS = [
    _august_session(f"s{day}", day=day, tokens=100 * day) for day in range(1, 31)
]


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_in_progress_marker_is_visible_at_every_common_width(lang: str) -> None:
    now = datetime(2026, 8, 30, 12, 0).astimezone()
    marker = mr.t("partial_bar.day", lang, pct=50)
    shown = _shown_with_now(_THIRTY_AUGUST_DAYS, MARKER_WIDTHS, now, lang)
    for width, text in zip(MARKER_WIDTHS, shown, strict=True):
        assert marker in text, (lang, width, text.splitlines()[-2:])


def test_a_narrow_chart_gets_the_short_marker_and_plotext_keeps_it() -> None:
    # The fit rule in partial_marker is only worth something if plotext
    # agrees with it: at these widths the full sentence would be dropped.
    now = datetime(2026, 8, 30, 12, 0).astimezone()
    short = mr.t("partial_bar.short", "pt", pct=50)
    for width, text in zip(
        (24, 30, 40), _shown_with_now(_THIRTY_AUGUST_DAYS, [24, 30, 40], now, "pt"), strict=True
    ):
        assert short in text, (width, text.splitlines()[-2:])


@pytest.mark.parametrize("mode", ["tokens", "model"])
def test_the_in_progress_marker_is_drawn_in_every_chart_mode(mode: str) -> None:
    now = datetime(2026, 8, 30, 12, 0).astimezone()
    (text,) = _shown_with_now(_THIRTY_AUGUST_DAYS, [80], now, "en", mode=mode)
    assert mr.t("partial_bar.day", "en", pct=50) in text


def test_no_marker_at_any_width_once_the_last_bucket_is_complete() -> None:
    now = datetime(2026, 8, 31, 0, 0).astimezone()
    for text in _shown_with_now(_THIRTY_AUGUST_DAYS, MARKER_WIDTHS, now, "en"):
        assert "%" not in text and "last bar" not in text


# ---------------------------------------------------------------------------
# render_chart's third mode: tokens by model
# ---------------------------------------------------------------------------


def _model_session(
    session_id: str, day: int, by_model: dict[str, Any] | None, total: int = 100
) -> dict[str, Any]:
    record = _august_session(session_id, day=day, tokens=total)
    if by_model is not None:
        record["tokens_by_model"] = by_model
    return record


def _m(**fields: int) -> dict[str, int]:
    base = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    base.update(fields)
    return base


def test_toggle_mode_cycles_through_three_modes_and_returns():
    chart = tui.TrendChart(
        mr, [_august_session("a", day=1), _august_session("b", day=5)], lang="pt"
    )
    assert chart.mode == "pane"
    chart.toggle_mode()
    assert chart.mode == "tokens"
    chart.toggle_mode()
    assert chart.mode == "model"
    chart.toggle_mode()
    assert chart.mode == "pane", "the third press must come back to the start"


def test_model_mode_labels_each_band_with_a_shortened_model_name():
    records = [
        _model_session("a", 1, {"claude-opus-5": _m(input_tokens=100)}),
        _model_session("b", 5, {"claude-haiku-4-5-20251001": _m(input_tokens=100)}),
    ]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.mode = "model"
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())

    text = chart.plt.build()
    assert "opus-5" in text
    assert "haiku-4-5" in text, "the release-date suffix belongs in storage, not in a legend"
    assert "claude-opus-5" not in text, "the vendor prefix is the same word on every entry"


def test_model_mode_shows_the_unattributed_remainder_as_its_own_band():
    # Most of the real history has no map at all. The mode must show that
    # rather than quietly drawing a shorter bar than the other two modes do.
    records = [
        _model_session("a", 1, {"claude-opus-5": _m(input_tokens=40)}, total=100),
        _model_session("b", 5, None, total=100),
    ]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.mode = "model"
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())

    assert "sem modelo" in chart.plt.build()


def _ytick_labels(text: str) -> list[str]:
    """The y-axis tick labels plotext printed, which encode the scale the
    bars were drawn against (render_chart sets them from maximum_total). The
    legend text shares a line with the top tick, so this takes only what
    sits left of the axis, and strips the colour codes first."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return [line.split("\u2524")[0].strip() for line in plain.splitlines() if "\u2524" in line]


def test_every_mode_draws_a_day_against_the_same_scale():
    # The three modes slice one quantity - a day's tokens - so a day must
    # be the same height in all of them. If the by-model mode only summed
    # the attributed part, its bars would silently shrink and the chart
    # would understate every day that predates the per-model map.
    records = [
        _model_session("a", 1, {"claude-opus-5": _m(input_tokens=40)}, total=100),
        _model_session("b", 5, None, total=250),
        _model_session(
            "c",
            10,
            {"claude-opus-5": _m(input_tokens=30), "claude-sonnet-5": _m(output_tokens=5)},
            total=35,
        ),
    ]
    now = datetime(2026, 8, 12, 0, 0).astimezone()
    scales = {}
    for mode in tui.TrendChart.MODES:
        chart = tui.TrendChart(mr, records, lang="pt")
        chart.mode = mode
        chart.render_chart(now=now)
        scales[mode] = _ytick_labels(chart.plt.build())

    assert scales["model"] == scales["pane"]
    assert scales["model"] == scales["tokens"]
    assert scales["model"][0] == mr.format_tokens(250), (
        "the tallest day is the one with NO attribution at all - reading its "
        "height off the ticks is what proves the remainder is carrying it"
    )


def test_a_day_with_no_attribution_at_all_is_drawn_entirely_as_unattributed():
    # The degenerate case the backfill left behind for most of the history:
    # no map anywhere in the window. The chart must still draw those days at
    # full height under the "sem modelo" band rather than render nothing.
    records = [
        _model_session("a", 1, None, total=100),
        _model_session("b", 5, None, total=60),
    ]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.mode = "model"
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())

    text = chart.plt.build()
    assert "sem modelo" in re.sub(r"\x1b\[[0-9;]*m", "", text)
    assert _ytick_labels(text)[0] == mr.format_tokens(100)


# ---------------------------------------------------------------------------
# The zero row: every bar reaches it, and only in a series it actually holds
# ---------------------------------------------------------------------------
#
# plotext's stacked_bar leaves a bar floating one row above the zero line
# whenever its FIRST series is 0 there. The old workaround set that series
# to 1 token - and plotext draws any nonzero value as at least one full
# row, so the bar's base came out in the colour, under the legend name, of
# a series with no tokens on that day. In the model mode that painted the
# top model under every day that predates the per-model map (2026-09-22).

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _zero_row_colours(raw: str) -> list[str]:
    """The colour of each bar segment on the chart's zero row, left to right."""
    zero = next(line for line in raw.splitlines() if re.match(r"^\s*0┤", _ANSI.sub("", line)))
    return re.findall(r"\x1b\[([0-9;]*)m█+", zero)


def _legend_colour(raw: str, label: str) -> str:
    """The colour plotext's own legend draws `label` in."""
    match = re.search(r"\x1b\[([0-9;]*)m█+\x1b\[[0-9;]*m[^\n]*?" + re.escape(label), raw)
    assert match, f"no legend entry for {label!r}"
    return match.group(1)


def test_a_bar_reaches_zero_in_a_series_it_actually_has():
    records = [
        _model_session("a", 1, None, total=100),
        _model_session("b", 5, {"claude-opus-5": _m(input_tokens=100)}, total=100),
    ]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.mode = "model"
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())
    raw = chart.plt.build()

    unattributed = _legend_colour(raw, "sem modelo")
    opus = _legend_colour(raw, "opus-5")
    assert unattributed != opus, "the two series must be told apart for this to mean anything"
    # Day 1 has no model data at all, so its base is "sem modelo" - never
    # opus-5, which it does not have. Day 2 is all opus-5.
    assert _zero_row_colours(raw) == [unattributed, opus]


def test_every_bar_with_data_still_reaches_the_zero_line():
    # The float the old workaround existed for must stay fixed: here the
    # FIRST series (the top project) is 0 on day 1. Two bars, two segments
    # on the zero row - one would mean a bar is floating.
    records = [
        _august_session("a", day=1, tokens=50) | {"project_path": "/projects/small"},
        _august_session("b", day=5, tokens=400) | {"project_path": "/projects/big"},
        _august_session("c", day=5, tokens=50) | {"project_path": "/projects/small"},
    ]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())
    assert len(_zero_row_colours(chart.plt.build())) == 2


def _row_segments(line: str) -> list[tuple[str, int]]:
    """Each run of bar cells on one rendered row, as (colour code, width)."""
    return [(code, len(run)) for code, run in re.findall(r"\x1b\[([0-9;]*)m(█+)", line)]


def test_a_series_that_skips_days_keeps_its_bars_the_width_of_everyone_elses():
    # Four days. "big" is there every day; "small" only on the first and
    # the last. plotext sizes a bar by the average spacing of the x values
    # passed in ONE call, and a series is drawn without its zero days - so
    # without compensation "small" gets bars three times as wide, painting
    # its colour across days 2 and 3, where it has nothing. Found in review
    # (2026-09-23): every render test before this one had two buckets,
    # where a skipped day cannot happen.
    def day(sid: str, day_number: int, tokens: int, project: str) -> dict[str, Any]:
        return _august_session(sid, day=day_number, tokens=tokens) | {"project_path": project}

    records = [day(f"b{day_number}", day_number, 100, "/p/big") for day_number in (1, 2, 3, 4)] + [
        day("s1", 1, 40, "/p/small"),
        day("s4", 4, 40, "/p/small"),
    ]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.plt.plotsize(80, 16)
    chart.render_chart(now=datetime(2026, 8, 30).astimezone())
    raw = chart.plt.build()
    big, small = _legend_colour(raw, "big"), _legend_colour(raw, "small")

    zero = _row_segments(
        next(line for line in raw.splitlines() if re.match(r"^\s*0┤", _ANSI.sub("", line)))
    )
    # Big is the bottom of every day's stack: four bars on the zero row, all
    # big, and small nowhere on it.
    assert [code for code, _ in zero] == [big] * 4
    bar_width = zero[0][1]
    assert {width for _, width in zero} == {bar_width}

    # Above big's top, only small's two bars - each exactly one bar wide.
    above_big = [
        _row_segments(line)
        for line in raw.splitlines()
        if small in line and big not in line and "small" not in _ANSI.sub("", line)
    ]
    assert above_big, "the chart has no row above big's bars to check"
    for segments in above_big:
        assert segments == [(small, bar_width), (small, bar_width)], segments


def test_a_window_with_no_tokens_at_all_still_draws_its_axis():
    # Nothing to stack means no bar is drawn - and plotext with no data at
    # all renders an empty frame with no dates, which reads as broken rather
    # than as "nothing happened". Found in review (2026-09-23).
    records = [_august_session("a", day=1, tokens=0), _august_session("b", day=5, tokens=0)]
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())
    text = _ANSI.sub("", chart.plt.build())
    assert "01/08" in text
    assert "05/08" in text


def _tick_row_and_marker(text: str) -> list[str]:
    """The x tick labels - the row right under the axis - followed by the
    in-progress marker's words when the chart has one, on the row below."""
    lines = text.splitlines()
    axis = next(index for index, line in enumerate(lines) if "└" in line)
    return " ".join(lines[axis + 1 :]).split()


def _axis_labels(records: list[dict[str, Any]], now: datetime) -> list[str]:
    chart = tui.TrendChart(mr, records, lang="pt")
    chart.plt.plotsize(80, 12)
    chart.render_chart(now=now)
    return _tick_row_and_marker(_ANSI.sub("", chart.plt.build()))


def test_a_zero_token_bucket_at_the_end_keeps_its_date_and_its_in_progress_label():
    # plotext takes the x range from the bars actually drawn, and every
    # layer skips its zero days - so a bucket with records but no tokens,
    # at the window's edge, fell outside the plot. When that bucket was
    # today's, the " · 25%" in-progress label went with it. Found by the
    # 2026-09-23 re-review. The percentage has since moved to a line of
    # its own under the ticks (2026-09-25); the date must still be there.
    records = [_august_session(f"s{day}", day=day, tokens=100) for day in (1, 5, 10)]
    records.append(_august_session("s12", day=12, tokens=0))
    labels = _axis_labels(records, datetime(2026, 8, 12, 6, 0).astimezone())
    assert labels[-8:] == ["12/08", *mr.t("partial_bar.day", "pt", pct=25).split()], labels


def test_a_zero_token_bucket_at_the_start_keeps_its_date():
    records = [_august_session("s1", day=1, tokens=0)]
    records += [_august_session(f"s{day}", day=day, tokens=100) for day in (5, 10, 12)]
    labels = _axis_labels(records, datetime(2026, 8, 30).astimezone())
    assert labels[0] == "01/08", labels


def _mounted_axis_labels(records: list[dict[str, Any]], now: datetime) -> list[str]:
    """_axis_labels through the production width path: the chart mounted
    at 100 columns, sized and redrawn by its own render(). At 40 columns
    and under plotext dropped the long " · 25%" tick label for lack of
    room (measured 2026-09-23), which would have pinned nothing."""

    class ChartOnly(App[None]):
        def compose(self) -> ComposeResult:
            yield tui.TrendChart(mr, records, lang="pt")

    async def drive() -> list[str]:
        async with ChartOnly().run_test(size=(100, 12)) as pilot:
            await pilot.pause()
            chart = pilot.app.query_one(tui.TrendChart)
            chart.render_chart(now=now)
            await pilot.pause()
            return _tick_row_and_marker(str(chart.render()))

    return asyncio.run(drive())


def test_a_mounted_chart_keeps_a_zero_token_bucket_at_either_edge():
    end = [_august_session(f"s{day}", day=day, tokens=100) for day in (1, 5, 10)]
    end.append(_august_session("s12", day=12, tokens=0))
    labels = _mounted_axis_labels(end, datetime(2026, 8, 12, 6, 0).astimezone())
    assert labels[-8:] == ["12/08", *mr.t("partial_bar.day", "pt", pct=25).split()], labels

    start = [_august_session("s1", day=1, tokens=0)]
    start += [_august_session(f"s{day}", day=day, tokens=100) for day in (5, 10, 12)]
    labels = _mounted_axis_labels(start, datetime(2026, 8, 30).astimezone())
    assert labels[0] == "01/08", labels


# ---------------------------------------------------------------------------
# Neighbouring days never share a screen cell
# ---------------------------------------------------------------------------
#
# plotext rounds each bar edge to a character cell, and at 0.8 of a day wide
# the gap between two days is 0.2 of a day: under one cell as soon as a day
# is narrower than five cells. Day k's right edge and day k+1's left edge
# then land in the SAME cell, which shows whichever layer plotext drew last -
# a colour the other day may not have at all. Measured in review
# (2026-09-23): two series on alternating days, 30 days, 120 columns.


def _alternating_days(days: int) -> list[dict[str, Any]]:
    return [
        _august_session(f"s{day}", day=day, tokens=100)
        | {"project_path": "/p/odd" if day % 2 else "/p/even"}
        for day in range(1, days + 1)
    ]


def _assert_each_day_is_its_own_bar(raw: str, days: int) -> None:
    odd, even = _legend_colour(raw, "odd"), _legend_colour(raw, "even")
    zero = next(line for line in raw.splitlines() if re.match(r"^\s*0┤", _ANSI.sub("", line)))
    # Each unbroken run of bar cells, as the list of colours inside it. A
    # colour change alone does not separate two days - a shared cell moves
    # the boundary without adding one - so a day is only its own bar when
    # a blank cell separates it from the next, and only then is every one
    # of its cells in its own colour.
    runs: list[set[str]] = []
    colour, previous = "", ""
    for escape, char in re.findall(r"\x1b\[([0-9;]*)m|(.)", zero):
        if escape:
            colour = escape
        elif char == "█":
            if previous != "█":
                runs.append(set())
            runs[-1].add(colour)
        previous = char or previous
    assert runs == [{odd} if day % 2 else {even} for day in range(1, days + 1)], runs


def _built_at(records: list[dict[str, Any]], sizes: list[tuple[int, int]]) -> list[str]:
    """What the chart builds, mounted in an app at each terminal size in
    turn - through the widget's own render, which is where plotext learns
    the size it draws at."""

    class ChartOnly(App[None]):
        def compose(self) -> ComposeResult:
            yield tui.TrendChart(mr, records, lang="pt")

    async def drive() -> list[str]:
        built = []
        async with ChartOnly().run_test(size=sizes[0]) as pilot:
            for size in sizes:
                await pilot.resize_terminal(*size)
                await pilot.pause()
                built.append(pilot.app.query_one(tui.TrendChart).plt.build())
        return built

    return asyncio.run(drive())


def test_neighbouring_days_never_share_a_cell():
    # 120 columns is the reviewer's case; at 121 a canvas width estimated
    # two cells too wide (forgetting the frame lines) is already enough to
    # make neighbours touch.
    for raw in _built_at(_alternating_days(30), [(120, 20), (121, 20)]):
        _assert_each_day_is_its_own_bar(raw, 30)


def test_the_bars_are_resized_with_the_terminal():
    # Drawn for 200 columns, then shown in 120: bars sized for the wider
    # canvas would share cells in the narrower one.
    _, raw = _built_at(_alternating_days(30), [(200, 20), (120, 20)])
    _assert_each_day_is_its_own_bar(raw, 30)


def _day_spans(plot_width: int, days: int) -> list[set[int]]:
    """The columns each day's bar covers, found by drawing that day ALONE.
    A cell two days share cannot be seen in a render of both - it simply
    shows one of them - so each day is drawn by itself, with the same
    invisible layer holding the axis still, and the spans are compared."""
    spans = []
    for target_day in range(days):
        plt = PlotextPlot().plt
        plt.plotsize(plot_width, 8)
        heights = [[1 if day == target_day else 0 for day in range(days)]]
        # One-character y labels, so the canvas is the plot less the label
        # and the two frame lines: plot_width - 3.
        tui.draw_stacked_bars(
            plt, ["d"] * days, heights, ["x"], ["green"], canvas_width=plot_width - 3
        )
        plt.yticks([0, 1], ["0", "1"])
        lines = _ANSI.sub("", plt.build()).splitlines()
        # plotext caps a plot at the terminal's width, and the canvas width
        # passed above assumes it drew at plot_width.
        assert {len(line) for line in lines} == {plot_width}, "plot drawn narrower than asked"
        zero = next(line for line in lines if line.lstrip().startswith("0┤"))
        spans.append({column for column, char in enumerate(zero) if char == "█"})
    return spans


def test_bars_keep_a_blank_cell_between_them_when_there_is_room():
    spans = _day_spans(40, 10)
    assert all(
        max(first_span) + 1 < min(second_span) for first_span, second_span in pairwise(spans)
    ), spans
    # And the gap is made by narrowing a bar only as far as it needs: a
    # one-cell bar also leaves gaps, and is what a width of 0 draws
    # (2026-09-23 mutation sweep: _bar_width_on returning 0 passed here).
    assert all(len(span) >= 2 for span in spans), spans


def test_a_roomy_canvas_keeps_plotexts_own_bar_width():
    # Room to spare is not a reason to change how the chart looks: a bar
    # stays 0.8 of the distance from one day to the next.
    spans = _day_spans(80, 4)
    # plotext fills both edge cells, so 0.8 of a 20-cell step is 17 cells;
    # the widest bar that still leaves a gap here would be 19.
    step = min(spans[1]) - min(spans[0])
    # Both bounds: with only the upper one, bars of a single cell - what a
    # width of 0 draws - passed (2026-09-23 mutation sweep, _bar_width_on
    # and _bar_width_for each returning 0).
    assert all(round(0.8 * step) - 1 <= len(span) <= round(0.8 * step) + 1 for span in spans), (
        step,
        spans,
    )


def test_bars_are_one_cell_wide_and_apart_when_there_is_no_room_for_a_gap():
    # 15 cells for 10 days: too few for a blank cell between every two,
    # enough for each day to have a cell of its own.
    spans = _day_spans(18, 10)
    assert all(len(span) == 1 for span in spans), spans
    assert all(max(first_span) < min(second_span) for first_span, second_span in pairwise(spans)), (
        spans
    )


def test_bars_are_one_cell_wide_when_days_outnumber_cells():
    # 9 cells for 10 days: sharing is unavoidable, so no bar spreads over
    # more than the one cell it cannot help landing in.
    assert all(len(span) == 1 for span in _day_spans(12, 10))


def test_no_room_for_a_canvas_draws_bars_of_no_width():
    # A widget no wider than its y labels and frame leaves a canvas of 0
    # cells or fewer. The width solved for it once divided by zero at -1
    # and came out as plotext's full 0.8 below that (2026-09-23 review).
    assert [tui._bar_width_on(canvas, 30) for canvas in (-3, -2, -1, 0)] == [0.0] * 4


def _shown_at(
    records: list[dict[str, Any]], sizes: list[tuple[int, int]], lang: str = "pt"
) -> list[str]:
    """The text the mounted chart renders at each terminal size in turn."""

    class ChartOnly(App[None]):
        def compose(self) -> ComposeResult:
            yield tui.TrendChart(mr, records, lang=lang)

    async def drive() -> list[str]:
        shown = []
        async with ChartOnly().run_test(size=sizes[0]) as pilot:
            for size in sizes:
                await pilot.resize_terminal(*size)
                await pilot.pause()
                shown.append(str(pilot.app.query_one(tui.TrendChart).render()))
        return shown

    return asyncio.run(drive())


TOO_NARROW = mr.t("chart_too_narrow", "pt")


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_chart_survives_every_narrow_width(lang: str) -> None:
    # A tmux pane can be dragged through any width; a crash in render kills
    # the whole dashboard. Measured before the fix: 4 columns (canvas -1)
    # raised ZeroDivisionError here, 5 (canvas 0) a ValueError in plotext.
    # These days peak at 100 tokens, so the widest y label is "100": the
    # canvas is the width less 5, and plotext needs at least one cell.
    #
    # Parametrized over both languages (2026-09-23 review): every render
    # here had only ever been checked in Portuguese, so an English-only
    # regression in chart_too_narrow's own text - a typo, an accidental
    # empty string, MESSAGES losing the "en" key - could ship unnoticed.
    too_narrow = mr.t("chart_too_narrow", lang)
    shown = _shown_at(_alternating_days(30), [(width, 20) for width in range(1, 21)], lang=lang)
    for width, text in enumerate(shown, start=1):
        if width - 5 < 1:
            assert text == too_narrow, (lang, width)
        else:
            assert too_narrow not in text and "┤" in text, (lang, width, text)


def test_the_chart_survives_a_short_terminal():
    # Height is not what plotext trips on (measured down to one row), but a
    # pane is dragged in both directions.
    shown = _shown_at(_alternating_days(30), [(40, height) for height in (1, 2, 3, 4, 5)])
    assert TOO_NARROW not in shown


def test_the_whole_dashboard_survives_every_narrow_width(monkeypatch, tmp_path):
    # The same sweep through the real app, on the chart's pane, in each of
    # the three chart modes - the full app at 7 and 8 columns is where the
    # 2026-09-23 review found the crash.
    #
    # Not every width, though: mounting the whole dashboard costs about
    # 0.2s per resize, and 20 widths x 3 modes made this the slowest test
    # in the suite (13-15s). The chart-only sweep above already renders
    # every width from 1 to 20; what only the full app adds is the chart
    # squeezed beside the real legends and frame, and that can only go
    # wrong where the message switches to the chart. So per mode: width 1,
    # the switch found by bisection over 1..20 - observed, as before, from
    # where the message stops, never a hard-coded column - two widths
    # either side of it, one wide terminal, and the three short heights.
    # Trimmed 2026-09-23 from ~14s to ~6.5s. Revert-checked: loosening
    # _draw_chart's `canvas_width < 1` guard to `< 0` still turns it red,
    # with plotext's ValueError on the zero-cell canvas one column below
    # the switch - the column this sample exists to keep.
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    heights = [(80, height) for height in (1, 3, 5)]

    async def drive() -> dict[str, list[tuple[tuple[int, int], int, str]]]:
        shown: dict[str, list[tuple[tuple[int, int], int, str]]] = {}
        app = tui.MetricsTUIApp(
            mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
        )
        async with app.run_test(size=(80, 40)) as pilot:
            seen: dict[tuple[int, int], tuple[int, str]] = {}

            async def narrow_at(size: tuple[int, int]) -> bool:
                if size not in seen:
                    await pilot.resize_terminal(*size)
                    await pilot.pause()
                    chart = pilot.app.query_one(tui.TrendChart)
                    seen[size] = (chart.size.width, str(chart.render()))
                return seen[size][1] == TOO_NARROW

            await pilot.press("g")
            for _ in tui.TrendChart.MODES:
                seen.clear()
                # Narrowest width that draws the chart, assuming the message
                # at 1 and the chart at 20; if either end is wrong the
                # assertions below still see it, because both ends are kept.
                narrow, wide = 1, 20
                await narrow_at((narrow, 40))
                await narrow_at((wide, 40))
                while wide - narrow > 1:
                    middle = (narrow + wide) // 2
                    if await narrow_at((middle, 40)):
                        narrow = middle
                    else:
                        wide = middle
                for width in [*range(max(1, wide - 2), wide + 3), 120]:
                    await narrow_at((width, 40))
                for size in heights:
                    await narrow_at(size)
                widths = sorted(size for size in seen if size not in heights)
                mode = pilot.app.query_one(tui.TrendChart).mode
                shown[mode] = [(size, *seen[size]) for size in widths + heights]
                await pilot.resize_terminal(80, 40)
                await pilot.press("t")
        return shown

    shown = asyncio.run(drive())
    assert set(shown) == set(tui.TrendChart.MODES)
    for mode, renders in shown.items():
        too_narrow = [text == TOO_NARROW for _, _, text in renders]
        # The message on the narrowest widths, the chart on every one after
        # them, and nothing in between. Where it switches depends on the
        # widest y label (measured: after 8 columns for this fixture).
        assert too_narrow[0] and not too_narrow[-1], mode
        switch = too_narrow.index(False)
        assert not any(too_narrow[switch:]), (mode, too_narrow)
        # Past the switch it is plotext's output. Squeezed under the
        # legends, which wrap tall at these widths, the chart can be a
        # single row of dates, so no axis is asserted here - the chart-only
        # sweep above checks the axis.
        assert all(text.strip() for _, _, text in renders[switch:]), mode


# ---------------------------------------------------------------------------
# A model's colour does not depend on the period (backlog 2026-09-24 #6)
# ---------------------------------------------------------------------------


def _dated_model_session(
    session_id: str, days_ago: int, by_model: dict[str, dict[str, int]]
) -> dict[str, Any]:
    start = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
    start -= timedelta(days=days_ago)
    total = sum(sum(bucket.values()) for bucket in by_model.values())
    return {
        "session_id": session_id,
        "project_path": "/projects/p",
        "timestamp_start": start.isoformat(),
        "timestamp_end": (start + timedelta(hours=1)).isoformat(),
        "input_tokens": total,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "tokens_by_model": by_model,
    }


OPUS, SONNET = "claude-opus-4-8", "claude-sonnet-4-5"
# Two ids that hash to the same one of seven slots. Only the older sessions
# have "model-0", so a tie settled per period would hand "model-1" the slot
# both want in the last fifteen days and a different one over everything.
EARLY, LATE = "model-0", "model-1"

# Opus leads the whole history; sonnet leads the last fifteen days. Ranked,
# opus drew in the first colour under "tudo" and the second under "15-dias".
_OLD = {OPUS: _m(input_tokens=2000), SONNET: _m(input_tokens=10)}
_NEW = {OPUS: _m(input_tokens=10), SONNET: _m(input_tokens=1000)}
_RANKINGS_DIFFER = [
    _dated_model_session("old-1", 20, _OLD | {EARLY: _m(input_tokens=5), LATE: _m(input_tokens=5)}),
    _dated_model_session("old-2", 19, _OLD),
    _dated_model_session("new-1", 3, _NEW | {LATE: _m(input_tokens=5)}),
    _dated_model_session("new-2", 2, _NEW),
]


def _top_model(data: dict[str, Any]) -> str:
    totals = mr.compute_model_totals(data["daily_chart_records"])[0]
    return max(totals, key=lambda model: totals[model])


def _model_colours(data: dict[str, Any]) -> dict[str, str]:
    chart = tui.TrendChart(
        mr, data["daily_chart_records"], mode="model", lang="pt", model_slots=data["model_slots"]
    )
    chart.render_chart()
    raw = chart.plt.build()
    return {
        model: _legend_colour(raw, mr.model_display_label(model)) for model in (OPUS, SONNET, LATE)
    }


def test_a_model_keeps_its_colour_in_periods_that_rank_it_differently(monkeypatch, tmp_path):
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    _write_sessions(tmp_path, _RANKINGS_DIFFER)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(arguments, metrics_core_module=mr)
    everything = tui.apply_period(base, "tudo", mr)
    recent = tui.apply_period(base, "15-dias", mr)
    # The premise: the two periods really do rank the models differently.
    assert (_top_model(everything), _top_model(recent)) == (OPUS, SONNET)
    colours = _model_colours(everything)
    assert colours == _model_colours(recent)
    assert len(set(colours.values())) == 3


def test_the_dashboard_hands_its_chart_the_slots_settled_over_the_whole_history(
    monkeypatch, tmp_path
):
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    _write_sessions(tmp_path, _RANKINGS_DIFFER)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])

    async def drive() -> list[dict[str, int | None] | None]:
        app = tui.MetricsTUIApp(
            mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
        )
        seen = []
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            seen.append(app.query_one(tui.TrendChart).model_slots)
            await pilot.press("p")
            await pilot.pause()
            seen.append(app.query_one(tui.TrendChart).model_slots)
        return seen

    expected = mr.assign_model_slots([OPUS, SONNET, EARLY, LATE], len(tui.PANE_COLORS))
    assert asyncio.run(drive()) == [expected, expected]


def test_a_model_past_the_last_colour_is_drawn_in_the_other_colour():
    # Eight models, seven colours: the one left without a slot shares the
    # "other" colour with the unattributed band rather than reusing a
    # model's colour.
    models = [f"model-{index}" for index in range(8)]
    records = [
        _model_session(f"s{index}", 1 + index % 2, {model: _m(input_tokens=100)}, total=200)
        for index, model in enumerate(models)
    ]
    chart = tui.TrendChart(mr, records, mode="model", lang="pt")
    chart.render_chart(now=datetime(2026, 8, 12, 0, 0).astimezone())
    raw = chart.plt.build()
    colours = [_legend_colour(raw, model) for model in models]
    assert len(set(colours)) == 8
    assert _legend_colour(raw, mr.t("no_model", "pt")) in colours
