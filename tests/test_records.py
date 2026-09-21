"""Loading, deduping, normalizing, filtering, and delta-expanding raw
session/subagent records - direct coverage of lib/metrics_core/records.py.

filter_sessions_by_window, filter_deltas_by_window, and _parse_moment are
covered by tests/test_period_filters.py and are NOT duplicated here.
"""

from datetime import datetime, timedelta, tzinfo
from typing import Any

import metrics_core as mr
import metrics_core.records as records_module


def snapshot(duration_seconds: int, timestamp_end: str, **tokens: int) -> dict[str, Any]:
    r: dict[str, Any] = {"duration_seconds": duration_seconds, "timestamp_end": timestamp_end}
    r.update(tokens)
    return r


def _bucket(**tokens: int) -> dict[str, int]:
    """One model's inner mapping inside a record's tokens_by_model, with
    every one of the four fields present - the shape parse_transcript
    writes, where an unused field is an explicit zero rather than absent."""
    b = dict.fromkeys(
        ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"), 0
    )
    b.update(tokens)
    return b


# --- load_records --------------------------------------------------------


def test_load_records_missing_file_returns_empty_list():
    assert mr.load_records("/nonexistent/path/does-not-exist.jsonl") == []


def test_load_records_parses_each_valid_line_in_order(tmp_path):
    path = tmp_path / "sessions.jsonl"
    path.write_text('{"session_id": "a"}\n{"session_id": "b"}\n')
    assert mr.load_records(str(path)) == [{"session_id": "a"}, {"session_id": "b"}]


def test_load_records_skips_a_malformed_line_but_keeps_its_neighbors(tmp_path):
    path = tmp_path / "sessions.jsonl"
    path.write_text('{"session_id": "a"}\nnot json at all\n{"session_id": "b"}\n')
    assert mr.load_records(str(path)) == [{"session_id": "a"}, {"session_id": "b"}]


def test_load_records_skips_blank_lines(tmp_path):
    path = tmp_path / "sessions.jsonl"
    path.write_text('{"session_id": "a"}\n\n{"session_id": "b"}\n')
    assert mr.load_records(str(path)) == [{"session_id": "a"}, {"session_id": "b"}]


# --- dedupe_by_key / dedupe_sessions / dedupe_subagents -------------------


def test_dedupe_by_key_keeps_the_snapshot_with_largest_duration_seconds():
    records = [
        {"session_id": "a", "duration_seconds": 5, "marker": "first"},
        {"session_id": "a", "duration_seconds": 10, "marker": "second"},
    ]
    result = mr.dedupe_by_key(records, "session_id")
    assert len(result) == 1
    assert result[0]["marker"] == "second"


def test_dedupe_by_key_keeps_a_record_missing_the_key_field():
    records: list[dict[str, Any]] = [
        {"session_id": "a", "duration_seconds": 5},
        {"no_session_here": True},
    ]
    result = mr.dedupe_by_key(records, "session_id")
    assert len(result) == 2
    assert {"no_session_here": True} in result


def test_dedupe_by_key_does_not_replace_the_best_with_a_smaller_duration():
    records = [
        {"session_id": "a", "duration_seconds": 10, "marker": "keep"},
        {"session_id": "a", "duration_seconds": 1, "marker": "should-not-win"},
    ]
    result = mr.dedupe_by_key(records, "session_id")
    assert len(result) == 1
    assert result[0]["marker"] == "keep"


def test_dedupe_by_key_preserves_relative_order_of_distinct_keys():
    records = [
        {"session_id": "b", "duration_seconds": 1},
        {"session_id": "a", "duration_seconds": 1},
        {"session_id": "b", "duration_seconds": 5},
    ]
    result = mr.dedupe_by_key(records, "session_id")
    assert [r["session_id"] for r in result] == ["b", "a"]


def test_dedupe_sessions_dedupes_by_session_id():
    records = [
        {"session_id": "a", "duration_seconds": 1, "marker": "loser"},
        {"session_id": "a", "duration_seconds": 9, "marker": "winner"},
    ]
    result = mr.dedupe_sessions(records)
    assert len(result) == 1
    assert result[0]["marker"] == "winner"


