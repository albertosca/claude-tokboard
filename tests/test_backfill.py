import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import metrics_core as mr
import pytest
from state_shapes import (
    SHAPE_NAMES,
    UNREADABLE_STATES,
    restore_permissions,
    write_unreadable_state,
)


def write_transcript(path: Path, entries: list[tuple[str, str | None, dict[str, int]]]) -> None:
    """entries: list of (timestamp, message_id_or_None, usage_dict)."""
    with path.open("w") as transcript_file:
        for timestamp, message_id, usage in entries:
            message: dict[str, Any] = {"usage": usage}
            if message_id is not None:
                message["id"] = message_id
            transcript_file.write(
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


def test_sha256_of_hashes_the_whole_file_with_sha256(tmp_path):
    # Found by the 2026-09-23 whole-body mutation sweep: sha256_of
    # returning "" left the suite green. Both backfill scripts verify a
    # backup by comparing sha256_of(source) with sha256_of(backup), and
    # every test of that comparison stubs sha256_of - so a constant digest
    # made every backup "verified", including a truncated one. Pinned the
    # same two ways as sha256_bytes above, plus a file larger than the
    # 1 MiB read chunk so a digest of only the first chunk fails too.
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    abc = tmp_path / "abc"
    abc.write_bytes(b"abc")
    assert mr.sha256_of(str(empty)) == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert mr.sha256_of(str(abc)) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    big = tmp_path / "big"
    payload = bytes(range(256)) * (5 * 4096 + 7)  # ~5 MiB, not a chunk multiple
    big.write_bytes(payload)
    truncated = tmp_path / "truncated"
    truncated.write_bytes(payload[: 1 << 20])
    assert mr.sha256_of(str(big)) == hashlib.sha256(payload).hexdigest()
    assert mr.sha256_of(str(truncated)) != mr.sha256_of(str(big))


# --- index_transcripts ---------------------------------------------------


def test_index_transcripts_a_size_tie_does_not_replace_the_first_file_seen(tmp_path):
    projects_directory = tmp_path / "projects"
    first_slug = projects_directory / "proj-a"
    second_slug = projects_directory / "proj-b"
    first_slug.mkdir(parents=True)
    second_slug.mkdir(parents=True)
    (first_slug / "s1.jsonl").write_text("x" * 10)
    (second_slug / "s1.jsonl").write_text("y" * 10)  # same size, same session id

    # Path.glob's iteration order is a filesystem detail, not something this
    # test should assume - so it is discovered independently rather than
    # guessed. Whichever file glob visits FIRST is the one a correct `>`
    # comparison must keep: the same-size second file must lose the tie.
    first_visited = str(next(Path(projects_directory).glob("*/*.jsonl")))

    index = mr.index_transcripts(str(projects_directory))
    assert index["s1"] == first_visited


# --- scan_transcript / build_prefix_sums / as_of ---------------------------


def test_scan_transcript_skips_blank_malformed_and_non_assistant_lines(tmp_path):
    path = tmp_path / "t.jsonl"
    with path.open("w") as transcript_file:
        transcript_file.write("\n")  # blank line
        transcript_file.write("not json at all\n")  # malformed JSON
        transcript_file.write(json.dumps({"type": "user", "timestamp": "x", "message": {}}) + "\n")
        transcript_file.write(
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
    assert timeline == [("2026-08-01T10:00:00", False, (5, 0, 0, 0), "unknown", "msg-1")]


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
    assert [entry[1] for entry in timeline] == [False, False]


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
    assert [entry[1] for entry in timeline] == [False, True, False]
    assert [entry[4] for entry in timeline] == ["msg-1", "msg-1", "msg-2"]


def test_as_of_returns_peak_first_and_raw_cumulative_totals_at_a_moment(tmp_path):
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=100)),  # replay
            ("2026-08-01T10:00:02", "msg-2", usage(input_tokens=50)),
        ],
    )
    sums = mr.build_prefix_sums(mr.scan_transcript(str(path)))
    now = mr.as_of(sums, "2026-08-01T10:00:01")
    assert now.peak == [100, 0, 0, 0]
    assert now.first == [100, 0, 0, 0]
    assert now.raw == [200, 0, 0, 0]
    end = mr.as_of(sums, None)
    assert end.peak == [150, 0, 0, 0]
    assert end.first == [150, 0, 0, 0]
    assert end.raw == [250, 0, 0, 0]


def test_as_of_counts_a_streamed_message_at_its_peak_as_of_each_moment(tmp_path):
    # One message streamed as three entries: output grows 3 -> 40 -> 179,
    # input and cache repeat. The first-occurrence formula keeps 3; the
    # peak as of each moment is the largest entry seen up to it.
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "m1", usage(2, 3, 1000, 50)),
            ("2026-08-01T10:00:01", "m1", usage(2, 40, 1000, 50)),
            ("2026-08-01T10:00:02", "m1", usage(2, 179, 1000, 50)),
        ],
    )
    sums = mr.build_prefix_sums(mr.scan_transcript(str(path)))
    middle = mr.as_of(sums, "2026-08-01T10:00:01")
    assert middle.peak == [2, 40, 1000, 50]
    assert middle.first == [2, 3, 1000, 50]
    assert middle.raw == [4, 43, 2000, 100]
    assert mr.as_of(sums, None).peak == [2, 179, 1000, 50]


def test_as_of_peak_does_not_depend_on_the_order_entries_are_in(tmp_path):
    # Interleaved replay of a streamed message (measured on real data: 2,
    # 270, 2, 270, 2) - neither the first nor the last entry is the peak.
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "m1", usage(output_tokens=2)),
            ("2026-08-01T10:00:01", "m1", usage(output_tokens=270)),
            ("2026-08-01T10:00:02", "m1", usage(output_tokens=2)),
        ],
    )
    sums = mr.build_prefix_sums(mr.scan_transcript(str(path)))
    assert mr.as_of(sums, None).peak == [0, 270, 0, 0]


def _model_line(stamp: str, message_id: str, model: str, **usage: int) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": stamp,
            "message": {"id": message_id, "model": model, "usage": usage},
        }
    )


def test_scan_transcript_carries_each_entrys_model(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        "\n".join(
            [
                _model_line("2026-08-01T10:00:00", "a1", "model-alpha", input_tokens=1),
                _model_line("2026-08-01T10:00:01", "a2", "model-beta", output_tokens=2),
            ]
        )
        + "\n"
    )

    timeline = mr.scan_transcript(str(path))

    assert [entry[3] for entry in timeline] == ["model-alpha", "model-beta"]


def test_scan_transcript_maps_a_missing_model_to_unknown(tmp_path):
    # Same rule parse_transcript applies, reached through the shared helper -
    # a message with no `model` key must still land in a bucket, or the
    # reconstruction silently loses its tokens.
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-01T10:00:00",
                "message": {"id": "a1", "usage": {"input_tokens": 5}},
            }
        )
        + "\n"
    )

    timeline = mr.scan_transcript(str(path))

    assert timeline[0][3] == "unknown"


def test_model_totals_as_of_sums_per_model_up_to_the_moment(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        "\n".join(
            [
                _model_line("2026-08-01T10:00:00", "a1", "model-alpha", input_tokens=1),
                _model_line("2026-08-01T10:00:01", "a2", "model-beta", output_tokens=20),
                _model_line("2026-08-01T10:00:02", "a3", "model-alpha", input_tokens=100),
            ]
        )
        + "\n"
    )
    timeline = mr.scan_transcript(str(path))

    early = mr.model_totals_as_of(timeline, "2026-08-01T10:00:01")
    assert early == {
        "model-alpha": {
            "input_tokens": 1,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        "model-beta": {
            "input_tokens": 0,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    }

    end = mr.model_totals_as_of(timeline, None)
    assert end["model-alpha"]["input_tokens"] == 101


def test_model_totals_as_of_ignores_replayed_entries(tmp_path):
    # A resumed/compacted transcript replays earlier turns. The duplicate flag
    # scan_transcript already computes must suppress them here too, or the
    # reconstruction inflates exactly the model that was replayed.
    path = tmp_path / "t.jsonl"
    line = _model_line("2026-08-01T10:00:00", "dup", "model-alpha", input_tokens=10)
    path.write_text("\n".join([line, line, line]) + "\n")
    timeline = mr.scan_transcript(str(path))

    assert mr.model_totals_as_of(timeline, None)["model-alpha"]["input_tokens"] == 10


def test_model_totals_as_of_skips_synthetic_only_when_it_contributes_nothing(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(
        "\n".join(
            [
                _model_line("2026-08-01T10:00:00", "a1", "<synthetic>"),
                _model_line("2026-08-01T10:00:01", "a2", "<synthetic>", input_tokens=7),
            ]
        )
        + "\n"
    )
    timeline = mr.scan_transcript(str(path))

    totals = mr.model_totals_as_of(timeline, None)

    # The zero-token synthetic entry gets no bucket; the one carrying tokens
    # does, so the per-model sums still reconcile with the record's totals.
    assert totals == {
        "<synthetic>": {
            "input_tokens": 7,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
    }


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
    assert stats["corrected: stored the raw sum (every entry)"] == 1
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


def test_plan_corrections_refuses_a_raw_total_split_differently_across_fields(tmp_path):
    # stored's TOTAL (200) equals the raw sum's, but its per-field split
    # does not: it is not the raw sum, so it is not recognised at all.
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=100)),  # replay
        ],
    )
    records = [record("s1", "2026-08-01T10:00:01", 90, 110)]
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session has a row matching neither formula"] == 1


