import hashlib
import json
from pathlib import Path
from typing import Any

import metrics_core as mr


def write_transcript(path: Path, entries: list[tuple[str, str | None, dict[str, int]]]) -> None:
    """entries: list of (timestamp, message_id_or_None, usage_dict)."""
    with path.open("w") as f:
        for timestamp, message_id, usage in entries:
            message: dict[str, Any] = {"usage": usage}
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


def usage(
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_creation,
    }


def record(
    session_id: str,
    timestamp_end: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "timestamp_end": timestamp_end,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_creation,
    }


# --- sha256_bytes ---------------------------------------------------------


def test_sha256_bytes_really_is_sha256_and_not_merely_some_digest():
    # The digest this returns is printed by the backfill script as the
    # operator's record of exactly which bytes were read and which were
    # written before a destructive correction. Nothing in the suite used
    # to constrain WHICH hash produced it: swapping sha256 for sha1 left
    # the whole suite green. The one property that must not be assumed is
    # the algorithm, so it is pinned two independent ways, and neither
    # calls the function under test to build its own expectation - a
    # comparison of the function against itself passes under sha1, sha512
    # or a constant.
    #
    # First, published SHA-256 test vectors (FIPS 180-4 / RFC 6234), which
    # hold even if hashlib itself were aliased.
    assert (
        mr.sha256_bytes(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert (
        mr.sha256_bytes(b"abc")
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    # Second, an independently computed digest over a realistic payload,
    # so the property is pinned for arbitrary input and not only for the
    # two constants above.
    payload = b'{"session_id": "s1", "input_tokens": 100}\n'
    assert mr.sha256_bytes(payload) == hashlib.sha256(payload).hexdigest()


# --- index_transcripts ---------------------------------------------------


def test_index_transcripts_a_size_tie_does_not_replace_the_first_file_seen(tmp_path):
    projects_dir = tmp_path / "projects"
    slug_a = projects_dir / "proj-a"
    slug_b = projects_dir / "proj-b"
    slug_a.mkdir(parents=True)
    slug_b.mkdir(parents=True)
    (slug_a / "s1.jsonl").write_text("x" * 10)
    (slug_b / "s1.jsonl").write_text("y" * 10)  # same size, same session id

    # Path.glob's iteration order is a filesystem detail, not something this
    # test should assume - so it is discovered independently rather than
    # guessed. Whichever file glob visits FIRST is the one a correct `>`
    # comparison must keep: the same-size second file must lose the tie.
    first_visited = str(next(Path(projects_dir).glob("*/*.jsonl")))

    index = mr.index_transcripts(str(projects_dir))
    assert index["s1"] == first_visited


# --- scan_transcript / build_prefix_sums / as_of ---------------------------


def test_scan_transcript_skips_blank_malformed_and_non_assistant_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    with path.open("w") as f:
        f.write("\n")  # blank line
        f.write("not json at all\n")  # malformed JSON
        f.write(json.dumps({"type": "user", "timestamp": "x", "message": {}}) + "\n")
        f.write(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-08-01T10:00:00",
                    "message": {"id": "msg-1", "usage": usage(input_tokens=5)},
                }
            )
            + "\n"
        )
    timeline = mr.scan_transcript(str(path))
    assert timeline == [("2026-08-01T10:00:00", False, (5, 0, 0, 0))]


def test_scan_transcript_never_flags_entries_with_no_message_id_as_duplicates(tmp_path):
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", None, usage(input_tokens=10)),
            ("2026-08-01T10:00:01", None, usage(input_tokens=10)),
        ],
    )
    timeline = mr.scan_transcript(str(path))
    assert [is_dup for _, is_dup, _ in timeline] == [False, False]


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
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: already correct"] == 1


def test_plan_corrections_skips_a_session_with_no_transcript():
    records = [record("ghost", "2026-08-01T10:00:00", 999, 0)]
    plan, stats, _factors = mr.plan_corrections(records, {}, cutoff="2026-08-13")
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
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session spans the cutoff"] == 2