def test_dedupe_subagents_dedupes_by_agent_id():
    records = [
        {"agent_id": "x", "duration_seconds": 1, "marker": "loser"},
        {"agent_id": "x", "duration_seconds": 9, "marker": "winner"},
    ]
    result = mr.dedupe_subagents(records)
    assert len(result) == 1
    assert result[0]["marker"] == "winner"


# --- is_ephemeral_path -----------------------------------------------------


def test_is_ephemeral_path_matches_tmp():
    assert mr.is_ephemeral_path("/tmp/foo")


def test_is_ephemeral_path_matches_private_tmp():
    assert mr.is_ephemeral_path("/private/tmp/foo")


def test_is_ephemeral_path_matches_scratchpad():
    assert mr.is_ephemeral_path("/Users/me/scratchpad/whatever")


def test_is_ephemeral_path_false_for_an_ordinary_project_path():
    assert not mr.is_ephemeral_path("/Users/me/Programming/proj")


# --- normalize_project_paths ------------------------------------------------


def test_normalize_project_paths_collapses_a_lone_child_into_its_parent():
    # /proj/hooks is the ONLY child recorded under /proj (1 <= 2), so it
    # gets rewritten to the canonical parent path.
    records = [
        {"project_path": "/Users/me/Programming/proj"},
        {"project_path": "/Users/me/Programming/proj/hooks"},
    ]
    result = mr.normalize_project_paths(records)
    assert [r["project_path"] for r in result] == [
        "/Users/me/Programming/proj",
        "/Users/me/Programming/proj",
    ]


def test_normalize_project_paths_collapses_at_the_two_child_boundary():
    # Exactly 2 children recorded under /parent - still <= the 2-child cutoff,
    # so both still collapse into their parent.
    records = [
        {"project_path": "/Users/me/Programming/parent"},
        {"project_path": "/Users/me/Programming/parent/a"},
        {"project_path": "/Users/me/Programming/parent/b"},
    ]
    result = mr.normalize_project_paths(records)
    assert [r["project_path"] for r in result] == [
        "/Users/me/Programming/parent",
        "/Users/me/Programming/parent",
        "/Users/me/Programming/parent",
    ]


def test_normalize_project_paths_leaves_a_path_with_no_canonical_match_untouched():
    records = [{"project_path": "/Users/me/Programming/standalone"}]
    result = mr.normalize_project_paths(records)
    assert result[0]["project_path"] == "/Users/me/Programming/standalone"


def test_normalize_project_paths_does_not_collapse_a_container_with_many_children():
    # 3 children recorded under /Users/me/Programming - past the 2-child
    # cutoff, so /Users/me/Programming is a real container, not a project
    # occasionally logged from a subdirectory - nothing collapses.
    records = [{"project_path": "/Users/me/Programming"}] + [
        {"project_path": f"/Users/me/Programming/proj{i}"} for i in range(3)
    ]
    result = mr.normalize_project_paths(records)
    assert [r["project_path"] for r in result] == [
        "/Users/me/Programming",
        "/Users/me/Programming/proj0",
        "/Users/me/Programming/proj1",
        "/Users/me/Programming/proj2",
    ]


def test_normalize_project_paths_folds_ephemeral_paths_to_none():
    records = [{"project_path": "/tmp/whatever"}]
    result = mr.normalize_project_paths(records)
    assert result[0]["project_path"] is None


def test_normalize_project_paths_excludes_worktree_paths_from_collapsing():
    # /Users/me/proj/.claude/worktrees/feature-x's immediate parent
    # (/Users/me/proj/.claude/worktrees) is included here AS ITS OWN
    # RECORD - unlike the original version of this test, where that
    # parent path never appeared in the dataset at all and so could never
    # have been a collapse target with or without the marker. With the
    # parent present, it has exactly one known child (feature-x), which
    # is <= max_children_to_collapse - a genuine collapse candidate: if
    # the marker didn't exclude worktree paths, feature-x would collapse
    # into it and both entries would read back as the bare
    # ".../worktrees" path. The marker keeps both untouched, preserving
    # the full "parent / worktree-name" path for color_group_key to merge
    # later.
    records = [
        {"project_path": "/Users/me/proj"},
        {"project_path": "/Users/me/proj/.claude/worktrees"},
        {"project_path": "/Users/me/proj/.claude/worktrees/feature-x"},
    ]
    result = mr.normalize_project_paths(records)
    assert [r["project_path"] for r in result] == [
        "/Users/me/proj",
        "/Users/me/proj/.claude/worktrees",
        "/Users/me/proj/.claude/worktrees/feature-x",
    ]