def test_plan_corrections_refuses_a_near_miss_of_the_raw_sum(tmp_path):
    # A 2% tolerance used to accept this (0.5% off the raw sum) as broken-hook
    # output. It was measured for the August run's rows, which are corrected
    # now; on 2026-09-23 every row the real plan corrects, subagent and
    # session, matches its formula exactly, and a near-miss corrected from
    # the reconstruction is a row rewritten from reconstruction noise. So
    # both formulas are recognised on an exact match only.
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=1000)),
            ("2026-08-01T10:00:01", "msg-1", usage(input_tokens=1000)),  # replay
        ],
    )
    records = [record("s1", "2026-08-01T10:00:01", 1990, 0)]  # raw is 2000
    plan, stats, _factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: session has a row matching neither formula"] == 1


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


def test_plan_corrections_counts_a_replay_filed_before_its_original_at_its_peak(tmp_path):
    # A replayed turn keeps its ORIGINAL timestamp, so file order and
    # timestamp order can disagree: here msg-1 appears first in the FILE at
    # the LATER timestamp and second in the file at the EARLIER one. The
    # first-occurrence formula flags the earlier-stamped entry a duplicate,
    # so as of that moment it counted ZERO - and this test used to pin a
    # "correction" of the stored 100 down to 0. Under the peak rule the
    # message counts once at the largest entry up to the moment, which is
    # 100: the row is already correct and nothing is rewritten.
    path = tmp_path / "s1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:05", "msg-1", usage(input_tokens=100)),  # later ts, first in file
            ("2026-08-01T10:00:00", "msg-1", usage(input_tokens=100)),  # earlier ts, replay
        ],
    )
    records = [record("s1", "2026-08-01T10:00:00", 100, 0)]
    plan, stats, factors = mr.plan_corrections(records, {"s1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: already correct"] == 1
    assert factors == []


# --- plan_corrections_by_formula: raw AND first-occurrence rows -------------


def _streamed_transcript(path: Path) -> None:
    # m1 streamed in three entries (output 3 -> 40 -> 179), m2 in one.
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "m1", usage(2, 3, 1000, 50)),
            ("2026-08-01T10:00:01", "m1", usage(2, 40, 1000, 50)),
            ("2026-08-01T10:00:02", "m1", usage(2, 179, 1000, 50)),
            ("2026-08-01T10:00:03", "m2", usage(1, 7, 10, 0)),
        ],
    )


def test_plan_raises_a_row_that_stored_the_first_occurrence_sum(tmp_path):
    # What the hook wrote from 2026-08-11 on: each id's FIRST entry only.
    path = tmp_path / "a1.jsonl"
    _streamed_transcript(path)
    records = [record("s1", "2026-08-01T10:00:03", 3, 10, 1010, 50)]  # first = [3, 10, 1010, 50]

    result = mr.plan_corrections_by_formula(records, {"s1": str(path)}, cutoff="2026-08-13")

    assert result.values == {0: [3, 186, 1010, 50]}
    assert result.formulas == {0: "first"}
    assert +result.stats == {"corrected: stored the first-occurrence sum": 1}
    assert result.model_maps == {
        0: {
            "unknown": {
                "input_tokens": 3,
                "output_tokens": 186,
                "cache_read_tokens": 1010,
                "cache_creation_tokens": 50,
            }
        }
    }


def test_plan_lowers_a_row_that_stored_the_raw_sum_of_a_streamed_message(tmp_path):
    # What the hook wrote before 2026-08-11: every entry. The correction is
    # to the PEAK, not to the first entry - the old reconstruction's target.
    path = tmp_path / "a1.jsonl"
    _streamed_transcript(path)
    records = [record("s1", "2026-08-01T10:00:03", 7, 229, 3010, 150)]  # raw

    result = mr.plan_corrections_by_formula(records, {"s1": str(path)}, cutoff="2026-08-13")

    assert result.values == {0: [3, 186, 1010, 50]}
    assert result.formulas == {0: "raw"}


def test_plan_leaves_a_row_already_at_the_peak_alone(tmp_path):
    path = tmp_path / "a1.jsonl"
    _streamed_transcript(path)
    records = [record("s1", "2026-08-01T10:00:03", 3, 186, 1010, 50)]

    result = mr.plan_corrections_by_formula(records, {"s1": str(path)}, cutoff="2026-08-13")

    assert result.values == {}
    assert result.stats["untouched: already correct"] == 1


def test_plan_corrects_each_snapshot_of_a_unit_from_its_own_formula(tmp_path):
    # One unit whose early snapshot was written by the raw hook and whose
    # later one by the first-occurrence hook - the deploy landed between
    # them. Both are corrected, each to its own as-of peak.
    path = tmp_path / "a1.jsonl"
    _streamed_transcript(path)
    records = [
        record("s1", "2026-08-01T10:00:02", 6, 222, 3000, 150),  # raw as of 10:00:02
        record("s1", "2026-08-01T10:00:03", 3, 10, 1010, 50),  # first as of 10:00:03
    ]

    result = mr.plan_corrections_by_formula(records, {"s1": str(path)}, cutoff="2026-08-13")

    assert result.values == {0: [2, 179, 1000, 50], 1: [3, 186, 1010, 50]}
    assert result.formulas == {0: "raw", 1: "first"}


def test_plan_never_takes_a_near_miss_for_a_first_occurrence_sum(tmp_path):
    # A 2% near-miss tolerance was measured for the RAW formula in August
    # (and has since been dropped too). Extended to this formula it
    # recognised real session rows (2026-09-23 dry run) whose transcripts
    # have NO streamed ids - first == peak - and which sat one late message
    # below the as-of reconstruction: rows the hook got right, which a
    # "correction" would have rewritten from reconstruction noise. Every one
    # of the real subagent rows it must catch matches exactly.
    path = tmp_path / "a1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "m1", usage(0, 100, 0, 0)),
            ("2026-08-01T10:00:01", "m1", usage(0, 101, 0, 0)),
            ("2026-08-01T10:00:02", "m2", usage(0, 900, 0, 0)),
        ],
    )
    # first = [0, 1000, 0, 0] (0.5% away), raw = [0, 1101, 0, 0] (~9% away)
    records = [record("s1", "2026-08-01T10:00:02", 0, 1000 - 5, 0, 0)]

    result = mr.plan_corrections_by_formula(records, {"s1": str(path)}, cutoff="2026-08-13")

    assert result.values == {}
    assert result.stats["untouched: session has a row matching neither formula"] == 1


def test_model_totals_as_of_counts_a_streamed_message_at_its_peak(tmp_path):
    path = tmp_path / "t.jsonl"
    lines = [
        _model_line("2026-08-01T10:00:00", "m1", "model-alpha", output_tokens=3),
        _model_line("2026-08-01T10:00:01", "m1", "model-alpha", output_tokens=179),
        _model_line("2026-08-01T10:00:02", "m1", "model-alpha", output_tokens=2),
    ]
    path.write_text("\n".join(lines) + "\n")
    timeline = mr.scan_transcript(str(path))

    assert mr.model_totals_as_of(timeline, None)["model-alpha"]["output_tokens"] == 179
    early = mr.model_totals_as_of(timeline, "2026-08-01T10:00:00")
    assert early["model-alpha"]["output_tokens"] == 3


def test_apply_plan_replaces_a_map_it_was_given_in_place_and_never_adds_one():
    records = [
        dict(record("s1", "x", 5, 5), tokens_by_model={"x": {}}, tail=1),
        record("s2", "x", 5, 5),
        dict(record("s3", "x", 5, 5), tokens_by_model={"x": {}}),
    ]
    new_map = {"m": dict.fromkeys(mr.TOKEN_FIELDS, 1)}
    out = mr.apply_plan(records, {0: [1, 1, 1, 1], 1: [1, 1, 1, 1], 2: [1, 1, 1, 1]}, {0: new_map})
    assert out[0]["tokens_by_model"] == new_map
    assert list(out[0]) == list(records[0])  # same key position
    assert "tokens_by_model" not in out[1]
    assert "tokens_by_model" not in out[2]  # no rebuilt map: dropped, as before


