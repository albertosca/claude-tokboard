import json

import metrics_core as mr


def write_transcript(path, entries):
    """entries: list of (timestamp, message_id_or_None, usage_dict)."""
    with open(path, "w") as f:
        for timestamp, message_id, usage in entries:
            message = {"usage": usage}
            if message_id is not None:
                message["id"] = message_id
            f.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": timestamp,
                        "message": message,
                    }
                )
                + "\n"
            )


def usage(input_tokens=0, output_tokens=0, cache_read=0, cache_creation=0):
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }


def record(session_id, timestamp_end, input_tokens, output_tokens, cache_read=0, cache_creation=0):
    return {
        "session_id": session_id,
        "timestamp_end": timestamp_end,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
    }


# --- scan_transcript / build_prefix_sums / as_of ---------------------------


def test_scan_transcript_flags_a_repeated_message_id_in_file_order(tmp_path):
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=100)),  # replay
            ("2026-08-01T10:00:02", "msg-2", usage(input_tokens=50)),
        ],
    )
    timeline = mr.scan_transcript(str(path))
    assert [is_dup for _, is_dup, _ in timeline] == [False, True, False]


def test_as_of_returns_deduped_and_raw_cumulative_totals_at_a_moment(tmp_path):
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=100)),  # replay
            ("2026-08-01T10:00:02", "msg-2", usage(input_tokens=50)),
        ],
    )
    stamps, deduped, raw = mr.build_prefix_sums(mr.scan_transcript(str(path)))
    deduped_now, raw_now = mr.as_of(stamps, deduped, raw, "2026-08-01T10:00:01")
    assert deduped_now == [100, 0, 0, 0]
    assert raw_now == [200, 0, 0, 0]
    deduped_end, raw_end = mr.as_of(stamps, deduped, raw, None)
    assert deduped_end == [150, 0, 0, 0]
    assert raw_end == [250, 0, 0, 0]


# --- plan_corrections --------------------------------------------------


def test_plan_corrections_fixes_a_session_stored_as_the_raw_sum(tmp_path):
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=100)),  # replay
        ],
    )
    records = [record("s1", "2026-08-01T10:00:01", 200, 0)]  # stored = raw
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {0: [100, 0, 0, 0]}
    assert stats["corrected"] == 1
    assert factors == [2.0]


def test_plan_corrections_leaves_an_already_correct_row_alone(tmp_path):
    path = tmp_path / "s1.jsonl"
    write_transcript(path, [("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100))])
    records = [record("s1", "2026-08-01T10:00:00", 100, 0)]  # stored = deduped
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: already correct"] == 1


def test_plan_corrections_skips_a_session_with_no_transcript():
    records = [record("ghost", "2026-08-01T10:00:00", 999, 0)]
    plan, stats, factors = mr.plan_corrections(records, {}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: no transcript on disk"] == 1


def test_plan_corrections_skips_the_whole_session_when_any_row_spans_the_cutoff(tmp_path):
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=100)),
        ],
    )
    records = [
        record("s1", "2026-08-01T10:00:01", 200, 0),  # correctable on its own
        record("s1", "2026-08-14T00:00:00", 200, 0),  # past the cutoff
    ]
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session spans the cutoff"] == 2


def test_plan_corrections_refuses_a_row_that_would_increase_a_field(tmp_path):
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=100)),  # replay
        ],
    )
    # deduped_now=[100,0,0,0], raw_now=[200,0,0,0]. stored's TOTAL (200)
    # matches raw_now's total exactly (drift 0, so the "neither formula"
    # check does not refuse it first) but its PER-FIELD split does not:
    # input_tokens is stored as 90, below the deduped truth of 100 -
    # correcting would raise that one field, which the row-level "only
    # decrease" invariant forbids.
    records = [record("s1", "2026-08-01T10:00:01", 90, 110)]
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session where a correction would increase a field"] == 1


def test_plan_corrections_accepts_a_row_within_tolerance_of_the_raw_sum(tmp_path):
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=1000)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=1000)),  # replay
        ],
    )
    # raw_now is 2000; 1990 is 0.5% off, inside NEITHER_TOLERANCE (2%).
    records = [record("s1", "2026-08-01T10:00:01", 1990, 0)]
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {0: [1000, 0, 0, 0]}
    assert stats["corrected"] == 1