# --- filter_records ----------------------------------------------------------


def test_filter_records_by_tag_alone():
    records = [{"tag": "t1", "marker": "keep"}, {"tag": "t2", "marker": "drop"}]
    assert mr.filter_records(records, tag="t1") == [{"tag": "t1", "marker": "keep"}]


def test_filter_records_by_project_alone():
    records = [{"project_path": "/a", "marker": "keep"}, {"project_path": "/b", "marker": "drop"}]
    assert mr.filter_records(records, project="/a") == [{"project_path": "/a", "marker": "keep"}]


def test_filter_records_by_tag_and_project_together():
    records = [
        {"tag": "t1", "project_path": "/a", "marker": "keep"},
        {"tag": "t1", "project_path": "/b", "marker": "drop-wrong-project"},
        {"tag": "t2", "project_path": "/a", "marker": "drop-wrong-tag"},
    ]
    assert mr.filter_records(records, tag="t1", project="/a") == [
        {"tag": "t1", "project_path": "/a", "marker": "keep"}
    ]


def test_filter_records_with_neither_filter_returns_everything():
    records = [{"tag": "t1"}, {"tag": "t2"}]
    assert mr.filter_records(records) == records


# --- filter_by_days ------------------------------------------------------


class _FrozenNow(datetime):
    """A datetime subclass whose now() is pinned to a fixed instant, so
    filter_by_days' `datetime.now() - timedelta(days=days)` cutoff becomes
    deterministic instead of racing wall-clock time between test setup and
    the function's own call - required to pin the exact boundary instant."""

    fixed: "_FrozenNow"

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> "_FrozenNow":
        return cls.fixed if tz is None else cls.fixed.astimezone(tz)


_FrozenNow.fixed = _FrozenNow(2026, 8, 20, 12, 0, 0).astimezone()


def test_filter_by_days_none_returns_everything_unchanged():
    records = [{"timestamp_start": "2020-01-01T00:00:00Z"}]
    assert mr.filter_by_days(records, None) == records


def test_filter_by_days_keeps_the_cutoff_and_drops_the_first_moment_past_it(monkeypatch):
    monkeypatch.setattr(records_module, "datetime", _FrozenNow)
    cutoff = _FrozenNow.fixed - timedelta(days=3)
    at_cutoff = {"timestamp_start": cutoff.isoformat(), "marker": "at_cutoff"}
    just_past_cutoff = {
        "timestamp_start": (cutoff - timedelta(seconds=1)).isoformat(),
        "marker": "just_past_cutoff",
    }
    result = mr.filter_by_days([at_cutoff, just_past_cutoff], 3)
    assert result == [at_cutoff]


def test_filter_by_days_skips_a_record_with_no_timestamp_start():
    records = [{"marker": "no_timestamp_at_all"}]
    assert mr.filter_by_days(records, 3) == []


def test_filter_by_days_skips_a_record_with_an_unparseable_timestamp_start():
    records = [{"timestamp_start": "not-a-real-timestamp"}]
    assert mr.filter_by_days(records, 3) == []


# --- record_token_total / compute_token_type_totals ------------------------


def test_record_token_total_sums_all_four_distinct_fields():
    record = {
        "input_tokens": 1,
        "output_tokens": 20,
        "cache_read_tokens": 300,
        "cache_creation_tokens": 4000,
    }
    assert mr.record_token_total(record) == 4321


def test_record_token_total_treats_missing_fields_as_zero():
    assert mr.record_token_total({}) == 0