def test_verify_fields_lets_a_may_increase_row_rise_and_nothing_else():
    before: list[dict[str, Any]] = [
        {"input_tokens": 10, "tokens_by_model": {"a": 1}},
        {"input_tokens": 10},
    ]
    rose: list[dict[str, Any]] = [
        {"input_tokens": 12, "tokens_by_model": {"a": 2}},
        {"input_tokens": 12},
    ]
    problems = mr.verify_fields(
        before, rose, may_increase=frozenset({0}), allow_changed_keys=frozenset({"tokens_by_model"})
    )
    assert problems == ["line 2: input_tokens increased"]

    fell: list[dict[str, Any]] = [
        {"input_tokens": 8, "tokens_by_model": {"a": 1}},
        {"input_tokens": 8},
    ]
    problems = mr.verify_fields(before, fell, may_increase=frozenset({0}))
    assert problems == ["line 1: input_tokens decreased"]

    moved: list[dict[str, Any]] = [
        {"tokens_by_model": {"a": 1}, "input_tokens": 10},
        {"input_tokens": 10},
    ]
    problems = mr.verify_fields(before, moved, allow_changed_keys=frozenset({"tokens_by_model"}))
    assert problems == ["line 1: key order changed"]


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


# --- subagent units: grouped by agent_id, never by the parent session_id ----


def subagent_record(agent_id: str, timestamp_end: str, input_tokens: int) -> dict[str, Any]:
    # Every subagent row also carries session_id - its PARENT's. Two agents
    # dispatched by one session therefore share it, which is exactly what a
    # grouping on session_id cannot tell apart.
    return dict(record("parent", timestamp_end, input_tokens, 0), agent_id=agent_id)


def test_plan_corrections_corrects_two_subagents_of_one_parent_independently(tmp_path):
    # The contamination test (the 2026-08-06 bug, for this function): two
    # subagents of the same parent session, each with its own duplicated
    # transcript. Grouped by agent_id, each is measured against its own
    # transcript and lands on its own deduped value. Grouped by session_id,
    # both rows fall into one unit keyed "parent", which has no transcript
    # of its own - and with a parent transcript present they would both be
    # measured against it.
    first = tmp_path / "agent-a1.jsonl"
    second = tmp_path / "agent-a2.jsonl"
    parent = tmp_path / "parent.jsonl"
    write_transcript(
        first,
        [
            ("2026-08-01T10:00:00", "m1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "m1", usage(input_tokens=100)),  # replay
        ],
    )
    write_transcript(
        second,
        [
            ("2026-08-01T11:00:00", "m2", usage(input_tokens=30)),
            ("2026-08-01T11:00:01", "m2", usage(input_tokens=30)),  # replay
            ("2026-08-01T11:00:02", "m2", usage(input_tokens=30)),  # replay
        ],
    )
    write_transcript(parent, [("2026-08-01T09:00:00", "p1", usage(input_tokens=7))])
    records = [
        subagent_record("a1", "2026-08-01T10:00:01", 200),  # raw: 2 x 100
        subagent_record("a2", "2026-08-01T11:00:02", 90),  # raw: 3 x 30
    ]
    transcripts = {"a1": str(first), "a2": str(second), "parent": str(parent)}

    plan, stats, factors = mr.plan_corrections(
        records, transcripts, cutoff="2026-08-13", id_key="agent_id"
    )

    assert plan == {0: [100, 0, 0, 0], 1: [30, 0, 0, 0]}
    assert +stats == {"corrected: stored the raw sum (every entry)": 2}  # unary + drops zero counts
    assert sorted(factors) == [2.0, 3.0]


def test_plan_corrections_still_groups_on_session_id_by_default(tmp_path):
    # The sessions path is unchanged: without id_key, rows group on
    # session_id, so a transcript keyed by an agent id is never consulted.
    path = tmp_path / "agent-a1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "m1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "m1", usage(input_tokens=100)),
        ],
    )
    records = [subagent_record("a1", "2026-08-01T10:00:01", 200)]
    plan, stats, _factors = mr.plan_corrections(records, {"a1": str(path)}, cutoff="2026-08-13")
    assert plan == {}
    assert stats["untouched: no transcript on disk"] == 1


def test_plan_corrections_labels_a_spanning_unit_by_agent_when_grouped_by_agent_id(tmp_path):
    # The disposition label used to say "session" unconditionally, even for
    # a --kind subagents run grouped by agent_id (2026-09-23 review) -
    # misleading in scripts/backfill-token-dedup.py's own printed "Row
    # disposition" report on that kind. Grouped by agent_id, it must say
    # "agent" instead, and never also register under the "session" wording.
    path = tmp_path / "agent-a1.jsonl"
    write_transcript(
        path,
        [
            ("2026-08-01T10:00:00", "m1", usage(input_tokens=100)),
            ("2026-08-01T10:00:01", "m1", usage(input_tokens=100)),
        ],
    )
    records = [
        subagent_record("a1", "2026-08-01T10:00:01", 200),  # correctable on its own
        subagent_record("a1", "2026-08-14T00:00:00", 200),  # past the cutoff
    ]
    plan, stats, _factors = mr.plan_corrections(
        records, {"a1": str(path)}, cutoff="2026-08-13", id_key="agent_id"
    )
    assert plan == {}
    assert stats["untouched: agent spans the cutoff"] == 2
    assert "untouched: session spans the cutoff" not in stats


def test_plan_corrections_labels_a_neither_formula_unit_by_agent_when_grouped_by_agent_id(
    tmp_path,
):
    path = tmp_path / "agent-a1.jsonl"
    write_transcript(path, [("2026-08-01T10:00:00", "m1", usage(input_tokens=1000))])
    # 1500 is 50% off the raw sum (1000) - well outside tolerance.
    records = [subagent_record("a1", "2026-08-01T10:00:00", 1500)]
    plan, stats, _factors = mr.plan_corrections(
        records, {"a1": str(path)}, cutoff="2026-08-13", id_key="agent_id"
    )
    assert plan == {}
    assert stats["untouched: agent has a row matching neither formula"] == 1
    assert "untouched: session has a row matching neither formula" not in stats


def test_find_nonmonotonic_sessions_groups_subagents_by_agent_id():
    # Two agents of one parent: a1 finishes big, a2 finishes small and later.
    # Each series is non-decreasing on its own; merged under the parent's
    # session_id they read as a drop, which would refuse a correct rewrite.
    records = [
        dict(subagent_record("a1", "x", 500), duration_seconds=10),
        dict(subagent_record("a2", "x", 40), duration_seconds=20),
    ]
    assert mr.find_nonmonotonic_sessions(records, id_key="agent_id") == set()
    assert mr.find_nonmonotonic_sessions(records) == {"parent"}


def test_find_nonmonotonic_sessions_still_flags_a_drop_within_one_subagent():
    records = [
        dict(subagent_record("a1", "x", 500), duration_seconds=10),
        dict(subagent_record("a1", "x", 40), duration_seconds=20),
    ]
    assert mr.find_nonmonotonic_sessions(records, id_key="agent_id") == {"a1"}


def test_file_kind_problem_accepts_rows_that_match_the_declared_kind():
    sessions = [record("s1", "x", 1, 0)]
    subagents = [subagent_record("a1", "x", 1)]
    assert mr.file_kind_problem(sessions, "sessions") is None
    assert mr.file_kind_problem(subagents, "subagents") is None
    assert mr.file_kind_problem([], "subagents") is None


def test_file_kind_problem_refuses_a_subagents_run_on_rows_without_agent_id():
    rows = [record("s1", "x", 1, 0), subagent_record("a1", "x", 1)]
    problem = mr.file_kind_problem(rows, "subagents")
    assert problem is not None
    assert "1 of 2 rows have no agent_id" in problem


def test_file_kind_problem_refuses_a_subagents_run_on_rows_whose_agent_id_is_empty():
    # A null agent_id groups every such row into one unit keyed None: the
    # same merge as grouping on session_id, one level down.
    rows = [dict(record("s1", "x", 1, 0), agent_id=None)]
    problem = mr.file_kind_problem(rows, "subagents")
    assert problem is not None
    assert "1 of 1 rows have no agent_id" in problem


def test_file_kind_problem_refuses_a_sessions_run_on_subagent_rows():
    rows = [subagent_record("a1", "x", 1), subagent_record("a2", "x", 1)]
    problem = mr.file_kind_problem(rows, "sessions")
    assert problem is not None
    assert "2 of 2 rows carry an agent_id" in problem


def test_subagent_transcripts_resolves_each_unit_through_its_recorded_path(tmp_path):
    here = tmp_path / "agent-a1.jsonl"
    here.write_text("")
    moved = tmp_path / "moved-agent-a2.jsonl"
    moved.write_text("")
    records = [
        dict(subagent_record("a1", "x", 1), agent_transcript_path=str(here)),
        dict(subagent_record("a1", "y", 2), agent_transcript_path=str(here)),
        # stale recorded path, relocated through the by-id index
        dict(subagent_record("a2", "x", 1), agent_transcript_path=str(tmp_path / "gone")),
        # no recorded path at all: never guessed from the index
        dict(subagent_record("a3", "x", 1), agent_transcript_path=None),
        # stale and not in the index
        dict(subagent_record("a4", "x", 1), agent_transcript_path=str(tmp_path / "gone")),
        # no agent_id: never a unit of its own, never keyed None
        dict(subagent_record("a5", "x", 1), agent_id=None, agent_transcript_path=str(here)),
    ]
    index = {"a2": str(moved), "a3": str(here)}

    resolved = mr.subagent_transcripts(records, index)

    assert resolved == {"a1": str(here), "a2": str(moved)}