def test_plan_corrections_refuses_the_whole_session_when_one_row_matches_neither_formula(tmp_path):
    # Two sessions worth of rows would each be individually decidable in
    # isolation - row 0 is a clean raw-sum correction (mirrors
    # test_plan_corrections_fixes_a_session_stored_as_the_raw_sum above),
    # row 1 matches neither formula. The all-or-nothing-per-session
    # invariant means the refusal on row 1 must also veto row 0: plan must
    # be empty for BOTH rows, and the refusal reason must count both, not
    # just the row that triggered it. Single-row tests elsewhere cannot
    # prove this propagation - a lone refused row disqualifying "the
    # session" is indistinguishable from it disqualifying only itself.
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=100)),  # replay
            ("2026-08-01T10:00:02", "msg-2", usage(input_tokens=1000)),
        ],
    )
    records = [
        record("s1", "2026-08-01T10:00:01", 200, 0),  # correctable on its own: stored = raw
        record(
            "s1", "2026-08-01T10:00:02", 1800, 0
        ),  # matches neither formula (deduped=1100, raw=1200)
    ]
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session has a row matching neither formula"] == 2


def test_plan_corrections_refuses_a_row_matching_neither_formula(tmp_path):
    path = tmp_path / "s1.jsonl"
    write_transcript(path, [("2026-08-01T10:00:00", "msg-1", usage(input_tokens=1000))])
    # 1500 is 50% off the raw sum (1000) - well outside tolerance.
    records = [record("s1", "2026-08-01T10:00:00", 1500, 0)]
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session has a row matching neither formula"] == 1


# --- find_nonmonotonic_sessions -----------------------------------------


def test_find_nonmonotonic_sessions_flags_a_decreasing_series():
    records = [
        dict(record("s1", "x", 100, 0), duration_seconds=10),
        dict(record("s1", "x", 50, 0), duration_seconds=20),  # dropped
    ]
    assert mr.find_nonmonotonic_sessions(records) == {"s1"}


def test_find_nonmonotonic_sessions_ignores_a_non_decreasing_series():
    records = [
        dict(record("s1", "x", 100, 0), duration_seconds=10),
        dict(record("s1", "x", 150, 0), duration_seconds=20),
    ]
    assert mr.find_nonmonotonic_sessions(records) == set()


# --- apply_plan / verify_fields -----------------------------------------


def test_apply_plan_only_touches_planned_indexes_and_only_token_fields():
    records = [
        record("s1", "x", 200, 0),
        {
            "session_id": "s2",
            "extra_field": "keep-me",
            "input_tokens": 10,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    ]
    corrected = mr.apply_plan(records, {0: [100, 0, 0, 0]})
    assert corrected[0]["input_tokens"] == 100
    assert corrected[1] == records[1]  # untouched, same dict contents


def test_verify_fields_passes_a_token_only_decrease():
    before = [record("s1", "x", 200, 0)]
    after = [record("s1", "x", 100, 0)]
    assert mr.verify_fields(before, after) == []


def test_verify_fields_catches_a_token_increase():
    before = [record("s1", "x", 100, 0)]
    after = [record("s1", "x", 200, 0)]
    problems = mr.verify_fields(before, after)
    assert len(problems) == 1
    assert "input_tokens increased" in problems[0]


def test_verify_fields_catches_a_non_token_field_change():
    before = [record("s1", "x", 100, 0)]
    after = [dict(record("s1", "x", 100, 0), session_id="s1-tampered")]
    problems = mr.verify_fields(before, after)
    assert len(problems) == 1
    assert "session_id changed" in problems[0]


# --- backfill state file ------------------------------------------------


def test_load_backfill_state_returns_none_when_no_file_exists(tmp_path):
    assert mr.load_backfill_state(str(tmp_path / "missing.json")) is None


def test_write_then_load_backfill_state_round_trips(tmp_path):
    path = str(tmp_path / "state.json")
    written = mr.write_backfill_state("2026-08-19", "2026-08-19T12:00:00", 3411, path=path)
    loaded = mr.load_backfill_state(path)
    assert loaded == written
    assert loaded["cutoff"] == "2026-08-19"
    assert loaded["corrected_rows"] == 3411


def test_load_backfill_state_returns_none_on_corrupt_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    assert mr.load_backfill_state(str(path)) is None


def test_describe_backfill_state_when_never_applied():
    text = mr.describe_backfill_state(None)
    assert "2026-08-13" in text
    assert "não" in text  # Portuguese: not yet corrected


def test_describe_backfill_state_when_applied():
    state = {"cutoff": "2026-08-19", "applied_at": "2026-08-19T12:00:00", "corrected_rows": 3411}
    text = mr.describe_backfill_state(state)
    assert "2026-08-19" in text