def test_compute_token_type_totals_sums_each_series_independently():
    records = [
        {
            "input_tokens": 1,
            "output_tokens": 10,
            "cache_read_tokens": 100,
            "cache_creation_tokens": 1000,
        },
        {
            "input_tokens": 2,
            "output_tokens": 20,
            "cache_read_tokens": 200,
            "cache_creation_tokens": 2000,
        },
    ]
    assert mr.compute_token_type_totals(records) == {
        "series-1": 3,
        "series-2": 30,
        "series-3": 300,
        "series-4": 3000,
    }


# --- compute_model_totals ---------------------------------------------------


def _row(
    total_by_field: tuple[int, int, int, int],
    by_model: dict[str, dict[str, int]] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "input_tokens": total_by_field[0],
        "output_tokens": total_by_field[1],
        "cache_read_tokens": total_by_field[2],
        "cache_creation_tokens": total_by_field[3],
    }
    if by_model is not None:
        record["tokens_by_model"] = by_model
    return record


def _bucket_for(**fields: int) -> dict[str, int]:
    base = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    base.update(fields)
    return base


def test_compute_model_totals_splits_each_records_total_across_its_models():
    records = [
        _row(
            (10, 0, 0, 0),
            {"model-a": _bucket_for(input_tokens=6), "model-b": _bucket_for(input_tokens=4)},
        ),
        _row((5, 0, 0, 0), {"model-a": _bucket_for(input_tokens=5)}),
    ]

    by_model, unattributed = mr.compute_model_totals(records)

    assert by_model == {"model-a": 11, "model-b": 4}
    assert unattributed == 0


def test_compute_model_totals_reports_the_shortfall_as_unattributed():
    # The whole point of the third chart mode's grey band: a record whose map
    # accounts for less than its own total is not silently rescaled, it keeps
    # the remainder visible.
    records = [_row((100, 0, 0, 0), {"model-a": _bucket_for(input_tokens=30)})]

    by_model, unattributed = mr.compute_model_totals(records)

    assert by_model == {"model-a": 30}
    assert unattributed == 70


def test_compute_model_totals_counts_a_record_with_no_map_as_entirely_unattributed():
    # 75% of the real history is this case - the transcript is gone and no
    # backfill can ever recover it.
    records = [_row((40, 0, 0, 0)), _row((0, 2, 0, 0))]

    by_model, unattributed = mr.compute_model_totals(records)

    assert by_model == {}
    assert unattributed == 42


def test_compute_model_totals_sums_to_the_same_number_record_token_total_does():
    # The invariant the chart's shared scale rests on: whatever the split,
    # the bands of a day must add up to that day's height in the other two
    # modes. If this ever stops holding, switching tabs rescales the day.
    records = [
        _row((1, 2, 3, 4), {"model-a": _bucket_for(input_tokens=1, output_tokens=2)}),
        _row((10, 0, 5, 0)),
        _row((7, 0, 0, 0), {"model-b": _bucket_for(input_tokens=7)}),
    ]

    by_model, unattributed = mr.compute_model_totals(records)

    assert sum(by_model.values()) + unattributed == sum(mr.record_token_total(r) for r in records)


def test_compute_model_totals_never_reports_a_negative_remainder():
    # A map claiming more than the record's own totals would otherwise push
    # the grey band below zero and invert the stack. The backfill's gate
    # refuses to write such a row, but nothing stops an older one existing.
    records = [_row((10, 0, 0, 0), {"model-a": _bucket_for(input_tokens=999)})]

    by_model, unattributed = mr.compute_model_totals(records)

    assert by_model == {"model-a": 999}
    assert unattributed == 0


# --- compute_quota_totals ---------------------------------------------------


def test_compute_quota_totals_sums_input_and_cache_creation_keeps_output_separate():
    # Per Anthropic's documented API rate-limit rule (input_tokens and
    # cache_creation_input_tokens both count toward ITPM; output_tokens
    # counts toward a separate OTPM limit) - the two numbers must NOT be
    # combined into one.
    totals = {"series-1": 100, "series-2": 20, "series-3": 999_999, "series-4": 5}
    assert mr.compute_quota_totals(totals) == (105, 20)