def test_subagent_transcripts_drops_a_unit_whose_rows_resolve_to_different_files(tmp_path):
    first = tmp_path / "one.jsonl"
    first.write_text("")
    second = tmp_path / "two.jsonl"
    second.write_text("")
    records = [
        dict(subagent_record("a1", "x", 1), agent_transcript_path=str(first)),
        dict(subagent_record("a1", "y", 2), agent_transcript_path=str(second)),
    ]
    assert mr.subagent_transcripts(records, {}) == {}


def _copy(projects: Path, slug: str, parent: str, agent_id: str, body: str) -> str:
    directory = projects / slug / parent / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"agent-{agent_id}.jsonl"
    path.write_text(body)
    return str(path)


def test_identical_copies_under_two_slugs_are_one_transcript_not_an_ambiguity(tmp_path):
    # Checked 2026-09-28: every agent id found under two project slugs was
    # a byte-identical copy (a project directory renamed, a worktree slug
    # migrated). Which copy is read changes nothing, so refusing them left
    # correctable rows behind for no reason.
    projects = tmp_path / "projects"
    first = _copy(projects, "slug-a", "sess-1", "dup", "same\n")
    _copy(projects, "slug-b", "sess-1", "dup", "same\n")

    index, ambiguous = mr.index_subagent_transcripts(str(projects))

    assert index == {"dup": first}
    assert ambiguous == []


def test_copies_that_differ_stay_ambiguous(tmp_path):
    projects = tmp_path / "projects"
    _copy(projects, "slug-a", "sess-1", "dup", "one\n")
    _copy(projects, "slug-b", "sess-1", "dup", "one\ntwo\n")

    index, ambiguous = mr.index_subagent_transcripts(str(projects))

    assert index == {}
    assert ambiguous == ["dup"]


def test_differing_copies_are_told_apart_by_the_rows_own_parent_session(tmp_path):
    # A subagent transcript lives under <slug>/<parent session>/subagents/,
    # and the row records that parent as its session_id - the row's own data,
    # not a guess, picks the copy when exactly one sits under its parent.
    projects = tmp_path / "projects"
    mine = _copy(projects, "slug-a", "parent", "a1", "one\n")
    _copy(projects, "slug-b", "other-parent", "a1", "two\n")
    # a2: both differing copies sit under a directory named for its parent.
    _copy(projects, "slug-a", "parent", "a2", "one\n")
    _copy(projects, "slug-b", "parent", "a2", "two\n")
    found = mr.find_subagent_transcripts(str(projects))
    index, ambiguous = mr.index_subagent_transcripts(str(projects), found)
    gone = str(tmp_path / "gone" / "agent-x.jsonl")
    records = [
        dict(subagent_record("a1", "x", 1), agent_transcript_path=gone),
        dict(subagent_record("a1", "y", 2), agent_transcript_path=gone),
        dict(subagent_record("a2", "x", 1), agent_transcript_path=gone),
    ]
    candidates = {agent_id: found[agent_id] for agent_id in ambiguous}

    assert mr.subagent_transcripts(records, index, candidates) == {"a1": mine}
    # Without the candidates, both stay unresolved, as before.
    assert mr.subagent_transcripts(records, index) == {}


def test_a_row_with_no_recorded_path_is_not_resolved_through_its_parent_either(tmp_path):
    projects = tmp_path / "projects"
    mine = _copy(projects, "slug-a", "parent", "a1", "one\n")
    _copy(projects, "slug-b", "other", "a1", "two\n")
    records = [dict(subagent_record("a1", "x", 1), agent_transcript_path=None)]

    assert mr.subagent_transcripts(records, {}, {"a1": [mine]}) == {}


def test_subagent_transcripts_drops_a_unit_where_one_row_does_not_resolve(tmp_path):
    here = tmp_path / "agent-a1.jsonl"
    here.write_text("")
    records = [
        dict(subagent_record("a1", "x", 1), agent_transcript_path=str(here)),
        dict(subagent_record("a1", "y", 2), agent_transcript_path=None),
    ]
    assert mr.subagent_transcripts(records, {"a1": str(here)}) == {}


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
            "session_id": f"s{day}",  # one snapshot each, so each delta is its own total
            "duration_seconds": 0,
            "timestamp_end": f"2026-08-0{day}T10:00:00",
            "input_tokens": day,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        for day in range(1, 6)
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


def _names(count: int, prefix: str = "row") -> list[str]:
    """`count` distinct row names, standing in for row_identities."""
    return [f"{prefix}{number}" for number in range(count)]


def test_load_backfill_state_returns_none_when_no_file_exists(tmp_path):
    assert mr.load_backfill_state(str(tmp_path / "missing.json")) is None


def test_write_then_load_backfill_state_round_trips(tmp_path):
    path = str(tmp_path / "state.json")
    written = mr.write_backfill_state("2026-08-19", "2026-08-19T12:00:00", _names(3411), path=path)
    loaded = mr.load_backfill_state(path)
    assert loaded == written
    assert loaded["cutoff"] == "2026-08-19"
    assert loaded["corrected_rows"] == 3411
    assert loaded["rows_changed_this_run"] == 3411
    assert loaded["corrected_row_ids"] == sorted(_names(3411))


def test_load_backfill_state_returns_none_on_corrupt_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    assert mr.load_backfill_state(str(path)) is None


def test_write_backfill_state_creates_missing_parent_directories(tmp_path):
    # write_backfill_state used to be a truncate-then-write with no
    # os.makedirs guard, inconsistent with the rest of this module's
    # atomic-write discipline - the real path lives under ~/.claude/metrics,
    # which is not guaranteed to exist yet on a fresh machine.
    path = str(tmp_path / "nested" / "does" / "not" / "exist" / "state.json")
    mr.write_backfill_state("2026-08-19", "2026-08-19T12:00:00", _names(1), path=path)
    assert mr.load_backfill_state(path) is not None


def test_write_backfill_state_records_uncorrectable_rows_and_earliest_corrected(tmp_path):
    path = str(tmp_path / "state.json")
    written = mr.write_backfill_state(
        "2026-08-19",
        "2026-08-19T12:00:00",
        _names(3411),
        uncorrectable_rows=42,
        earliest_corrected="2026-07-22T09:00:00",
        path=path,
    )
    loaded = mr.load_backfill_state(path)
    assert loaded == written
    assert loaded["uncorrectable_rows"] == 42
    assert loaded["earliest_corrected"] == "2026-07-22T09:00:00"


def test_write_backfill_state_for_subagents_keeps_the_sessions_entry(tmp_path):
    # One state file, two metrics files. The sessions entry stays the flat
    # top-level shape both dashboards already read; a subagents run nests
    # its own entry under "subagents" and must not overwrite the sessions one.
    path = str(tmp_path / "state.json")
    mr.write_backfill_state(
        "2026-08-21",
        "2026-08-21T19:49:23",
        _names(2500),
        earliest_corrected="2026-07-22",
        path=path,
    )
    written = mr.write_backfill_state(
        "2026-08-21",
        "2026-09-23T20:00:00",
        _names(150),
        uncorrectable_rows=7,
        earliest_corrected="2026-07-10T00:00:00",
        path=path,
        kind="subagents",
    )
    loaded = mr.load_backfill_state(path)
    assert loaded == written
    assert loaded["applied_at"] == "2026-08-21T19:49:23"
    assert loaded["corrected_rows"] == 2500
    assert loaded["earliest_corrected"] == "2026-07-22"
    assert loaded["subagents"] == {
        "cutoff": "2026-08-21",
        "applied_at": "2026-09-23T20:00:00",
        "corrected_rows": 150,
        "rows_changed_this_run": 150,
        "corrected_row_ids": sorted(_names(150)),
        "uncorrectable_rows": 7,
        "earliest_corrected": "2026-07-10T00:00:00",
    }
    # The boundary note both dashboards render still describes the sessions run.
    assert "2026-07-22" in mr.describe_backfill_state(loaded, lang="en")


def test_write_backfill_state_for_sessions_keeps_the_subagents_entry(tmp_path):
    path = str(tmp_path / "state.json")
    mr.write_backfill_state(
        "2026-08-21", "2026-09-23T20:00:00", _names(150), path=path, kind="subagents"
    )
    mr.write_backfill_state("2026-08-25", "2026-09-24T09:00:00", _names(12), path=path)
    loaded = mr.load_backfill_state(path)
    assert loaded is not None
    assert loaded["cutoff"] == "2026-08-25"
    assert loaded["corrected_rows"] == 12
    assert loaded["subagents"]["corrected_rows"] == 150