def test_plan_corrections_treats_a_row_exactly_on_the_cutoff_as_spanning_it(tmp_path):
    # DEFAULT_CUTOFF's own docstring says rows written "on or after" the
    # cutoff are left alone - so a row whose timestamp_end equals the
    # cutoff exactly must disqualify the session too, not just rows
    # strictly past it.
    path = tmp_path / "s1.jsonl"
    write_transcript(path, [("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100))])
    records = [record("s1", "2026-08-13", 100, 0)]  # timestamp_end == cutoff, exactly
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session spans the cutoff"] == 1


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
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
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
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
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
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session has a row matching neither formula"] == 2


def test_plan_corrections_refuses_a_row_matching_neither_formula(tmp_path):
    path = tmp_path / "s1.jsonl"
    write_transcript(path, [("2026-08-01T10:00:00", "msg-1", usage(input_tokens=1000))])
    # 1500 is 50% off the raw sum (1000) - well outside tolerance.
    records = [record("s1", "2026-08-01T10:00:00", 1500, 0)]
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session has a row matching neither formula"] == 1


def test_plan_corrections_skips_the_factor_when_the_correction_is_to_zero(tmp_path):
    # A replayed turn keeps its ORIGINAL timestamp (see scan_transcript's
    # docstring), so file order and timestamp order can disagree: here
    # msg-1 appears first in the FILE at the LATER timestamp (not a
    # duplicate yet) and second in the file at the EARLIER timestamp (now
    # flagged a duplicate). Sorted by timestamp for the prefix sums, the
    # earlier moment is therefore a duplicate and contributes nothing to
    # the deduped total - so the deduped-as-of-that-moment total is zero,
    # while the raw hook still logged 100. That makes the correction
    # target itself zero, without any refusal: exactly the `if after:`
    # guard's reason to exist.
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:05", "msg-1", usage(input_tokens=100)),  # later ts, first in file
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),  # earlier ts, replay
        ],
    )
    records = [record("s1", "2026-08-01T10:00:00", 100, 0)]  # stored = raw-as-of the earlier moment
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {0: [0, 0, 0, 0]}
    assert stats["corrected"] == 1
    assert factors == []


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


# --- daily_totals ---------------------------------------------------------


def test_daily_totals_skips_a_delta_with_no_timestamp(tmp_path):
    records = [
        {  # no timestamp_end and no timestamp_start: the delta this expands
            # to carries no timestamp at all and must be dropped, not crash.
            "session_id": "s-no-ts",
            "duration_seconds": 0,
            "input_tokens": 999,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        {
            "session_id": "s-ok",
            "duration_seconds": 0,
            "timestamp_end": "2026-08-01T10:00:00",
            "input_tokens": 7,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    ]
    result = mr.daily_totals(records, days=3)
    assert result == [("2026-08-01", 7)]  # the 999 never shows up anywhere


def test_daily_totals_returns_exactly_the_last_n_days_when_days_truncates():
    # The `days` slice is the whole point of the parameter, and neither of
    # the neighbouring tests can see its width: one passes days=3 against
    # data that yields a SINGLE day (where [-3:] and [-4:] are the same
    # list) and the other passes days=None and never reaches the slice.
    # Five distinct days against days=3 is the smallest shape where an
    # off-by-one in either direction changes the answer - [-2:] drops
    # 08-03, [-4:] keeps 08-02.
    records = [
        {
            "session_id": f"s{n}",  # one snapshot each, so each delta is its own total
            "duration_seconds": 0,
            "timestamp_end": f"2026-08-0{n}T10:00:00",
            "input_tokens": n,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        for n in range(1, 6)
    ]

    assert mr.daily_totals(records, days=3) == [
        ("2026-08-03", 3),
        ("2026-08-04", 4),
        ("2026-08-05", 5),
    ]


def test_daily_totals_keeps_every_day_when_days_is_falsy():
    records = [
        {
            "session_id": "s1",
            "duration_seconds": 0,
            "timestamp_end": "2026-08-01T10:00:00",
            "input_tokens": 3,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        {
            "session_id": "s1",
            "duration_seconds": 10,
            "timestamp_end": "2026-08-02T10:00:00",
            "input_tokens": 4,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    ]
    result = mr.daily_totals(records, days=None)
    # both days present, unsliced - the second snapshot's delta is 4-3=1.
    assert result == [("2026-08-01", 3), ("2026-08-02", 1)]


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


def test_verify_fields_flags_a_record_count_change():
    before = [record("s1", "x", 100, 0)]
    after: list[dict[str, Any]] = []
    problems = mr.verify_fields(before, after)
    assert problems == ["record count changed: 1 -> 0"]


def test_verify_fields_flags_a_key_set_change():
    # Distinct from the value-change case above: here the SET of keys
    # itself differs (an extra field was added), which must be caught
    # before ever comparing individual values.
    before = [record("s1", "x", 100, 0)]
    after = [dict(record("s1", "x", 100, 0), extra_field="unexpected")]
    problems = mr.verify_fields(before, after)
    assert problems == ["line 1: key set changed"]


def test_verify_fields_flags_a_key_order_change_with_the_same_key_set():
    # Distinct from the key-SET case above: the two dicts share the exact
    # same keys, only their insertion order differs.
    before = [{"session_id": "s1", "input_tokens": 100, "output_tokens": 0}]
    after = [{"output_tokens": 0, "session_id": "s1", "input_tokens": 100}]
    problems = mr.verify_fields(before, after)
    assert problems == ["line 1: key order changed"]


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