def test_compute_quota_totals_excludes_cache_read_entirely():
    # cache_read_input_tokens (series-3) does not count toward ITPM for any
    # current model - the exact finding that motivated this function: a day
    # that is 96% cache_read should barely move the quota-relevant number.
    totals = {"series-3": 1_000_000_000}
    assert mr.compute_quota_totals(totals) == (0, 0)


def test_compute_quota_totals_missing_series_default_to_zero():
    assert mr.compute_quota_totals({}) == (0, 0)


# --- expand_records_to_daily_deltas -----------------------------------------


def test_expand_turns_cumulative_snapshots_into_per_snapshot_deltas():
    raw = [
        {
            "session_id": "s1",
            **snapshot(10, "2026-08-01T10:10:00", input_tokens=100, output_tokens=10),
        },
        {
            "session_id": "s1",
            **snapshot(20, "2026-08-01T10:20:00", input_tokens=250, output_tokens=15),
        },
    ]
    result = mr.expand_records_to_daily_deltas(raw)
    assert [r["input_tokens"] for r in result] == [100, 150]
    assert [r["output_tokens"] for r in result] == [10, 5]


def test_expand_dates_each_delta_by_its_own_timestamp_end_not_the_session_start():
    raw = [
        {
            "session_id": "s1",
            "timestamp_start": "2026-08-01T00:00:00",
            **snapshot(5, "2026-08-05T00:00:00", input_tokens=10),
        }
    ]
    result = mr.expand_records_to_daily_deltas(raw)
    assert result[0]["timestamp_start"] == "2026-08-05T00:00:00"


def test_expand_groups_by_agent_id_before_session_id_so_subagents_dont_contaminate():
    # Two subagents dispatched by the SAME parent session_id - if
    # session_id were checked first they'd wrongly be treated as two
    # sequential snapshots of one entity and their deltas would cross.
    raw = [
        {"agent_id": "ag1", "session_id": "shared", **snapshot(5, "t1", input_tokens=50)},
        {"agent_id": "ag2", "session_id": "shared", **snapshot(5, "t2", input_tokens=70)},
    ]
    result = mr.expand_records_to_daily_deltas(raw)
    totals_by_agent = {r["agent_id"]: r["input_tokens"] for r in result}
    assert totals_by_agent == {"ag1": 50, "ag2": 70}


def test_expand_sorts_out_of_order_snapshots_before_differencing():
    raw = [
        {"session_id": "s1", **snapshot(20, "second", input_tokens=250)},
        {"session_id": "s1", **snapshot(10, "first", input_tokens=100)},
    ]
    result = mr.expand_records_to_daily_deltas(raw)
    assert [(r["timestamp_start"], r["input_tokens"]) for r in result] == [
        ("first", 100),
        ("second", 150),
    ]


def test_expand_skips_a_record_with_neither_id_field():
    raw = [{**snapshot(5, "t1", input_tokens=50)}]
    assert mr.expand_records_to_daily_deltas(raw) == []


def test_expand_floors_a_decreasing_token_count_at_zero():
    raw = [
        {"session_id": "s1", **snapshot(5, "t1", input_tokens=100)},
        {"session_id": "s1", **snapshot(10, "t2", input_tokens=50)},
    ]
    result = mr.expand_records_to_daily_deltas(raw)
    assert result[1]["input_tokens"] == 0


def test_expand_differences_tokens_by_model_instead_of_copying_the_cumulative_map():
    # tokens_by_model is a CUMULATIVE snapshot like the four totals, so a
    # verbatim copy would make each delta carry the session's running
    # per-model map: summing across the deltas would then overcount (1.4x
    # on this very fixture) and disagree with the four differenced totals
    # these same records carry, which is what the trend chart reads.
    raw = [
        {
            "session_id": "s1",
            **snapshot(10, "2026-08-01T10:10:00", input_tokens=100),
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=100)},
        },
        {
            "session_id": "s1",
            **snapshot(20, "2026-08-02T10:20:00", input_tokens=250),
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=250)},
        },
    ]

    result = mr.expand_records_to_daily_deltas(raw)

    assert [r["tokens_by_model"]["model-alpha"]["input_tokens"] for r in result] == [100, 150]
    # The reconciliation the four totals already satisfy, now for the map:
    # summing the deltas returns the session's final cumulative value.
    assert sum(r["tokens_by_model"]["model-alpha"]["input_tokens"] for r in result) == 250
    assert sum(r["input_tokens"] for r in result) == 250