def test_a_second_apply_extends_the_record_instead_of_replacing_it(tmp_path):
    # A later run corrects only what the earlier ones left. Replacing the
    # entry would report its handful of rows as the whole correction and
    # move the retention boundary forward to ITS earliest row - on a copy of
    # the real files, thousands of corrected session rows became a few dozen
    # and the boundary moved from July to 2026-08-12.
    path = str(tmp_path / "state.json")
    mr.write_backfill_state(
        "2026-08-21",
        "2026-08-21T19:49:23",
        _names(2500),
        10,
        earliest_corrected="2026-07-22",
        path=path,
    )
    written = mr.write_backfill_state(
        "2026-09-23",
        "2026-09-23T21:00:00",
        _names(41, "later"),
        8000,
        earliest_corrected="2026-08-12",
        path=path,
    )
    assert written == {
        "cutoff": "2026-09-23",
        "applied_at": "2026-09-23T21:00:00",
        "corrected_rows": 2541,
        "rows_changed_this_run": 41,
        "corrected_row_ids": sorted(_names(2500) + _names(41, "later")),
        "uncorrectable_rows": 8000,
        "earliest_corrected": "2026-07-22",
    }


def test_a_row_two_token_runs_corrected_is_counted_once(tmp_path):
    # Adding each run's count, as the entry did before 2026-09-29, counted
    # a row corrected by two runs twice - the double count the field
    # entries were fixed for. The token entry keeps names the same way.
    path = str(tmp_path / "state.json")
    mr.write_backfill_state("2026-08-21", "a", ["e1", "e2"], path=path)
    written = mr.write_backfill_state("2026-09-23", "b", ["e2", "e3"], path=path)
    assert written["corrected_rows"] == 3
    assert written["rows_changed_this_run"] == 2
    assert written["corrected_row_ids"] == ["e1", "e2", "e3"]
    assert "unidentified_earlier_rows" not in written


def test_a_second_apply_over_a_record_without_a_boundary_keeps_it_unknown(tmp_path):
    # The real state predates earliest_corrected. The earlier run's boundary
    # is unknown, so the later run's own earliest row is no boundary either.
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"cutoff": "2026-08-21", "applied_at": "2026-08-21T19:49", "corrected_rows": 5})
    )
    written = mr.write_backfill_state(
        "2026-09-23",
        "2026-09-23T21:00:00",
        _names(41),
        0,
        earliest_corrected="2026-08-12",
        path=str(path),
    )
    assert written["corrected_rows"] == 46
    assert written["earliest_corrected"] is None


def test_a_token_apply_over_an_old_shape_entry_keeps_its_count_unidentified(tmp_path):
    # The real state's token entries hold a count and no names. Which
    # of those a later run touches again cannot be
    # told, so the count is carried as unidentified, and a third run still
    # carries it rather than dropping it.
    path = tmp_path / "state.json"
    old = {
        "cutoff": "2026-09-23",
        "applied_at": "2026-09-23T21:38:39",
        "corrected_rows": 2541,
        "uncorrectable_rows": 8000,
        "earliest_corrected": None,
        "subagents": {
            "cutoff": "2026-09-25",
            "applied_at": "2026-09-29T10:58:01",
            "corrected_rows": 1100,
            "uncorrectable_rows": 1300,
            "earliest_corrected": "2026-08-11T20:45:41.010Z",
        },
    }
    path.write_text(json.dumps(old))
    note_before = {
        lang: mr.describe_backfill_state(mr.load_backfill_state(str(path)), lang=lang)
        for lang in ("en", "pt")
    }

    mr.write_backfill_state("2026-09-28", "2026-09-28T10:00", ["e1", "e2"], 8000, path=str(path))
    written = mr.write_backfill_state(
        "2026-09-29", "2026-09-29T10:00", ["e2", "e3"], 7000, path=str(path)
    )
    subagents = mr.write_backfill_state(
        "2026-09-29", "2026-09-29T11:00", ["a1"], 3, "2026-08-20", path=str(path), kind="subagents"
    )

    assert written["corrected_rows"] == 2541 + 3
    assert written["rows_changed_this_run"] == 2
    assert written["unidentified_earlier_rows"] == 2541
    assert written["corrected_row_ids"] == ["e1", "e2", "e3"]
    assert subagents["subagents"]["corrected_rows"] == 1100 + 1
    assert subagents["subagents"]["unidentified_earlier_rows"] == 1100
    assert subagents["subagents"]["earliest_corrected"] == "2026-08-11T20:45:41.010Z"
    # The note both dashboards render reads none of the new keys.
    for lang, note in note_before.items():
        loaded = mr.load_backfill_state(str(path))
        assert mr.describe_backfill_state(loaded, lang=lang) == note.replace(
            "2026-09-23", "2026-09-29"
        )


@pytest.mark.parametrize("earlier", [{"corrected_rows": 0}, {}], ids=["zero", "missing"])
def test_an_old_token_entry_that_corrected_nothing_leaves_nothing_unidentified(tmp_path, earlier):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"cutoff": "2026-08-21", "applied_at": "a", **earlier}))
    written = mr.write_backfill_state("2026-09-23", "b", ["e1"], path=str(path))
    assert written["corrected_rows"] == 1
    assert "unidentified_earlier_rows" not in written


def test_a_sessions_entry_drops_an_unidentified_count_left_by_no_real_run(tmp_path):
    # The sessions entry lives at the top level beside other keys. A count
    # left there without a run to own it (no applied_at, so not merged)
    # must not survive a write that carries no unidentified rows.
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"unidentified_earlier_rows": 7}))
    written = mr.write_backfill_state("2026-09-23", "2026-09-23T10:00", ["e1"], path=str(path))
    assert written["corrected_rows"] == 1
    assert "unidentified_earlier_rows" not in written


def test_a_new_shape_token_state_renders_the_same_note_as_the_old_shape(tmp_path):
    old = {
        "cutoff": "2026-09-23",
        "applied_at": "2026-09-23T21:38:39",
        "corrected_rows": 2541,
        "uncorrectable_rows": 8000,
        "earliest_corrected": "2026-07-22T09:00:00",
    }
    path = tmp_path / "state.json"
    written = mr.write_backfill_state(
        "2026-09-23",
        "2026-09-23T21:38:39",
        _names(2541),
        8000,
        "2026-07-22T09:00:00",
        path=str(path),
    )
    for lang in ("en", "pt"):
        assert mr.describe_backfill_state(written, lang=lang) == mr.describe_backfill_state(
            old, lang=lang
        )


def test_a_second_subagents_apply_extends_the_subagents_record(tmp_path):
    path = str(tmp_path / "state.json")
    mr.write_backfill_state(
        "2026-08-21",
        "a",
        _names(42),
        7,
        earliest_corrected="2026-08-11",
        path=path,
        kind="subagents",
    )
    written = mr.write_backfill_state(
        "2026-09-23",
        "b",
        _names(800, "later"),
        3,
        earliest_corrected="2026-08-24",
        path=path,
        kind="subagents",
    )
    assert written["subagents"] == {
        "cutoff": "2026-09-23",
        "applied_at": "b",
        "corrected_rows": 842,
        "rows_changed_this_run": 800,
        "corrected_row_ids": sorted(_names(42) + _names(800, "later")),
        "uncorrectable_rows": 3,
        "earliest_corrected": "2026-08-11",
    }


def test_a_later_apply_that_reaches_further_back_moves_the_boundary_back(tmp_path):
    # A unit an earlier run skipped (it spanned that run's cutoff) can hold
    # rows older than anything that run corrected.
    path = str(tmp_path / "state.json")
    mr.write_backfill_state(
        "2026-08-21", "a", _names(10), earliest_corrected="2026-08-01", path=path
    )
    written = mr.write_backfill_state(
        "2026-09-23", "b", _names(3, "later"), earliest_corrected="2026-07-15", path=path
    )
    assert written["earliest_corrected"] == "2026-07-15"
    assert written["corrected_rows"] == 13


# A state that exists but cannot be read safely is never taken for an
# empty one: a writer that did would replace every recorded boundary and
# row name with its own entry alone. The shapes are shared with the script
# and dashboard tests (tests/state_shapes.py).

DRY_RUN_EFFECT = "This dry run does not read the state."


def _unreadable_state(tmp_path: Path, shape: str) -> Path:
    path = tmp_path / "state.json"
    write_unreadable_state(path, shape)
    return path


@pytest.mark.parametrize("shape", SHAPE_NAMES)
@pytest.mark.parametrize("writer", ["write_backfill_state", "write_field_state"])
def test_a_state_writer_refuses_a_state_it_cannot_read(tmp_path, writer, shape):
    path = _unreadable_state(tmp_path, shape)
    try:
        with pytest.raises(mr.UnreadableStateError) as raised:
            if writer == "write_backfill_state":
                mr.write_backfill_state(
                    "2026-08-21", "b", _names(1), path=str(path), kind="subagents"
                )
            else:
                mr.write_field_state("sessions", "tool_calls", None, "b", _names(1), path=str(path))
    finally:
        restore_permissions(path)
    assert path.read_bytes() == UNREADABLE_STATES[shape]
    assert str(path) in str(raised.value)
    assert sorted(child.name for child in tmp_path.iterdir()) == ["state.json"]


@pytest.mark.parametrize("shape", SHAPE_NAMES)
def test_the_sessions_writer_refuses_a_state_it_cannot_read_too(tmp_path, shape):
    # The sessions entry is the top level itself, so it merges into
    # "fields" and "subagents" only by keeping them - still never by
    # dropping a malformed one.
    path = _unreadable_state(tmp_path, shape)
    try:
        with pytest.raises(mr.UnreadableStateError):
            mr.write_backfill_state("2026-08-21", "b", _names(1), path=str(path))
    finally:
        restore_permissions(path)
    assert path.read_bytes() == UNREADABLE_STATES[shape]


@pytest.mark.parametrize(
    ("shape", "reason"),
    [
        ("empty", "the file is empty"),
        ("not UTF-8", "UnicodeDecodeError"),
        ("a list at the top level", "it holds a JSON array, not an object"),
        ("fields is null", 'its "fields" value is a JSON null, not an object'),
        ("subagents is a string", 'its "subagents" value is a JSON string, not an object'),
        (
            "a fields entry is a list",
            'its "fields" entry "sessions.tool_calls" is a JSON array, not an object',
        ),
    ],
)
def test_the_refusal_names_what_is_wrong(tmp_path, shape, reason):
    path = _unreadable_state(tmp_path, shape)
    with pytest.raises(mr.UnreadableStateError, match=re.escape(reason)):
        mr.read_state_for_update(str(path))


def test_read_state_for_update_reads_a_missing_file_as_a_first_run(tmp_path):
    assert mr.read_state_for_update(str(tmp_path / "absent" / "state.json")) == {}


def test_read_state_for_update_returns_a_readable_state(tmp_path):
    path = tmp_path / "state.json"
    state = {
        "cutoff": "2026-08-21",
        "subagents": {"cutoff": "c"},
        "fields": {"sessions.tool_calls": {"corrected_row_ids": ["x"]}},
    }
    path.write_text(json.dumps(state))
    assert mr.read_state_for_update(str(path)) == state


# Every leaf shape a writer in this repository's history has produced, in
# every place it can sit. A valid state is never refused: the nulls are
# the writers' own ("unknown for good" counts, a by-model cutoff, no
# retention boundary), and so is total_distinct_rows, which entries
# written on 2026-09-29 carry (an int, or null) and later writers left in
# place. Keys this version does not know are kept, for a newer one.
FULL_VALID_STATE: dict[str, Any] = {
    # The flat sessions entry, as written before row names existed.
    "cutoff": "2026-08-21",
    "applied_at": "2026-09-01T10:00:00",
    "corrected_rows": 0,
    "uncorrectable_rows": 0,
    "earliest_corrected": None,
    "a key from a newer version": {"anything": [1, None]},
    "subagents": {
        "cutoff": "2026-08-21",
        "applied_at": "2026-09-02T10:00:00",
        "corrected_rows": None,
        "rows_changed_this_run": 0,
        "corrected_row_ids": [],
        "unidentified_earlier_rows": None,
        "uncorrectable_rows": 4,
        "earliest_corrected": "2026-07-01T09:00:00",
    },
    "fields": {
        "sessions.tool_calls": {
            "cutoff": "2026-08-21",
            "applied_at": "2026-09-03T10:00:00",
            "corrected_rows": 7,
            "rows_changed_this_run": 1,
            "corrected_row_ids": ["0123456789abcdef", "row-c"],
            "unidentified_earlier_rows": 6,
        },
        "subagents.tool_calls": {
            "cutoff": "2026-08-21",
            "applied_at": "2026-09-04T10:00:00",
            "corrected_rows": None,
        },
        "sessions.incomplete_usage": {
            "cutoff": "2026-08-21",
            "applied_at": "2026-09-05T10:00:00",
            "corrected_rows": 2,
            "total_distinct_rows": 2,
            "corrected_row_ids": ["row-d", "row-e"],
        },
        "subagents.incomplete_usage": {
            "cutoff": "2026-08-21",
            "applied_at": "2026-09-05T10:00:00",
            "corrected_rows": 3,
            "total_distinct_rows": None,
            "corrected_row_ids": ["row-f"],
            "unidentified_earlier_rows": 0,
        },
        "sessions.tokens_by_model": {
            "cutoff": None,
            "applied_at": "2026-09-06T10:00:00",
            "corrected_rows": 1,
            "rows_changed_this_run": 1,
            "corrected_row_ids": ["row-g"],
            "a key from a newer version": "kept",
        },
        "sessions.a pass from a newer version": {},
    },
}


def test_a_full_valid_state_is_read_for_an_update_as_it_is(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(FULL_VALID_STATE))
    assert mr.read_state_for_update(str(path)) == FULL_VALID_STATE
    for apply in (True, False):
        assert mr.unreadable_state_notice(str(path), apply=apply, dry_run_effect="") is None


@pytest.mark.parametrize("kind", ["sessions", "subagents"])
def test_both_writers_merge_into_a_full_valid_state(tmp_path, kind):
    path = tmp_path / "state.json"
    path.write_text(json.dumps(FULL_VALID_STATE))
    written = mr.write_backfill_state("2026-09-23", "b", _names(1), path=str(path), kind=kind)
    assert json.loads(path.read_text()) == written
    written = mr.write_field_state(kind, "tool_calls", None, "c", _names(1), path=str(path))
    assert json.loads(path.read_text()) == written
    assert written["a key from a newer version"] == {"anything": [1, None]}
    assert written["fields"]["sessions.a pass from a newer version"] == {}
    # The null counts are "unknown for good": a later run keeps them so.
    assert written["subagents"]["corrected_rows"] is None
    # A null count on a count-only entry is one that reported no rows.
    expected = {"sessions": 6 + 2 + 1, "subagents": 0 + 1}[kind]
    assert written["fields"][f"{kind}.tool_calls"]["corrected_rows"] == expected


SESSIONS = "top-level sessions entry"
SUBAGENTS = '"subagents" entry'
TOOL_CALLS = '"fields" entry "sessions.tool_calls"'
COUNT_OR_NULL = "not a non-negative integer or null"


@pytest.mark.parametrize(
    ("shape", "place", "key", "problem"),
    [
        (
            "the sessions cutoff is a number",
            SESSIONS,
            "cutoff",
            "is a JSON number, not a string or null",
        ),
        ("the sessions applied_at is null", SESSIONS, "applied_at", "is a JSON null, not a string"),
        (
            "the subagents corrected_rows is a boolean",
            SUBAGENTS,
            "corrected_rows",
            f"is a JSON boolean, {COUNT_OR_NULL}",
        ),
        (
            "the subagents uncorrectable_rows is null",
            SUBAGENTS,
            "uncorrectable_rows",
            "is a JSON null, not a non-negative integer",
        ),
        (
            "the sessions corrected_rows is negative",
            SESSIONS,
            "corrected_rows",
            f"is a negative number, {COUNT_OR_NULL}",
        ),
        (
            "a fields entry's corrected_rows is a decimal",
            TOOL_CALLS,
            "corrected_rows",
            f"is a decimal number, {COUNT_OR_NULL}",
        ),
        (
            "a fields entry's corrected_row_ids is a string",
            TOOL_CALLS,
            "corrected_row_ids",
            "is a JSON string, not a list of non-empty strings",
        ),
        (
            "a fields entry's corrected_row_ids holds a number",
            TOOL_CALLS,
            "corrected_row_ids",
            "holds a JSON number, not a non-empty string",
        ),
        (
            "the sessions corrected_row_ids holds an empty name",
            SESSIONS,
            "corrected_row_ids",
            "holds an empty string, not a non-empty string",
        ),
    ],
)
def test_the_refusal_names_the_malformed_leaf(tmp_path, shape, place, key, problem):
    path = _unreadable_state(tmp_path, shape)
    reason = f'the "{key}" of its {place} {problem}'
    with pytest.raises(mr.UnreadableStateError, match=re.escape(reason)):
        mr.read_state_for_update(str(path))


@pytest.mark.parametrize("shape", SHAPE_NAMES)
def test_the_notice_refuses_an_apply_and_warns_a_dry_run(tmp_path, shape):
    path = _unreadable_state(tmp_path, shape)
    try:
        refusal = mr.unreadable_state_notice(str(path), apply=True, dry_run_effect=DRY_RUN_EFFECT)
        warning = mr.unreadable_state_notice(str(path), apply=False, dry_run_effect=DRY_RUN_EFFECT)
    finally:
        restore_permissions(path)
    assert refusal is not None
    assert refusal.startswith("refusing to apply")
    assert str(path) in refusal
    assert "move it aside or fix it" in refusal
    assert "an empty file is not a missing one" in refusal
    assert DRY_RUN_EFFECT not in refusal
    assert warning is not None
    assert warning.startswith("WARNING")
    assert str(path) in warning
    assert DRY_RUN_EFFECT in warning
    assert "--apply will refuse" in warning