def test_expand_differences_a_model_that_first_appears_in_a_later_snapshot_against_zero():
    # A session picks up a second model partway through. Its first delta is
    # its whole cumulative value (previous is zero), not a KeyError and not
    # zero.
    raw = [
        {
            "session_id": "s1",
            **snapshot(10, "t1", input_tokens=100),
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=100)},
        },
        {
            "session_id": "s1",
            **snapshot(20, "t2", input_tokens=340),
            "tokens_by_model": {
                "model-alpha": _bucket(input_tokens=250),
                "model-beta": _bucket(input_tokens=90),
            },
        },
    ]

    result = mr.expand_records_to_daily_deltas(raw)

    assert result[0]["tokens_by_model"] == {"model-alpha": _bucket(input_tokens=100)}
    assert result[1]["tokens_by_model"] == {
        "model-alpha": _bucket(input_tokens=150),
        "model-beta": _bucket(input_tokens=90),
    }
    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        assert sum(b[field] for r in result for b in r["tokens_by_model"].values()) == sum(
            r[field] for r in result
        ), field


# --- fold_subagent_tokens_into_sessions -------------------------------------


def test_fold_adds_subagent_tokens_onto_the_parent_session():
    sessions = [{"session_id": "s1", "input_tokens": 10}]
    subagents = [
        {"session_id": "s1", "input_tokens": 5},
        {"session_id": "s1", "input_tokens": 3},
    ]
    result = mr.fold_subagent_tokens_into_sessions(sessions, subagents)
    assert result[0]["input_tokens"] == 18


def test_fold_ignores_a_subagent_with_no_session_id():
    sessions = [{"session_id": "s1", "input_tokens": 10}]
    subagents = [{"input_tokens": 999}]
    result = mr.fold_subagent_tokens_into_sessions(sessions, subagents)
    assert result[0]["input_tokens"] == 10


def test_fold_leaves_a_session_with_no_matching_subagent_totals_untouched():
    sessions = [{"session_id": "s1", "input_tokens": 10}]
    subagents = [{"session_id": "other", "input_tokens": 999}]
    result = mr.fold_subagent_tokens_into_sessions(sessions, subagents)
    assert result == [{"session_id": "s1", "input_tokens": 10}]


def test_fold_mutates_the_input_records_in_place_not_a_copy():
    # dashboard_html.py and dashboard_tui.py both call this function and
    # discard the return value - the in-place mutation on the caller's own
    # dicts is the ONLY effect they get (see the function's docstring).
    # Pin both halves of that contract: the ORIGINAL list's dicts reflect
    # the fold, and the returned list is the same objects, not equal
    # copies - a `records = [dict(r) for r in records]` at the top would
    # satisfy every other test in this file while breaking both dashboards
    # silently (the 2026-08-05 under-half-of-real-consumption bug).
    sessions = [{"session_id": "s1", "input_tokens": 10}]
    subagents = [{"session_id": "s1", "input_tokens": 5}]
    result = mr.fold_subagent_tokens_into_sessions(sessions, subagents)
    assert sessions[0]["input_tokens"] == 15
    assert result[0] is sessions[0]


# --- filter_sessions_by_window gap not covered by test_period_filters.py ---
# test_period_filters.py exercises every overlap/window case for this
# function, but never a record whose timestamp_start doesn't parse to a
# moment at all - the one remaining uncovered branch in this module.


def test_filter_sessions_by_window_skips_a_record_with_no_parseable_start():
    window = (
        datetime(2026, 8, 1).astimezone(),
        datetime(2026, 8, 2).astimezone(),
    )
    records = [{"timestamp_start": None, "timestamp_end": "2026-08-01T10:00:00"}]
    assert mr.filter_sessions_by_window(records, window) == []