def test_the_notice_is_silent_for_a_missing_or_readable_state(tmp_path):
    path = tmp_path / "state.json"
    assert mr.unreadable_state_notice(str(path), apply=True, dry_run_effect="") is None
    path.write_text("{}")
    assert mr.unreadable_state_notice(str(path), apply=True, dry_run_effect="") is None


@pytest.mark.parametrize("shape", SHAPE_NAMES)
def test_load_backfill_state_stays_lenient_on_every_unreadable_shape(tmp_path, shape):
    # What the dashboards and the by-model dry run read through: it must
    # never raise, whatever the file holds.
    path = _unreadable_state(tmp_path, shape)
    try:
        loaded = mr.load_backfill_state(str(path))
    finally:
        restore_permissions(path)
    assert loaded is None or isinstance(loaded, (dict, list, int))
    for lang in ("en", "pt"):
        assert mr.describe_backfill_state(loaded, lang=lang)


def test_describe_backfill_state_when_never_applied():
    text = mr.describe_backfill_state(None, lang="pt")
    assert "2026-08-13" in text
    assert "não" in text  # Portuguese: not yet corrected


def test_describe_backfill_state_degrades_on_a_state_that_is_not_a_dict():
    # load_backfill_state() only guards against corrupt JSON; valid JSON
    # that parses to a list or a number is a different, unguarded shape
    # this used to raise TypeError on (state['cutoff'] on a list).
    text = mr.describe_backfill_state([1, 2, 3], lang="pt")  # type: ignore[arg-type]
    assert "não" in text


def test_describe_backfill_state_degrades_on_a_dict_missing_required_keys():
    text = mr.describe_backfill_state({"corrected_rows": 3411}, lang="pt")
    assert "não" in text


def test_describe_backfill_state_when_applied():
    state = {"cutoff": "2026-08-19", "applied_at": "2026-08-19T12:00:00", "corrected_rows": 3411}
    text = mr.describe_backfill_state(state, lang="pt")
    assert "2026-08-19" in text


def test_describe_backfill_state_names_the_retention_boundary_when_present():
    state = {
        "cutoff": "2026-08-19",
        "applied_at": "2026-08-19T12:00:00",
        "corrected_rows": 3411,
        "uncorrectable_rows": 42,
        "earliest_corrected": "2026-07-22T09:00:00",
    }
    text = mr.describe_backfill_state(state, lang="pt")
    assert "2026-07-22" in text


def test_describe_backfill_state_omits_the_retention_boundary_when_absent():
    state = {"cutoff": "2026-08-19", "applied_at": "2026-08-19T12:00:00", "corrected_rows": 3411}
    text = mr.describe_backfill_state(state, lang="pt")
    assert "pra corrigir" not in text


def test_verify_fields_still_rejects_an_undeclared_new_key():
    # The gate's default must stay closed: this is what stops a backfill
    # from quietly adding a field nobody reviewed.
    before = [{"session_id": "s1", "input_tokens": 10}]
    after = [{"session_id": "s1", "input_tokens": 10, "tokens_by_model": {}}]

    problems = mr.verify_fields(before, after)

    assert problems, "an undeclared added key must be reported"


def test_verify_fields_accepts_a_declared_new_key():
    before = [{"session_id": "s1", "input_tokens": 10}]
    after = [{"session_id": "s1", "input_tokens": 10, "tokens_by_model": {}}]

    problems = mr.verify_fields(before, after, allow_new_keys=frozenset({"tokens_by_model"}))

    assert problems == []


def test_verify_fields_declaring_one_key_does_not_admit_another():
    before = [{"session_id": "s1", "input_tokens": 10}]
    after = [{"session_id": "s1", "input_tokens": 10, "something_else": 1}]

    problems = mr.verify_fields(before, after, allow_new_keys=frozenset({"tokens_by_model"}))

    assert problems, "only the declared key may be added"


def test_apply_plan_drops_tokens_by_model_from_rows_it_corrects():
    # The map was computed against the totals this correction supersedes, so
    # keeping it would leave sum(tokens_by_model) disagreeing with the four
    # fields - with verify_fields approving, since the map is not a token
    # field. Absent means "unknown", which is the honest state here.
    records = [
        {
            "session_id": "s1",
            "input_tokens": 200,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "tokens_by_model": {
                "model-alpha": {
                    "input_tokens": 200,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                }
            },
        }
    ]

    out = mr.apply_plan(records, {0: [100, 0, 0, 0]})

    assert out[0]["input_tokens"] == 100
    assert "tokens_by_model" not in out[0]
    # The input is never mutated - the caller still holds the pre-correction row.
    assert "tokens_by_model" in records[0]


def test_apply_plan_leaves_tokens_by_model_on_rows_it_does_not_correct():
    records = [
        {"session_id": "s1", "input_tokens": 5, "tokens_by_model": {"m": {}}},
        {"session_id": "s2", "input_tokens": 7, "tokens_by_model": {"m": {}}},
    ]

    out = mr.apply_plan(records, {1: [3, 0, 0, 0]})

    assert out[0]["tokens_by_model"] == {"m": {}}
    assert "tokens_by_model" not in out[1]


def test_verify_fields_accepts_a_declared_removed_key():
    # This is the real shape apply_plan produces: a row that had
    # tokens_by_model no longer has it.
    before = [{"session_id": "s1", "input_tokens": 200, "tokens_by_model": {"m": {}}}]
    after = [{"session_id": "s1", "input_tokens": 100}]

    problems = mr.verify_fields(before, after, allow_removed_keys=frozenset({"tokens_by_model"}))

    assert problems == []


def test_verify_fields_still_rejects_an_undeclared_removed_key():
    # The gate's default must stay closed here too: dropping any other field
    # is still a bug, not a feature.
    before = [{"session_id": "s1", "input_tokens": 200, "tokens_by_model": {"m": {}}}]
    after = [{"session_id": "s1", "input_tokens": 100}]

    problems = mr.verify_fields(before, after)

    assert problems, "an undeclared removed key must be reported"


def test_verify_fields_allow_removed_keys_does_not_permit_adding_that_key():
    # Declaring a key removable grants only removal, not addition.
    before = [{"session_id": "s1", "input_tokens": 10}]
    after = [{"session_id": "s1", "input_tokens": 10, "tokens_by_model": {}}]

    problems = mr.verify_fields(before, after, allow_removed_keys=frozenset({"tokens_by_model"}))

    assert problems, "allow_removed_keys must not also permit adding the key"


def test_verify_fields_allow_new_keys_does_not_permit_removing_that_key():
    # Declaring a key addable grants only addition, not removal - the
    # reverse of the case above.
    before = [{"session_id": "s1", "input_tokens": 10, "tokens_by_model": {}}]
    after = [{"session_id": "s1", "input_tokens": 10}]

    problems = mr.verify_fields(before, after, allow_new_keys=frozenset({"tokens_by_model"}))

    assert problems, "allow_new_keys must not also permit removing the key"


def test_verify_fields_still_rejects_a_value_change_on_a_declared_addable_key():
    # A key already present in `old` is never in `added` (added requires
    # `key not in old`), so declaring it addable does not exempt it from the
    # value-change check. This is exactly the case sub-project 2 exercises:
    # rewriting a row that already carries tokens_by_model.
    before = [{"session_id": "s1", "input_tokens": 10, "tokens_by_model": {"m": {"x": 1}}}]
    after = [{"session_id": "s1", "input_tokens": 10, "tokens_by_model": {"m": {"x": 2}}}]

    problems = mr.verify_fields(before, after, allow_new_keys=frozenset({"tokens_by_model"}))

    assert problems, "a value change on a pre-existing declared key must still be reported"


# A token pass that corrects a row carrying tokens_by_model rebuilds that
# map by the same rule as the totals. The by-model oracle must not count
# it as native any more, so the pass names those rows in the state.


def test_write_backfill_state_names_the_maps_the_token_pass_rebuilt(tmp_path):
    path = str(tmp_path / "state.json")
    written = mr.write_backfill_state(
        "2026-09-25",
        "2026-09-25T08:35:57",
        _names(3),
        path=path,
        kind="subagents",
        rebuilt_maps=["row1", "row2"],
    )
    entry = written["fields"][f"subagents.{mr.REBUILT_MAPS_FIELD}"]
    assert entry["corrected_row_ids"] == ["row1", "row2"]
    assert entry["rows_changed_this_run"] == 2
    assert entry["applied_at"] == "2026-09-25T08:35:57"
    assert entry["cutoff"] == "2026-09-25"
    assert written["subagents"]["corrected_rows"] == 3
    assert mr.load_backfill_state(path) == written


def test_a_later_token_pass_adds_its_rebuilt_maps_to_the_earlier_ones(tmp_path):
    path = str(tmp_path / "state.json")
    mr.write_backfill_state("2026-09-23", "a", _names(2), path=path, rebuilt_maps=["row0"])
    written = mr.write_backfill_state(
        "2026-09-25", "b", _names(3), path=path, rebuilt_maps=["row2"]
    )
    entry = written["fields"][f"sessions.{mr.REBUILT_MAPS_FIELD}"]
    assert entry["corrected_row_ids"] == ["row0", "row2"]
    assert entry["rows_changed_this_run"] == 1


def test_a_token_pass_that_rebuilt_no_map_leaves_the_fields_alone(tmp_path):
    path = str(tmp_path / "state.json")
    earlier = {"fields": {f"sessions.{mr.REBUILT_MAPS_FIELD}": {"applied_at": "kept"}}}
    Path(path).write_text(json.dumps(earlier))
    written = mr.write_backfill_state("2026-09-23", "b", _names(1), path=path, rebuilt_maps=[])
    assert written["fields"] == earlier["fields"]
    fresh = mr.write_backfill_state("2026-09-23", "b", _names(1), path=str(tmp_path / "other.json"))
    assert "fields" not in fresh


# Both state writers run AFTER the metrics file was replaced. A write that
# truncated the state and then failed would leave the dashboards' boundary
# note, and every recorded row name, gone - so the state is written to a
# temporary file beside it and renamed into place.


def _failing_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(source: Any, target: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)


@pytest.mark.parametrize("writer", ["write_backfill_state", "write_field_state"])
def test_a_state_write_whose_rename_fails_leaves_the_old_state_intact(
    tmp_path, monkeypatch, writer
):
    path = tmp_path / "state.json"
    mr.write_backfill_state("2026-09-23", "a", _names(2), path=str(path), rebuilt_maps=["row0"])
    before = path.read_bytes()
    _failing_replace(monkeypatch)

    with pytest.raises(OSError, match="disk full"):
        if writer == "write_backfill_state":
            mr.write_backfill_state("2026-09-25", "b", _names(5), path=str(path))
        else:
            mr.write_field_state("sessions", "tool_calls", None, "b", _names(5), path=str(path))

    assert path.read_bytes() == before
    assert sorted(child.name for child in tmp_path.iterdir()) == ["state.json"]


def test_a_state_write_keeps_the_state_files_mode(tmp_path):
    path = tmp_path / "state.json"
    mr.write_backfill_state("2026-09-23", "a", _names(1), path=str(path))
    path.chmod(0o644)
    mr.write_field_state("sessions", "tool_calls", None, "b", _names(1), path=str(path))
    mr.write_backfill_state("2026-09-25", "c", _names(1), path=str(path))
    assert path.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize(("umask", "expected"), [(0o022, 0o644), (0o077, 0o600)])
def test_a_new_state_file_follows_the_umask(tmp_path, umask, expected):
    # The plain open("w") this write replaced created the file 0o666 minus
    # the umask; a temporary file from mkstemp is 0o600 whatever it is.
    previous = os.umask(umask)
    try:
        mr.write_field_state(
            "sessions", "tool_calls", None, "a", _names(1), path=str(tmp_path / "s.json")
        )
    finally:
        os.umask(previous)
    assert (tmp_path / "s.json").stat().st_mode & 0o777 == expected


def test_a_state_write_through_a_symlink_replaces_the_target_and_keeps_the_link(tmp_path):
    # A state file kept elsewhere and linked into the metrics directory:
    # renaming over the link would turn it into a regular file and leave
    # the real one behind, stale.
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    real = real_directory / "state.json"
    mr.write_backfill_state("2026-09-23", "a", _names(1), path=str(real))
    link = tmp_path / "state.json"
    link.symlink_to(real)

    mr.write_backfill_state("2026-09-25", "b", _names(2), path=str(link))

    assert link.is_symlink()
    assert json.loads(real.read_text())["applied_at"] == "b"
    assert sorted(child.name for child in real_directory.iterdir()) == ["state.json"]
    assert sorted(child.name for child in tmp_path.iterdir()) == ["real", "state.json"]


def test_a_state_write_leaves_the_process_umask_as_it_found_it(tmp_path):
    # Reading the umask means setting it; a write that forgot to set it
    # back would leave every file the process creates afterwards 0o666.
    previous = os.umask(0o027)
    try:
        mr.write_field_state(
            "sessions", "tool_calls", None, "a", _names(1), path=str(tmp_path / "s.json")
        )
        after = os.umask(0o027)
    finally:
        os.umask(previous)
    assert after == 0o027


# A printed command names its program relative to the directory the
# printing run was started from, so it runs when pasted there - the repo
# root or anywhere else - and quoted by the caller, so spaces survive.


def _a_script(tmp_path: Path) -> Path:
    script = tmp_path / "repo with space" / "scripts" / "some-script.py"
    script.parent.mkdir(parents=True)
    script.write_text("")
    return script


def test_script_invocation_names_the_script_from_the_repository_root(tmp_path, monkeypatch):
    script = _a_script(tmp_path)
    monkeypatch.chdir(tmp_path / "repo with space")
    assert mr.script_invocation(str(script)) == (
        "python3",
        str(Path("scripts") / "some-script.py"),
    )


def test_script_invocation_names_the_script_from_anywhere_else(tmp_path, monkeypatch):
    script = _a_script(tmp_path)
    elsewhere = tmp_path / "some other dir"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    program, shown = mr.script_invocation(str(script))
    assert program == "python3"
    assert shown == str(Path("..") / "repo with space" / "scripts" / "some-script.py")
    assert (elsewhere / shown).resolve() == script.resolve()


def test_script_invocation_falls_back_to_the_absolute_path(tmp_path, monkeypatch):
    # os.path.relpath raises when no relative path exists - on Windows,
    # a script on another drive than the working directory.
    script = _a_script(tmp_path)

    def impossible(*arguments: Any) -> str:
        raise ValueError("path is on mount 'C:', start on mount 'D:'")

    monkeypatch.setattr(os.path, "relpath", impossible)
    assert mr.script_invocation(str(script)) == ("python3", str(script))


def test_script_invocation_survives_a_working_directory_that_was_removed(tmp_path, monkeypatch):
    # The dedup recovery hint is printed after the metrics file was
    # replaced; a working directory deleted mid-run must not turn it into
    # a traceback. relpath needs the cwd, so the absolute path stands in.
    script = _a_script(tmp_path)
    doomed = tmp_path / "removed while running"
    doomed.mkdir()
    monkeypatch.chdir(doomed)
    doomed.rmdir()
    assert mr.script_invocation(str(script)) == ("python3", str(script))


def test_script_invocation_of_a_relative_path_without_a_working_directory(monkeypatch):
    # With no cwd there is nothing to resolve a relative path against, so
    # it is printed as given.
    def gone() -> str:
        raise FileNotFoundError("the working directory was removed")

    monkeypatch.setattr(os, "getcwd", gone)
    relative = str(Path("scripts") / "some-script.py")
    assert mr.script_invocation(relative) == ("python3", relative)


def test_the_notice_is_silent_on_a_full_valid_state(tmp_path):
    # Every block a real state file has carried, with synthetic values:
    # the flat sessions keys (old count-only and current named), the
    # subagents entry, field entries with and without names, and the
    # rebuilt-maps entry. None of it may read as unreadable.
    state = {
        "cutoff": "2026-08-21",
        "applied_at": "2026-09-01T10:00:00",
        "corrected_rows": 5,
        "rows_changed_this_run": 2,
        "corrected_row_ids": ["row-a", "row-b"],
        "unidentified_earlier_rows": 3,
        "uncorrectable_rows": 4,
        "earliest_corrected": "2026-07-01T09:00:00",
        "subagents": {
            "cutoff": "2026-08-21",
            "applied_at": "2026-09-02T10:00:00",
            "corrected_rows": 1,
            "rows_changed_this_run": 1,
            "corrected_row_ids": ["agent-row-a"],
            "uncorrectable_rows": 0,
            "earliest_corrected": None,
        },
        "fields": {
            "sessions.tool_calls": {
                "cutoff": "2026-08-21",
                "applied_at": "2026-09-03T10:00:00",
                "corrected_rows": 7,
                "rows_changed_this_run": 1,
                "corrected_row_ids": ["row-c"],
                "unidentified_earlier_rows": 6,
            },
            "sessions.incomplete_usage": {
                "cutoff": "2026-08-21",
                "applied_at": "2026-09-04T10:00:00",
                "corrected_rows": 2,
            },
            "sessions.tokens_by_model": {
                "cutoff": None,
                "applied_at": "2026-09-05T10:00:00",
                "corrected_rows": 1,
                "rows_changed_this_run": 1,
                "corrected_row_ids": ["row-d"],
            },
            "subagents.tokens_by_model_rebuilt": {
                "cutoff": "2026-08-21",
                "applied_at": "2026-09-06T10:00:00",
                "corrected_rows": 1,
                "rows_changed_this_run": 1,
                "corrected_row_ids": ["agent-row-b"],
            },
        },
    }
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state))
    for apply in (True, False):
        assert mr.unreadable_state_notice(str(path), apply=apply, dry_run_effect="x") is None
    assert mr.read_state_for_update(str(path)) == state