# --- normalize_project_paths, declared project roots ------------------
#
# The fan-out heuristic above can only roll a path up ONE level, and only
# when its parent looks like a lone project rather than a container. That
# leaves a session logged from `<project>/docs/vendor` naming itself
# "vendor" in the legend, which is what Alberto reported on 2026-09-21
# ("a legenda por vezes ta pegando nomes de subagents... e so pra pegar o
# nome do diretorio base daquele projeto, sempre"). The cwd alone cannot
# say where the project stops - so the containers are DECLARED, and a
# direct child of one is a project no matter how deep the cwd went.


def _p(path: str, session_id: str = "s") -> dict[str, Any]:
    return {"session_id": session_id, "project_path": path}


def test_declared_root_collapses_any_depth_to_the_direct_child():
    records = [
        _p("/home/me/code/my-api/docs/vendor", "a"),
        _p("/home/me/code/blog-site/a/b/c/d", "b"),
    ]
    result = mr.normalize_project_paths(records, project_roots=["/home/me/code"])
    assert [r["project_path"] for r in result] == [
        "/home/me/code/my-api",
        "/home/me/code/blog-site",
    ]


def test_declared_root_beats_the_fan_out_heuristic_that_would_swallow_a_project():
    # Two children is the heuristic's collapse boundary, so without the
    # declared root these two would be rolled INTO the container itself -
    # the exact 2026-07-21 failure its comment records.
    records = [_p("/home/me/code/my-api", "a"), _p("/home/me/code", "b")]
    result = mr.normalize_project_paths(records, project_roots=["/home/me/code"])
    assert [r["project_path"] for r in result] == ["/home/me/code/my-api", "/home/me/code"]


def test_the_container_itself_is_not_turned_into_a_project():
    records = [_p("/home/me/code", "a"), _p("/home/me/code/", "b")]
    result = mr.normalize_project_paths(records, project_roots=["/home/me/code"])
    assert [r["project_path"] for r in result] == ["/home/me/code", "/home/me/code/"]


def test_a_declared_root_keeps_the_worktree_it_contains_distinguishable():
    # Collapsing to the direct child would erase which worktree the work
    # happened in. The label is "project / worktree" precisely so both
    # survive, and the color still groups them together.
    records = [_p("/home/me/code/my-api/.worktrees/feature-x/deep/dir", "a")]
    result = mr.normalize_project_paths(records, project_roots=["/home/me/code"])
    assert result[0]["project_path"] == "/home/me/code/my-api/.worktrees/feature-x"
    assert mr.project_display_label(result[0]["project_path"]) == "my-api / feature-x"
    assert mr.color_group_key(result[0]["project_path"]) == "/home/me/code/my-api"


def test_paths_outside_every_declared_root_still_go_through_the_heuristic():
    records = [_p("/elsewhere/proj/hooks", "a"), _p("/elsewhere/proj", "b")]
    result = mr.normalize_project_paths(records, project_roots=["/home/me/code"])
    assert [r["project_path"] for r in result] == ["/elsewhere/proj", "/elsewhere/proj"]


def test_no_declared_roots_leaves_every_path_exactly_as_before():
    records = [_p("/home/me/code/my-api/docs/vendor", "a")]
    assert mr.normalize_project_paths(records)[0]["project_path"] == (
        "/home/me/code/my-api/docs/vendor"
    )


def test_a_trailing_slash_on_a_declared_root_is_not_a_different_root():
    records = [_p("/home/me/code/my-api/docs", "a")]
    result = mr.normalize_project_paths(records, project_roots=["/home/me/code/"])
    assert result[0]["project_path"] == "/home/me/code/my-api"


def test_the_longest_matching_root_wins_when_roots_nest():
    records = [_p("/home/me/code/client/my-api/docs", "a")]
    result = mr.normalize_project_paths(
        records, project_roots=["/home/me/code", "/home/me/code/client"]
    )
    assert result[0]["project_path"] == "/home/me/code/client/my-api"


def test_a_doubled_slash_in_the_cwd_still_names_the_project():
    records = [_p("/home/me/code//my-api/docs", "a")]
    result = mr.normalize_project_paths(records, project_roots=["/home/me/code"])
    assert result[0]["project_path"] == "/home/me/code/my-api"
