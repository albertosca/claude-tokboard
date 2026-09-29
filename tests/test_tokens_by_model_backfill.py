"""Covers scripts/backfill-tokens-by-model.py.

DANGER: this script's whole purpose is rewriting ~/.claude/metrics/*.jsonl,
which holds thousands of irreplaceable records that other running Claude
sessions append to continuously. Every test here builds its own records and
transcripts under tmp_path and never touches a real metrics file.
"""

import argparse
import collections
import importlib.util
import json
import os
import shlex
import sys
import types
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill-tokens-by-model.py"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("tokens_by_model_backfill", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _transcript(path: Path, entries: Iterable[tuple[str, str, str, dict[str, int]]]) -> str:
    """entries: (timestamp, message_id, model, {usage field: value})"""
    lines = []
    for stamp, message_id, model, usage in entries:
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": stamp,
                    "message": {"id": message_id, "model": model, "usage": usage},
                }
            )
        )
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def _bucket(**over: int) -> dict[str, int]:
    base = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    base.update(over)
    return base


def test_plan_reconstructs_each_snapshot_as_of_its_own_moment(script, tmp_path):
    # Two snapshots of one session: the first must carry only what existed at
    # its own timestamp_end, not the session's final total. Getting this wrong
    # is what piles a whole session onto its first day.
    transcript_path = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-beta", {"input_tokens": 400}),
        ],
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
        },
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T12:00:00Z",
        },
    ]

    plan, _stats = script.plan_backfill(records, {"s1": transcript_path}, "transcript_path")

    assert plan[0] == {"model-alpha": _bucket(input_tokens=10)}
    assert plan[1] == {
        "model-alpha": _bucket(input_tokens=10),
        "model-beta": _bucket(input_tokens=400),
    }


def test_plan_skips_a_session_when_any_snapshot_has_no_transcript(script, tmp_path):
    # All-or-nothing per session: correcting some snapshots and leaving others
    # inflates the delta at the seam. The dedup backfill measured that as an
    # invented spike of billions of tokens on a single day.
    transcript_path = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
        },
        {"session_id": "s1", "transcript_path": None, "timestamp_end": "2026-08-01T12:00:00Z"},
    ]

    plan, stats = script.plan_backfill(records, {"s1": transcript_path}, "transcript_path")

    assert plan == {}
    assert sum(stats.values()) > 0


def test_plan_leaves_snapshots_that_already_have_the_field(script, tmp_path):
    transcript_path = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=10)},
        }
    ]

    plan, _stats = script.plan_backfill(records, {"s1": transcript_path}, "transcript_path")

    assert plan == {}, "a snapshot that already has the field is not rewritten"


def test_check_against_native_is_silent_when_the_reconstruction_agrees(script, tmp_path):
    transcript_path = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=10)},
        }
    ]

    report = script.check_against_native(records, "transcript_path")

    assert report.problems == []
    assert (report.exact, report.tolerated) == (1, 0)


def test_check_against_native_reports_a_disagreement(script, tmp_path):
    # The oracle: the field written natively by the hook is what the
    # reconstruction must reproduce. A mismatch means the reconstructor is
    # wrong, and nothing may be written anywhere.
    transcript_path = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=999)},
        }
    ]

    problems = script.check_against_native(records, "transcript_path").problems

    assert len(problems) == 1
    assert "s1" in problems[0]


def _native_row(
    transcript: str, native: dict[str, dict[str, int]], stamp: str = "2026-08-01T10:30:00Z"
) -> dict[str, Any]:
    return {
        "session_id": "s1",
        "transcript_path": transcript,
        "timestamp_end": stamp,
        "tokens_by_model": native,
    }


def _one_entry_transcript(tmp_path: Path) -> str:
    """A transcript worth exactly 100,000 input tokens to model-alpha."""
    return _transcript(
        tmp_path / "t.jsonl",
        [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 100_000})],
    )


def test_the_oracle_accepts_a_shortfall_inside_the_tolerance_and_counts_it_apart(script, tmp_path):
    # A record stores timestamp_end, never the line the hook summed to, so a
    # transcript whose timestamps run backwards against file order cannot be
    # cut exactly - and the residue is always a shortfall. 0.5% is accepted,
    # but it is NOT counted as exact: a run where the tolerated count jumps
    # is the only warning that the residue has stopped being residue.
    transcript_path = _one_entry_transcript(tmp_path)
    records = [_native_row(transcript_path, {"model-alpha": _bucket(input_tokens=100_500)})]

    report = script.check_against_native(records, "transcript_path")

    assert report.problems == []
    assert (report.exact, report.tolerated) == (0, 1)


def test_the_oracle_reports_a_shortfall_past_the_tolerance(script, tmp_path):
    # 2% low. Same direction as the accepted case, an order of magnitude
    # bigger, and the floor is what separates them.
    transcript_path = _one_entry_transcript(tmp_path)
    records = [_native_row(transcript_path, {"model-alpha": _bucket(input_tokens=102_000)})]

    report = script.check_against_native(records, "transcript_path")

    assert len(report.problems) == 1
    assert "past the 1% floor" in report.problems[0]
    assert (report.exact, report.tolerated) == (0, 0)


def test_the_oracle_reports_an_over_count_however_small(script, tmp_path):
    # THE test the asymmetry exists for. 0.01% HIGH - two hundred times
    # smaller than the shortfall accepted above, and refused anyway, because
    # this repo's worst backfill outcome was inventing billions of tokens and
    # a fabricated bar is loud where a sub-1% shortfall is invisible. If this
    # test ever passes with a symmetric comparison, the tolerance has quietly
    # become "close enough" in both directions.
    transcript_path = _one_entry_transcript(tmp_path)
    records = [_native_row(transcript_path, {"model-alpha": _bucket(input_tokens=99_990)})]

    report = script.check_against_native(records, "transcript_path")

    assert len(report.problems) == 1
    assert "ABOVE native" in report.problems[0]
    assert "never tolerated" in report.problems[0]
    assert (report.exact, report.tolerated) == (0, 0)


def test_the_oracle_reports_a_model_bucket_that_exists_on_only_one_side(script, tmp_path):
    # A missing or invented series is not a rounding difference, so it is
    # reported in both directions and never tolerated - even when the numbers
    # attached to it are tiny enough to sit inside the 1% floor.
    transcript_path = _one_entry_transcript(tmp_path)
    missing_from_reconstruction = [
        _native_row(
            transcript_path,
            {
                "model-alpha": _bucket(input_tokens=100_000),
                "model-ghost": _bucket(input_tokens=1),
            },
        )
    ]
    invented_by_reconstruction = [
        _native_row(transcript_path, {}),
    ]

    absent = script.check_against_native(missing_from_reconstruction, "transcript_path")
    extra = script.check_against_native(invented_by_reconstruction, "transcript_path")

    assert len(absent.problems) == 1
    assert "only in the native map ['model-ghost']" in absent.problems[0]
    assert len(extra.problems) == 1
    assert "only in the reconstruction ['model-alpha']" in extra.problems[0]
    assert (absent.tolerated, extra.tolerated) == (0, 0)


def test_the_oracle_follows_a_transcript_that_moved_between_project_slugs(script, tmp_path):
    # Measured on real data 2026-09-16: both snapshots that first exposed the
    # tolerance question had their transcripts migrate out of a worktree
    # project slug within the hour, leaving the recorded path dead. Without
    # the by-id index the oracle stops checking exactly those rows and
    # reports a pass it has not earned - a green that means "I looked away",
    # which is worse than a red.
    moved = _one_entry_transcript(tmp_path)
    dead_path = str(tmp_path / "old-slug" / "t.jsonl")
    records = [_native_row(dead_path, {"model-alpha": _bucket(input_tokens=1)})]

    assert script.checkable_native_rows(records, "transcript_path") == []

    with_index = script.checkable_native_rows(records, "transcript_path", {"s1": moved})

    assert with_index == [(0, moved)]
    assert len(script.check_against_native(records, "transcript_path", {"s1": moved}).problems) == 1


def _subagent_tree(projects: Path, slug: str, session_id: str, agent_id: str) -> str:
    """A subagent transcript at the real path shape AND the real filename.

    Both halves are load-bearing. The indexer globs
    <projects>/<slug>/<session_id>/subagents/, and the file inside is named
    `agent-<agent_id>.jsonl` while the record's own agent_id has no prefix -
    checked over a live tree, every file carries it and no id does. An
    earlier version of this helper named the file after
    the bare id, which made every test here pass against an indexer that
    could not match a single real row.
    """
    directory = projects / slug / session_id / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    return _transcript(
        directory / f"agent-{agent_id}.jsonl",
        [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 100_000})],
    )


def test_the_oracle_reaches_a_subagent_transcript_through_the_agent_id_index(script, tmp_path):
    # The gap the session-side fix did NOT close. index_transcripts globs
    # <slug>/*.jsonl and keys by session_id; a subagent transcript lives a
    # level deeper and is keyed by agent_id, so that index can never hit for
    # --file subagents. Found 2026-09-16: native subagent snapshots were
    # reported as having no transcript while every one of their files was on disk.
    #
    # This test builds the directory shape the indexer actually walks. Handing
    # a dict in by hand - as the session test above does - would pass whether
    # or not the glob is right, which is exactly why the gap survived.
    projects = tmp_path / "projects"
    moved = _subagent_tree(projects, "parent-slug", "sess-1", "abc123")
    records = [
        {
            # Bare in the record; prefixed on disk. This is the real pairing,
            # and it is what a hand-built index cannot exercise.
            "agent_id": "abc123",
            # The path the record recorded, in the worktree slug that has
            # since been cleaned up. The file is NOT there.
            "agent_transcript_path": str(
                projects / "worktree-slug" / "sess-1" / "subagents" / "agent-abc123.jsonl"
            ),
            "timestamp_end": "2026-08-01T10:30:00Z",
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=100_000)},
        }
    ]

    index, ambiguous = script.index_subagent_transcripts(str(projects))

    assert index == {"abc123": moved}, "the index keys on the id the RECORD carries"
    assert ambiguous == []

    without = script.classify_native_rows(records, "agent_transcript_path")
    with_index = script.classify_native_rows(records, "agent_transcript_path", index)

    assert (without.checkable, without.unreachable) == ([], [0])
    assert with_index.checkable == [(0, moved)]
    report = script.check_against_native(records, "agent_transcript_path", index)
    assert (report.problems, report.exact, report.unverified) == ([], 1, 0)


def test_an_agent_id_under_two_project_slugs_is_ambiguity_not_a_match(script, tmp_path):
    # Two copies and no way to tell which one the record was parsed from.
    # Picking the first, or the larger, would be a guess presented as a
    # resolution - and the row would then be "checked" against a file that
    # may not be its own. It is left unindexed and named instead.
    # Copies that DIFFER: identical ones are one transcript stored twice and
    # are indexed (see metrics_core.index_subagent_transcripts).
    projects = tmp_path / "projects"
    _subagent_tree(projects, "slug-a", "sess-1", "dup")
    with Path(_subagent_tree(projects, "slug-b", "sess-1", "dup")).open("a") as transcript_file:
        transcript_file.write("\n")
    only_once = _subagent_tree(projects, "slug-a", "sess-1", "unique")

    index, ambiguous = script.index_subagent_transcripts(str(projects))

    assert index == {"unique": only_once}
    assert ambiguous == ["dup"]


def test_the_index_is_chosen_by_metrics_file_not_shared_between_them(script, tmp_path):
    # Sessions keep their transcript at <slug>/<session_id>.jsonl, subagents a
    # level deeper under subagents/. One indexer cannot serve both, and the
    # bug this closes was precisely a session-shaped index being handed to the
    # subagent half.
    projects = tmp_path / "projects"
    (projects / "slug").mkdir(parents=True)
    session_file = _transcript(
        projects / "slug" / "sess-1.jsonl",
        [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 1})],
    )
    agent_file = _subagent_tree(projects, "slug", "sess-1", "xyz")

    sessions_index, sessions_ambiguous = script.index_for_file("sessions", str(projects))
    subagents_index, _ = script.index_for_file("subagents", str(projects))

    assert sessions_index == {"sess-1": session_file}
    assert sessions_ambiguous == []
    assert subagents_index == {"xyz": agent_file}


def test_the_oracle_reports_a_bucket_with_a_field_the_reconstruction_lacks(script, tmp_path):
    # The comparison loop reads TOKEN_FIELDS and nothing else, so a native
    # bucket with a fifth key would agree on all four and differ in fact. The
    # whole-dict comparison this replaced caught that for free.
    transcript_path = _one_entry_transcript(tmp_path)
    native = {"model-alpha": {**_bucket(input_tokens=100_000), "thinking_tokens": 5}}
    records = [_native_row(transcript_path, native)]

    report = script.check_against_native(records, "transcript_path")

    assert len(report.problems) == 1
    assert "bucket fields differ" in report.problems[0]
    assert "thinking_tokens" in report.problems[0]
    assert (report.exact, report.tolerated) == (0, 0)


def test_a_snapshot_empty_on_both_sides_is_counted_as_vacuous_not_as_evidence(script, tmp_path):
    # It agrees, and it could not have done otherwise. Counting it as an
    # exact match pads the number an operator reads as "how much proof is
    # behind this write" with checks that cannot fail.
    empty = _transcript(tmp_path / "empty.jsonl", [])
    records = [_native_row(empty, {})]

    report = script.check_against_native(records, "transcript_path")

    assert report.problems == []
    assert (report.exact, report.tolerated, report.vacuous) == (0, 0, 1)


def test_a_native_row_the_gate_cannot_reach_is_counted_as_unverified(script, tmp_path):
    # Never absorbed into the green count, and the two reasons stay apart:
    # one row has no moment to reconstruct at, the other has no transcript
    # anywhere.
    transcript_path = _one_entry_transcript(tmp_path)
    records = [
        _native_row(transcript_path, {"model-alpha": _bucket(input_tokens=100_000)}),
        {
            "session_id": "s2",
            "transcript_path": transcript_path,
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=1)},
        },
        _native_row(str(tmp_path / "gone.jsonl"), {"model-alpha": _bucket(input_tokens=1)}),
    ]

    rows = script.classify_native_rows(records, "transcript_path")
    report = script.check_against_native(records, "transcript_path")

    assert (rows.undated, rows.unreachable) == ([1], [2])
    assert (report.exact, report.unverified) == (1, 2)


def test_resolve_transcript_relocates_a_moved_file_but_never_invents_one(script, tmp_path):
    # The by-id index exists because one session logged from two working
    # directories lands under two project slugs, so a recorded path can be
    # stale while the file is still on disk. That is a RELOCATION, and it is
    # allowed. A row that recorded no path at all is a different thing: it
    # never had a transcript, there is nothing to relocate, and letting the
    # index answer for it is what would let a unit be written half way - see
    # test_plan_skips_a_session_when_any_snapshot_has_no_transcript, which
    # passes an index that WOULD answer for the row with no path.
    moved = _transcript(
        tmp_path / "moved.jsonl", [("2026-08-01T10:00:00Z", "a1", "m", {"input_tokens": 1})]
    )
    stale = {"session_id": "s1", "transcript_path": str(tmp_path / "gone.jsonl")}

    assert script.resolve_transcript(stale, {"s1": moved}, "transcript_path", "s1") == moved
    assert script.resolve_transcript(stale, {}, "transcript_path", "s1") is None
    assert (
        script.resolve_transcript({"session_id": "s1"}, {"s1": moved}, "transcript_path", "s1")
        is None
    )


def test_plan_skips_a_unit_whose_snapshot_has_no_timestamp_end(script, tmp_path):
    # Without the moment a snapshot was taken there is no as-of to
    # reconstruct at, and the only other candidate - the transcript's final
    # total - is the exact mistake this backfill is shaped to avoid.
    transcript_path = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
        },
        {"session_id": "s1", "transcript_path": transcript_path, "timestamp_end": None},
    ]

    plan, stats = script.plan_backfill(records, {}, "transcript_path")

    assert plan == {}
    assert isinstance(stats, collections.Counter)
    assert stats == collections.Counter({"untouched: a snapshot has no timestamp_end": 2})


def test_plan_writes_the_pre_capture_half_of_a_unit_that_straddles_the_deploy(script, tmp_path):
    # The shape that makes the oracle possible in the first place: capture
    # went live mid-session, so this session's later snapshot already carries
    # the field and its earlier one does not. The earlier one is written, the
    # later one is left exactly as the hook wrote it, and the unit is NOT
    # disqualified by owning a native row.
    transcript_path = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-beta", {"input_tokens": 400}),
        ],
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
        },
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T12:00:00Z",
            "tokens_by_model": {
                "model-alpha": _bucket(input_tokens=10),
                "model-beta": _bucket(input_tokens=400),
            },
        },
    ]

    plan, stats = script.plan_backfill(records, {}, "transcript_path")

    assert set(plan) == {0}
    assert plan[0] == {"model-alpha": _bucket(input_tokens=10)}
    assert stats == collections.Counter(
        {"planned": 1, "untouched: snapshot already has the field": 1}
    )


def test_a_unit_whose_every_snapshot_is_native_needs_no_transcript_at_all(script, tmp_path):
    # Counted separately from a unit skipped for lack of a transcript:
    # nothing is missing here, there is simply nothing left to do.
    records = [
        {
            "session_id": "s1",
            "transcript_path": str(tmp_path / "gone.jsonl"),
            "timestamp_end": "2026-08-01T10:30:00Z",
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=10)},
        }
    ]

    plan, stats = script.plan_backfill(records, {}, "transcript_path")

    assert plan == {}
    assert stats == collections.Counter({"untouched: every snapshot already has the field": 1})


def test_check_against_native_stays_silent_about_rows_it_cannot_check(script, tmp_path):
    # A native row that lost its transcript, and one that never recorded when
    # it was taken, are not disagreements - there is nothing to compare them
    # against. Reporting them would make the gate cry wolf on every run.
    transcript_path = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": str(tmp_path / "gone.jsonl"),
            "timestamp_end": "2026-08-01T10:30:00Z",
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=999)},
        },
        {
            "session_id": "s2",
            "transcript_path": transcript_path,
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=999)},
        },
    ]

    assert script.checkable_native_rows(records, "transcript_path") == []
    assert script.check_against_native(records, "transcript_path").problems == []


def test_a_transcript_shared_by_many_snapshots_is_scanned_once(script, tmp_path, monkeypatch):
    # A single session owns dozens of snapshots against one transcript of
    # thousands of entries. Re-reading the file per snapshot is what turns a
    # read-only check into a slow one, so the cache is load-bearing - and a
    # cache nobody counts is one that can silently stop working.
    transcript_path = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    native = {"model-alpha": _bucket(input_tokens=10)}
    stamps = [f"2026-08-01T1{hour}:00:00Z" for hour in (1, 2, 3)]
    scans: list[str] = []
    real_scan = script.scan_unit

    def counting_scan(path: str) -> Any:
        scans.append(path)
        return real_scan(path)

    monkeypatch.setattr(script, "scan_unit", counting_scan)

    native_rows = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": stamp,
            "tokens_by_model": native,
        }
        for stamp in stamps
    ]
    assert script.check_against_native(native_rows, "transcript_path").problems == []
    assert scans == [transcript_path]

    pending_rows = [
        {"session_id": "s1", "transcript_path": transcript_path, "timestamp_end": stamp}
        for stamp in stamps
    ]
    plan, _stats = script.plan_backfill(pending_rows, {}, "transcript_path")

    assert len(plan) == 3
    assert scans == [transcript_path, transcript_path]


def _lines(path: Path, lines: Iterable[dict[str, Any]]) -> str:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return str(path)


def _entry(stamp: str, message_id: str, model: str, input_tokens: int) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": stamp,
        "message": {"id": message_id, "model": model, "usage": {"input_tokens": input_tokens}},
    }


# The hook stopped reading at the queue-operation line; the model-beta entry
# is stamped before it but was written below it, so the hook never read it.
# Found 2026-09-29: native rows refused by the oracle as over-counts (or
# an extra bucket) all matched exactly at the cut their tokens reproduce.
LATE_WRITTEN = [
    _entry("2026-08-01T10:00:00Z", "a1", "model-alpha", 100),
    {"type": "queue-operation", "timestamp": "2026-08-01T10:00:05Z"},
    _entry("2026-08-01T10:00:03Z", "a2", "model-beta", 50),
]


def _row(transcript_path: str, **extra: Any) -> dict[str, Any]:
    row = {
        "session_id": "s1",
        "transcript_path": transcript_path,
        "timestamp_end": "2026-08-01T10:00:05Z",
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    row.update(extra)
    return row


def test_the_oracle_rebuilds_at_the_cut_the_rows_tokens_say_the_hook_made(script, tmp_path):
    transcript_path = _lines(tmp_path / "t.jsonl", LATE_WRITTEN)
    records = [_row(transcript_path, tokens_by_model={"model-alpha": _bucket(input_tokens=100)})]

    report = script.check_against_native(records, "transcript_path")

    assert report.problems == []
    assert report.exact == 1


def test_the_plan_writes_the_map_at_the_cut_the_rows_tokens_say_the_hook_made(script, tmp_path):
    transcript_path = _lines(tmp_path / "t.jsonl", LATE_WRITTEN)

    plan, _stats = script.plan_backfill([_row(transcript_path)], {}, "transcript_path")

    assert plan == {0: {"model-alpha": _bucket(input_tokens=100)}}


def test_file_cuts_whose_maps_disagree_fall_back_to_the_time_cut(script, tmp_path):
    # Both cuts at 10:00:05 reproduce the tokens (model-beta's entry adds
    # none), but only the second has a model-beta bucket: nothing tells
    # which the hook read, so the time cut is used and the oracle judges it.
    transcript_path = _lines(
        tmp_path / "t.jsonl",
        [
            _entry("2026-08-01T10:00:00Z", "a1", "model-alpha", 100),
            {"type": "attachment", "timestamp": "2026-08-01T10:00:05Z"},
            _entry("2026-08-01T10:00:00Z", "a2", "model-beta", 0),
            {"type": "attachment", "timestamp": "2026-08-01T10:00:05Z"},
        ],
    )
    records = [_row(transcript_path, tokens_by_model={"model-alpha": _bucket(input_tokens=100)})]

    report = script.check_against_native(records, "transcript_path")

    assert len(report.problems) == 1
    assert "only in the reconstruction ['model-beta']" in report.problems[0]


def test_plan_summary_of_an_empty_plan_over_a_file_with_no_tokens(script, capsys):
    # The degenerate end of the report: nothing to write, and no volume to
    # take a share of. The percentage must not divide by zero on the way to
    # saying so.
    script.print_plan_summary(
        [{"session_id": "s1"}],
        {},
        collections.Counter(),
        "session_id",
        script.Refusals(set(), set()),
    )

    out = capsys.readouterr().out
    assert "Units planned: 0 of 1" in out
    assert "Rows to write: 0 of 1" in out
    assert "Token volume covered: 0 of 0 (-)" in out
    assert "Earliest snapshot" not in out
    assert "declined by the reconciliation gate" not in out


# --- the second gate: every planned row against its own four totals ---------
#
# The oracle can only sample rows that already carry a native map, and all of
# those were written on deploy day, after the transcript-entry dedup was live.
# The rows this run would WRITE are historical and many store PRE-DEDUP,
# duplicate-inflated totals, so the oracle stays green while the run writes a
# map that contradicts the numbers beside it. When this gate was added, about
# a quarter of the planned subagent rows came out more than 1% short, and a
# few session rows came out ABOVE their own totals.


def _planned_unit(
    tmp_path: Path, transcript_tokens: int, *, totals: int, unit: str = "s1"
) -> tuple[list[dict[str, Any]], str]:
    """One session with one plannable snapshot: a transcript worth
    `transcript_tokens` and a row whose stored total says `totals`."""
    transcript_path = _transcript(
        tmp_path / f"{unit}.jsonl",
        [
            (
                "2026-08-01T10:00:00Z",
                f"{unit}-a1",
                "model-alpha",
                {"input_tokens": transcript_tokens},
            )
        ],
    )
    return [
        {
            "session_id": unit,
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
            "input_tokens": totals,
        }
    ], transcript_path


def _refuse(
    script: types.ModuleType, records: list[dict[str, Any]]
) -> tuple[dict[int, Any], Any, Any]:
    plan, stats = script.plan_backfill(records, {}, "transcript_path")
    plan, refusals = script.refuse_unwritable_units(records, plan, stats, "session_id")
    return plan, stats, refusals


def test_a_unit_whose_map_falls_short_of_its_own_totals_is_skipped_and_counted(script, tmp_path):
    # The defect the oracle is structurally blind to: the stored total is a
    # pre-dedup sum that counted a replayed turn twice, the reconstruction
    # dedupes, and the map lands 90% under the number printed beside it.
    records, _ = _planned_unit(tmp_path, 10_000, totals=100_000)

    plan, stats, refusals = _refuse(script, records)

    assert plan == {}, "a row whose map does not reach its totals is not written"
    assert refusals.unreconciled == {"s1"}
    assert refusals.nonmonotonic == set()
    assert stats == collections.Counter({script.RECONCILE_REASON: 1})
    assert "planned" not in stats, "the disposition table never prints a reason counting zero"


def test_a_unit_whose_map_exceeds_its_own_totals_is_skipped_at_any_magnitude(script, tmp_path):
    # One token above, which no tolerance covers in either gate: an as-of cut
    # can miss entries the totals counted, so a map that comes out ABOVE them
    # is not residue, and a fabricated bar is the failure this repo has
    # already paid billions of invented tokens for.
    records, _ = _planned_unit(tmp_path, 10_001, totals=10_000)

    plan, stats, refusals = _refuse(script, records)

    assert plan == {}
    assert refusals.unreconciled == {"s1"}
    assert stats == collections.Counter({script.RECONCILE_REASON: 1})


def test_a_unit_whose_map_reconciles_exactly_is_written_untouched(script, tmp_path):
    records, _ = _planned_unit(tmp_path, 10_000, totals=10_000)

    plan, stats, refusals = _refuse(script, records)

    assert plan == {0: {"model-alpha": _bucket(input_tokens=10_000)}}
    assert (refusals.unreconciled, refusals.nonmonotonic) == (set(), set())
    assert stats == collections.Counter({"planned": 1})


def test_a_shortfall_inside_the_tolerance_is_written_like_the_oracle_accepts_it(script, tmp_path):
    # 0.5% under, the structural residue of cutting by timestamp_end on a
    # transcript whose timestamps run backwards against file order. The same
    # bound the oracle uses, for the same reason.
    records, _ = _planned_unit(tmp_path, 99_500, totals=100_000)

    plan, _stats, refusals = _refuse(script, records)

    assert set(plan) == {0}
    assert refusals.unreconciled == set()


def test_a_unit_whose_per_model_series_would_go_down_is_skipped_and_counted(script, tmp_path):
    # expand_records_to_daily_deltas orders a unit's snapshots by
    # duration_seconds, not by timestamp_end, and floors each per-model delta
    # at zero. Here the two orders disagree, so the map differenced in
    # duration order falls from 410 to 10 - usage silently dropped, and a
    # phantom day the next time the series climbs back.
    transcript_path = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-alpha", {"input_tokens": 400}),
        ],
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T12:00:00Z",
            "duration_seconds": 100,
            "input_tokens": 410,
        },
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
            "duration_seconds": 200,
            "input_tokens": 10,
        },
    ]

    plan, stats, refusals = _refuse(script, records)

    assert plan == {}, "both rows reconcile with their own totals; the SERIES is what refuses"
    assert refusals.nonmonotonic == {"s1"}
    assert refusals.unreconciled == set()
    assert stats == collections.Counter({script.MONOTONIC_REASON: 2})


def test_a_model_missing_from_a_later_snapshot_is_not_a_decrease(script, tmp_path):
    # The expander keeps the last value it saw for a model rather than
    # differencing against zero, so an omission produces no delta at all and
    # nothing can be floored away. Refusing it would decline units over a
    # regression that cannot happen.
    records = [
        {"session_id": "s1", "duration_seconds": 100},
        {"session_id": "s1", "duration_seconds": 200},
    ]
    maps = {
        0: {"model-alpha": _bucket(input_tokens=10), "model-beta": _bucket(input_tokens=5)},
        1: {"model-alpha": _bucket(input_tokens=20)},
    }

    assert script.series_goes_down(records, [0, 1], maps) is False


def test_one_refused_unit_does_not_take_a_conforming_one_down_with_it(script, tmp_path):
    # Per unit, all-or-nothing - not per file. Declining the units whose
    # stored totals are inflated is the point; abandoning the good ones with
    # them would be the same over-reaction the oracle's abort is reserved for.
    good, _ = _planned_unit(tmp_path, 10_000, totals=10_000, unit="good")
    bad, _ = _planned_unit(tmp_path, 10_000, totals=100_000, unit="bad")
    records = good + bad

    plan, stats, refusals = _refuse(script, records)

    assert set(plan) == {0}
    assert refusals.unreconciled == {"bad"}
    assert stats == collections.Counter({"planned": 1, script.RECONCILE_REASON: 1})


def test_a_refused_units_native_rows_stay_counted_where_they_already_were(script, tmp_path):
    # The disposition table must keep summing to the row count. A refused
    # unit's PLANNED rows move to their reason; its native rows were already
    # counted as untouched and are untouched either way.
    transcript_path = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-alpha", {"input_tokens": 400}),
        ],
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T10:30:00Z",
            "input_tokens": 99_999,
        },
        {
            "session_id": "s1",
            "transcript_path": transcript_path,
            "timestamp_end": "2026-08-01T12:00:00Z",
            "input_tokens": 410,
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=410)},
        },
    ]

    plan, stats, _refusals = _refuse(script, records)

    assert plan == {}
    assert stats == collections.Counter(
        {script.RECONCILE_REASON: 1, "untouched: snapshot already has the field": 1}
    )
    assert sum(stats.values()) == len(records)


def test_the_summary_reports_the_declined_units_and_what_declining_them_costs(script, capsys):
    # Reported apart from the disposition table and apart from each other:
    # these are units the reconstruction got RIGHT and the stored data cannot
    # host, which is a different fact about the file than a missing
    # transcript.
    records = [
        {"session_id": "a", "input_tokens": 700},
        {"session_id": "b", "input_tokens": 300},
    ]

    script.print_plan_summary(
        records, {}, collections.Counter(), "session_id", script.Refusals({"a"}, {"b"})
    )

    out = capsys.readouterr().out
    assert (
        "Units declined by the reconciliation gate: 2 (1 whose map does not reconcile "
        "with their own totals, 1 whose per-model series goes down), holding 1,000 token(s)"
    ) in out


def _metrics_file(tmp_path: Path, name: str, records: list[dict[str, Any]]) -> Path:
    directory = tmp_path / "metrics"
    directory.mkdir(exist_ok=True)
    path = directory / f"{name}.jsonl"
    path.write_text("".join(json.dumps(record) + "\n" for record in records))
    return path


def _argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str) -> None:
    # Every main() test pins --metrics-dir and --projects-dir under tmp_path.
    # The defaults are the REAL ~/.claude/metrics and ~/.claude/projects, and
    # a test that forgot one would be reading months of irreplaceable history.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill-tokens-by-model.py",
            "--metrics-dir",
            str(tmp_path / "metrics"),
            "--projects-dir",
            str(tmp_path / "projects"),
            *extra,
        ],
    )


def test_main_reports_the_plan_when_every_reconstruction_agrees(
    script, tmp_path, monkeypatch, capsys
):
    transcript_path = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-beta", {"input_tokens": 400}),
        ],
    )
    _metrics_file(
        tmp_path,
        "sessions",
        [
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "input_tokens": 10,
            },
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T12:00:00Z",
                "input_tokens": 410,
                "tokens_by_model": {
                    "model-alpha": _bucket(input_tokens=10),
                    "model-beta": _bucket(input_tokens=400),
                },
            },
        ],
    )
    _argv(monkeypatch, tmp_path)

    exit_code = script.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert (
        "oracle: 1 natively written snapshots - 1 checked, "
        "0 with no transcript found anywhere, 0 with no timestamp_end" in out
    )
    assert (
        "oracle: 1 exact, 0 within the 1% downward tolerance, "
        "0 vacuous (empty on both sides), 0 disagreements, 0 unverified" in out
    )
    assert "Units planned: 1 of 1" in out
    assert "Rows to write: 1 of 2" in out
    assert "Token volume covered: 410 of 410 (100.0%)" in out
    assert "Earliest snapshot that would be written: 2026-08-01T10:30:00Z" in out
    # A bare run writes nothing, and hands the operator the exact invocation
    # that would - including the evidence count it just measured, so the
    # number in --expect-checked is one they read rather than one they guess.
    assert "DRY RUN - nothing written. To write: --file sessions --apply --expect-checked 1" in out
    assert not (tmp_path / "metrics" / "backups").exists()


def test_the_reported_volume_counts_only_what_would_actually_be_written(
    script, tmp_path, monkeypatch, capsys
):
    # The dry run used to count a refused unit's volume as covered, because
    # "covered" was read off the plan before the reconciliation gate narrowed
    # it - overstating both files by the volume of every declined unit.
    # Here the declined unit holds 99% of the file.
    good = _transcript(
        tmp_path / "good.jsonl",
        [("2026-08-01T10:00:00Z", "g1", "model-alpha", {"input_tokens": 10_000})],
    )
    bad = _transcript(
        tmp_path / "bad.jsonl",
        [("2026-08-01T10:00:00Z", "b1", "model-alpha", {"input_tokens": 10_000})],
    )
    _metrics_file(
        tmp_path,
        "sessions",
        [
            {
                "session_id": "good",
                "transcript_path": good,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "input_tokens": 10_000,
            },
            {
                "session_id": "bad",
                "transcript_path": bad,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "input_tokens": 1_000_000,
            },
        ],
    )
    _argv(monkeypatch, tmp_path)

    exit_code = script.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Units planned: 1 of 2" in out
    assert "Rows to write: 1 of 2" in out
    assert "Token volume covered: 10,000 of 1,010,000 (1.0%)" in out
    assert (
        "Units declined by the reconciliation gate: 1 (1 whose map does not reconcile "
        "with their own totals, 0 whose per-model series goes down), holding 1,000,000 "
        "token(s)"
    ) in out
    assert f"     1  {script.RECONCILE_REASON}" in out


def test_main_aborts_and_plans_nothing_when_a_reconstruction_disagrees(
    script, tmp_path, monkeypatch, capsys
):
    # The whole reason the read-only half runs first. The message carries
    # both values, because deciding WHICH of the two is wrong is the question
    # the oracle exists to raise, not one this script can answer.
    transcript_path = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    _metrics_file(
        tmp_path,
        "sessions",
        [
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "tokens_by_model": {"model-alpha": _bucket(input_tokens=999)},
            }
        ],
    )
    _argv(monkeypatch, tmp_path)

    exit_code = script.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "ORACLE FAILED (1 disagreements)" in captured.err
    assert "'s1'" in captured.err
    assert "999" in captured.err
    assert "Units planned" not in captured.out


def test_main_plans_the_subagent_file_through_the_same_mechanism(
    script, tmp_path, monkeypatch, capsys
):
    # Subagent rows key on agent_id/agent_transcript_path, and their
    # transcripts live where no session index would find them, so the file is
    # only reachable at all through the row's own recorded path.
    transcript_path = _transcript(
        tmp_path / "agent.jsonl",
        [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"output_tokens": 7})],
    )
    _metrics_file(
        tmp_path,
        "subagents",
        [
            {
                "agent_id": "ag1",
                "agent_transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "output_tokens": 7,
            }
        ],
    )
    _argv(monkeypatch, tmp_path, "--file", "subagents")

    exit_code = script.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "oracle: 0 natively written snapshots - 0 checked" in out
    assert "Units planned: 1 of 1" in out


def test_main_names_an_ambiguous_agent_id_instead_of_resolving_it(
    script, tmp_path, monkeypatch, capsys
):
    # The run says out loud which ids it refused to resolve, and the row that
    # depends on one stays unverified rather than being checked against a
    # file that might belong to a different agent. Silence here would look
    # identical to "the transcript is gone".
    projects = tmp_path / "projects"
    _subagent_tree(projects, "slug-a", "sess-1", "dup")
    with Path(_subagent_tree(projects, "slug-b", "sess-1", "dup")).open("a") as transcript_file:
        transcript_file.write("\n")  # differing copies: identical ones would be indexed
    _metrics_file(
        tmp_path,
        "subagents",
        [
            {
                "agent_id": "dup",
                "agent_transcript_path": str(tmp_path / "gone" / "agent-dup.jsonl"),
                "timestamp_end": "2026-08-01T10:30:00Z",
                "tokens_by_model": {"model-alpha": _bucket(input_tokens=100_000)},
            }
        ],
    )
    _argv(monkeypatch, tmp_path, "--file", "subagents")

    exit_code = script.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "indexed 0 subagent transcripts" in out
    assert "1 id(s) found under more than one project slug and left unindexed: ['dup']" in out
    assert "1 with no transcript found anywhere" in out
    assert "0 disagreements, 1 unverified" in out


def test_main_returns_one_when_there_is_no_metrics_file_at_the_given_path(
    script, tmp_path, monkeypatch, capsys
):
    _argv(monkeypatch, tmp_path)

    exit_code = script.main()

    assert exit_code == 1
    assert "no metrics file at" in capsys.readouterr().err


def test_module_guard_calls_main_and_exits_with_its_return_code(tmp_path, monkeypatch):
    # `if __name__ == "__main__": sys.exit(main())` is never exercised by
    # loading the module under any other name, including the `script`
    # fixture. Re-loading it with __name__ pre-set to "__main__" runs the
    # real file - pointed at a metrics directory under tmp_path that holds
    # nothing, so main() takes its first abort branch before reading
    # anything. It can never reach the real ~/.claude/metrics, because
    # --metrics-dir is always supplied.
    monkeypatch.setattr(
        sys,
        "argv",
        ["backfill-tokens-by-model.py", "--metrics-dir", str(tmp_path / "nowhere")],
    )

    spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 1


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_apply_writes_only_the_planned_key_and_preserves_everything_else(script, tmp_path):
    metrics = tmp_path / "sessions.jsonl"
    rows = [
        {"session_id": "s1", "input_tokens": 10, "tag": "keep-me"},
        {"session_id": "s2", "input_tokens": 20, "tag": "untouched"},
    ]
    _write_jsonl(metrics, rows)
    plan = {0: {"model-alpha": _bucket(input_tokens=10)}}

    script.apply_backfill(str(metrics), plan, rows)

    out = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert out[0]["tokens_by_model"] == {"model-alpha": _bucket(input_tokens=10)}
    assert out[0]["input_tokens"] == 10
    assert out[0]["tag"] == "keep-me"
    assert "tokens_by_model" not in out[1]
    assert out[1] == rows[1]


def test_apply_carries_over_lines_appended_while_it_ran(script, tmp_path):
    # The metrics file is append-only and live: another session's Stop hook
    # appends while this runs. Anything appended after the records were read
    # must survive verbatim.
    metrics = tmp_path / "sessions.jsonl"
    rows = [{"session_id": "s1", "input_tokens": 10}]
    _write_jsonl(metrics, rows)
    late = {"session_id": "s99", "input_tokens": 1}
    with metrics.open("a") as metrics_file:
        metrics_file.write(json.dumps(late) + "\n")

    script.apply_backfill(str(metrics), {0: {"model-alpha": _bucket(input_tokens=10)}}, rows)

    out = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert len(out) == 2
    assert out[1] == late, "the concurrently appended record must survive untouched"


def test_apply_refuses_when_the_prefix_changed_under_it(script, tmp_path):
    # If the rows we read are no longer the file's prefix, our plan's row
    # indexes point at different records - writing would corrupt them.
    metrics = tmp_path / "sessions.jsonl"
    _write_jsonl(metrics, [{"session_id": "s1", "input_tokens": 10}])
    stale = [{"session_id": "DIFFERENT", "input_tokens": 999}]

    with pytest.raises(SystemExit):
        script.apply_backfill(str(metrics), {0: {"m": _bucket()}}, stale)

    out = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert out[0]["session_id"] == "s1", "the file must be left untouched"


def _metrics_text(tmp_path: Path) -> str:
    return (tmp_path / "metrics" / "sessions.jsonl").read_text()


def _refused_to_write(tmp_path: Path) -> bool:
    """True when the row the plan would have written is still without the
    field. Asserting the STRING is absent from the whole file proves nothing
    here: the native snapshot the oracle checks carries it by construction,
    so that assertion passes whether or not the write happened."""
    first = json.loads(_metrics_text(tmp_path).splitlines()[0])
    return "tokens_by_model" not in first


def test_apply_refuses_when_a_prefix_line_is_no_longer_readable_as_a_record(
    script, tmp_path, capsys
):
    # load_records SKIPS a blank or malformed line, so one appearing inside
    # the prefix shifts every row index the plan holds by one. The prefix
    # check treats an unreadable line as a mismatch rather than parsing
    # around it, because writing through that shift is what would corrupt
    # rows the plan never named.
    metrics = tmp_path / "sessions.jsonl"
    row = {"session_id": "s1", "input_tokens": 10}
    metrics.write_text('{"session_id": "s1", "input_to\n' + json.dumps(row) + "\n")

    with pytest.raises(SystemExit):
        script.apply_backfill(str(metrics), {0: {"m": _bucket()}}, [row])

    assert "the file changed under us" in capsys.readouterr().err
    assert metrics.read_text().startswith('{"session_id": "s1", "input_to\n')


def test_apply_refuses_a_plan_that_names_a_row_the_records_do_not_have(script, tmp_path, capsys):
    # A plan built against a different read of the file would silently
    # address rows by the wrong index. Nothing downstream could tell.
    metrics = tmp_path / "sessions.jsonl"
    rows = [{"session_id": "s1", "input_tokens": 10}]
    _write_jsonl(metrics, rows)

    with pytest.raises(SystemExit):
        script.apply_backfill(str(metrics), {7: {"m": _bucket()}}, rows)

    assert "the plan names row(s) [7]" in capsys.readouterr().err
    assert json.loads(metrics.read_text()) == rows[0]
    assert not (tmp_path / "backups").exists(), "it must refuse before it copies anything"


def test_apply_refuses_when_the_backup_does_not_verify(script, tmp_path, monkeypatch, capsys):
    # An unverified backup is not a backup, and this is the only step that
    # makes the write reversible at all.
    metrics = tmp_path / "sessions.jsonl"
    rows = [{"session_id": "s1", "input_tokens": 10}]
    _write_jsonl(metrics, rows)
    monkeypatch.setattr(
        script, "sha256_of", lambda path: "backup-digest" if path.endswith(".bak") else "source"
    )

    with pytest.raises(SystemExit):
        script.apply_backfill(str(metrics), {0: {"m": _bucket()}}, rows)

    error_output = capsys.readouterr().err
    assert "BACKUP VERIFICATION FAILED" in error_output
    assert "backup-digest" in error_output
    assert json.loads(metrics.read_text()) == rows[0], "the file must be left untouched"


def test_apply_refuses_when_verification_sees_a_change_it_did_not_declare(script, tmp_path, capsys):
    # tokens_by_model is declared as an ADDITION only. A plan that would
    # overwrite a map already on the row is a change to a pre-existing
    # value, and the declaration does not cover it.
    metrics = tmp_path / "sessions.jsonl"
    rows = [
        {
            "session_id": "s1",
            "input_tokens": 10,
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=10)},
        }
    ]
    _write_jsonl(metrics, rows)

    with pytest.raises(SystemExit):
        script.apply_backfill(str(metrics), {0: {"model-beta": _bucket(input_tokens=99)}}, rows)

    error_output = capsys.readouterr().err
    assert "FIELD VERIFICATION FAILED (1 problems)" in error_output
    assert "tokens_by_model changed" in error_output
    assert json.loads(metrics.read_text()) == rows[0], "the file must be left untouched"


def test_apply_leaves_no_temporary_file_behind_when_the_rename_fails(script, tmp_path, monkeypatch):
    # The temporary lives beside the target, in the same directory the
    # metrics files and their backups live in. One left behind after a
    # failed run is litter in a directory whose contents are read by name.
    metrics = tmp_path / "sessions.jsonl"
    rows = [{"session_id": "s1", "input_tokens": 10}]
    _write_jsonl(metrics, rows)

    def boom(source: Any, destination: Any) -> None:
        raise RuntimeError("rename failed")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(RuntimeError, match="rename failed"):
        script.apply_backfill(str(metrics), {0: {"m": _bucket()}}, rows)

    assert json.loads(metrics.read_text()) == rows[0], "the file must be left untouched"
    assert list(tmp_path.glob(".sessions.jsonl.*")) == []


def test_apply_honours_an_explicit_backup_directory(script, tmp_path):
    # --backup-dir exists so the backup can be sent somewhere other than
    # beside an irreplaceable file; the copy must still be a real one.
    metrics = tmp_path / "sessions.jsonl"
    rows = [{"session_id": "s1", "input_tokens": 10}]
    _write_jsonl(metrics, rows)
    elsewhere = tmp_path / "somewhere" / "else"
    before = metrics.read_bytes()

    script.apply_backfill(str(metrics), {0: {"m": _bucket()}}, rows, str(elsewhere))

    backups = list(elsewhere.glob("sessions.jsonl.tokens-by-model-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == before, "the backup is the file as it was, not as it became"


def _applying_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *extra: str) -> None:
    _argv(monkeypatch, tmp_path, "--backup-dir", str(tmp_path / "backups"), *extra)


def _one_writable_session(tmp_path: Path) -> None:
    """A session whose first snapshot needs the field and whose second
    already carries it - so the oracle has exactly one row to verify and the
    plan has exactly one row to write."""
    transcript_path = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-beta", {"input_tokens": 400}),
        ],
    )
    _metrics_file(
        tmp_path,
        "sessions",
        [
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "input_tokens": 10,
            },
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T12:00:00Z",
                "input_tokens": 410,
                "tokens_by_model": {
                    "model-alpha": _bucket(input_tokens=10),
                    "model-beta": _bucket(input_tokens=400),
                },
            },
        ],
    )


def test_apply_writes_the_conforming_unit_and_leaves_the_refused_one_with_no_map(
    script, tmp_path, monkeypatch, capsys
):
    # The outcome the whole reconciliation gate exists for, checked on the
    # bytes that reach disk rather than on the plan: a row whose map would
    # contradict the totals printed beside it must come out of --apply with
    # no map at all, which is how this schema spells "unknown".
    _one_writable_session(tmp_path)
    metrics = tmp_path / "metrics" / "sessions.jsonl"
    inflated = _transcript(
        tmp_path / "bad.jsonl",
        [("2026-08-01T10:00:00Z", "b1", "model-alpha", {"input_tokens": 1_000})],
    )
    with metrics.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "session_id": "bad",
                    "transcript_path": inflated,
                    "timestamp_end": "2026-08-01T10:30:00Z",
                    "input_tokens": 500_000,
                }
            )
            + "\n"
        )
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")

    exit_code = script.main()

    assert exit_code == 0
    written = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert written[0]["tokens_by_model"] == {"model-alpha": _bucket(input_tokens=10)}
    assert "tokens_by_model" not in written[2], "the inflated row must be left alone"
    assert written[2]["input_tokens"] == 500_000, "and its totals must be untouched"
    assert "Rows to write: 1 of 3" in capsys.readouterr().out


def test_main_refuses_to_apply_without_an_expected_evidence_count(
    script, tmp_path, monkeypatch, capsys
):
    # The floor is not a formality. This gate has twice reported green while
    # measuring less than anyone believed, and both runs would have exited 0
    # - so the operator has to state, in the invocation, how much evidence
    # the dry run they just read actually held.
    _one_writable_session(tmp_path)
    _applying_argv(monkeypatch, tmp_path, "--apply")

    exit_code = script.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--apply requires --expect-checked N" in captured.err
    assert _refused_to_write(tmp_path)


def test_main_refuses_to_apply_when_the_oracle_verified_nothing(
    script, tmp_path, monkeypatch, capsys
):
    # Zero is refused whatever --expect-checked says, including the 0 that
    # would otherwise satisfy the comparison. A run with no native rows has
    # collected no evidence at all, and an empty index looks exactly like
    # this from the outside.
    transcript_path = _transcript(
        tmp_path / "t.jsonl",
        [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})],
    )
    _metrics_file(
        tmp_path,
        "sessions",
        [
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "input_tokens": 10,
            }
        ],
    )
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "0")

    exit_code = script.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "the oracle verified 0 snapshots" in captured.err
    assert _refused_to_write(tmp_path)


def test_main_refuses_to_apply_when_the_oracle_verified_fewer_than_expected(
    script, tmp_path, monkeypatch, capsys
):
    # The shape of the defect this exists for: the oracle still passes, it
    # has simply stopped reaching rows it used to reach, and its verdict
    # reads exactly as green as a full run's.
    _one_writable_session(tmp_path)
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "2")

    exit_code = script.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "the oracle verified 1 snapshot(s), fewer than the 2" in captured.err
    assert _refused_to_write(tmp_path)


def test_main_writes_when_the_oracle_cleared_the_expected_evidence_floor(
    script, tmp_path, monkeypatch, capsys
):
    _one_writable_session(tmp_path)
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")

    exit_code = script.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "backup verified" in out
    assert "tokens_by_model added to 1 row(s)" in out
    assert "2 line(s) (2 rewritten + 0 appended while running)" in out
    written = [json.loads(line) for line in _metrics_text(tmp_path).splitlines()]
    assert written[0]["tokens_by_model"] == {"model-alpha": _bucket(input_tokens=10)}
    assert written[0]["input_tokens"] == 10, "the totals the map explains are not touched"
    assert len(list((tmp_path / "backups").glob("*.bak"))) == 1


def test_main_with_apply_and_nothing_to_write_leaves_the_file_alone(
    script, tmp_path, monkeypatch, capsys
):
    # Every row already has the field: the oracle has something to verify,
    # the plan has nothing to do, and the right outcome is not to rewrite an
    # irreplaceable file to produce identical bytes.
    transcript_path = _one_entry_transcript(tmp_path)
    _metrics_file(
        tmp_path,
        "sessions",
        [_native_row(transcript_path, {"model-alpha": _bucket(input_tokens=100_000)})],
    )
    before = _metrics_text(tmp_path)
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")

    exit_code = script.main()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "nothing to write; leaving the file untouched" in out
    assert _metrics_text(tmp_path) == before
    assert not (tmp_path / "backups").exists(), "no write means no backup"


def test_apply_refuses_when_the_bytes_on_disk_are_not_the_bytes_it_wrote(
    script, tmp_path, monkeypatch, capsys
):
    # The last thing that can go wrong is the write itself. Counting lines
    # is not checking: canary 2 produced "1 line(s) (1 rewritten + 1
    # appended)" - a contradiction in one printed sentence - and still
    # exited 0. This compares the bytes, and says where the verified backup
    # is, because by this point the original is already gone.
    metrics = tmp_path / "sessions.jsonl"
    rows = [{"session_id": "s1", "input_tokens": 10}]
    _write_jsonl(metrics, rows)

    def hijack(self: Path, destination: Any) -> None:
        Path(destination).write_bytes(b'{"session_id": "something-else"}\n')

    monkeypatch.setattr(Path, "replace", hijack)

    with pytest.raises(SystemExit):
        script.apply_backfill(str(metrics), {0: {"m": _bucket()}}, rows)

    error_output = capsys.readouterr().err
    assert "POST-WRITE VERIFICATION FAILED" in error_output
    assert ".bak" in error_output, (
        "a run that has already renamed must name the backup it can fall back to"
    )


def test_apply_accepts_a_line_appended_in_the_instant_after_the_rename(
    script, tmp_path, monkeypatch
):
    # The post-write check is startswith, not equality, and this is the only
    # reason: the file is live, and a Stop hook that appends microseconds
    # after the rename must not be read as corruption.
    metrics = tmp_path / "sessions.jsonl"
    rows = [{"session_id": "s1", "input_tokens": 10}]
    _write_jsonl(metrics, rows)
    real_replace = Path.replace

    def append_right_after(self: Path, destination: Any) -> None:
        real_replace(self, destination)
        with Path(destination).open("ab") as destination_file:
            destination_file.write(b'{"session_id": "s-late"}\n')

    monkeypatch.setattr(Path, "replace", append_right_after)

    script.apply_backfill(str(metrics), {0: {"model-alpha": _bucket(input_tokens=10)}}, rows)

    out = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert out[0]["tokens_by_model"] == {"model-alpha": _bucket(input_tokens=10)}
    assert out[1] == {"session_id": "s-late"}


def test_apply_carries_a_line_appended_between_the_final_read_and_the_rename(
    script, tmp_path, monkeypatch
):
    # The window the 2026-09-23 review reproduced losing a line in for the
    # dedup backfill, which this script's write copied: the append lands on
    # the OLD file after the tail was read, just before the rename.
    metrics = tmp_path / "sessions.jsonl"
    rows = [{"session_id": "s1", "input_tokens": 10}]
    _write_jsonl(metrics, rows)
    real_replace = Path.replace

    def append_right_before(self: Path, destination: Any) -> None:
        with Path(destination).open("ab") as destination_file:
            destination_file.write(b'{"session_id": "s-between"}\n')
        real_replace(self, destination)

    monkeypatch.setattr(Path, "replace", append_right_before)

    script.apply_backfill(str(metrics), {0: {"model-alpha": _bucket(input_tokens=10)}}, rows)

    out = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert out[0]["tokens_by_model"] == {"model-alpha": _bucket(input_tokens=10)}
    assert out[1:] == [{"session_id": "s-between"}]


def _subagent_metrics(tmp_path: Path) -> None:
    """One subagent row that already carries a map the oracle confirms - so
    the subagents dry run has an evidence count, and a different one from
    the sessions run below."""
    transcript_path = _transcript(
        tmp_path / "agent.jsonl",
        [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"output_tokens": 7})],
    )
    _metrics_file(
        tmp_path,
        "subagents",
        [
            {
                "agent_id": "ag1",
                "agent_transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "output_tokens": 7,
                "tokens_by_model": {"model-alpha": _bucket(output_tokens=7)},
            }
        ],
    )


def _two_native_session_rows(tmp_path: Path) -> None:
    """A session with one row to write and TWO the oracle can verify, so the
    two files' evidence counts differ the way real ones can, and a command
    pasted from the wrong run is not caught by the floor."""
    transcript_path = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-beta", {"input_tokens": 400}),
        ],
    )
    both = {"model-alpha": _bucket(input_tokens=10), "model-beta": _bucket(input_tokens=400)}
    _metrics_file(
        tmp_path,
        "sessions",
        [
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "input_tokens": 10,
            },
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T10:45:00Z",
                "input_tokens": 10,
                "tokens_by_model": {"model-alpha": _bucket(input_tokens=10)},
            },
            {
                "session_id": "s1",
                "transcript_path": transcript_path,
                "timestamp_end": "2026-08-01T12:00:00Z",
                "input_tokens": 410,
                "tokens_by_model": both,
            },
        ],
    )


def test_the_dry_run_prints_the_file_it_actually_read(script, tmp_path, monkeypatch, capsys):
    # The printed command is meant to be pasted. Without --file, a subagents
    # dry run hands over a line that runs against sessions.jsonl, and the
    # hazard is ASYMMETRIC: the subagent file's smaller evidence count
    # clears the session file's floor and writes, while the reverse refuses
    # loudly. The direction that fails is the one that gets noticed.
    _two_native_session_rows(tmp_path)
    _subagent_metrics(tmp_path)

    _argv(monkeypatch, tmp_path)
    assert script.main() == 0
    sessions_line = capsys.readouterr().out

    _argv(monkeypatch, tmp_path, "--file", "subagents")
    assert script.main() == 0
    subagents_line = capsys.readouterr().out

    assert "To write: --file sessions --apply --expect-checked 2" in sessions_line
    assert "To write: --file subagents --apply --expect-checked 1" in subagents_line


# ---------------------------------------------------------------------------
# The oracle counts only NATIVE maps. A map an earlier by-model apply wrote
# was rebuilt by this same code, so re-checking it proves nothing - yet it
# carries the field exactly like a native one. After the 2026-09-21 and
# 2026-09-29 applies, about half of the session maps were the script's own
# output, and all of them counted as "exact" evidence toward
# --expect-checked. An apply now records the rows it wrote in the backfill
# state, and the oracle leaves them out.
# ---------------------------------------------------------------------------


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / "metrics" / "backfill_state.json"


def _recorded_ids(tmp_path: Path, kind: str = "sessions") -> list[str]:
    state = json.loads(_state_path(tmp_path).read_text())
    ids: list[str] = state["fields"][f"{kind}.tokens_by_model"]["corrected_row_ids"]
    return ids


def test_an_apply_records_the_rows_it_wrote_and_the_next_oracle_leaves_them_out(
    script, tmp_path, monkeypatch, capsys
):
    _one_writable_session(tmp_path)
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")
    assert script.main() == 0
    capsys.readouterr()
    records = [json.loads(line) for line in _metrics_text(tmp_path).splitlines()]
    assert _recorded_ids(tmp_path) == script.row_identities(records, [0], "session_id")

    _argv(monkeypatch, tmp_path)
    assert script.main() == 0
    out = capsys.readouterr().out
    # Both rows now carry a map, and only one of them is the hook's.
    assert "oracle: 1 natively written snapshots - 1 checked" in out
    assert "written by an earlier backfill: 1" in out
    assert "oracle: 1 exact," in out
    assert "--expect-checked 1" in out


def test_the_state_goes_beside_the_metrics_file_it_describes(script, tmp_path, monkeypatch):
    # Never the default ~/.claude/metrics state: a run pointed at a copy
    # must record into the copy's state.
    _one_writable_session(tmp_path)
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")
    assert script.main() == 0
    assert _state_path(tmp_path).exists()


def test_a_later_apply_keeps_the_rows_earlier_applies_recorded(
    script, tmp_path, monkeypatch, capsys
):
    _one_writable_session(tmp_path)
    earlier = ["0123456789abcdef"]
    _state_path(tmp_path).write_text(
        json.dumps(
            {
                "fields": {
                    "sessions.tokens_by_model": {
                        "cutoff": None,
                        "applied_at": "2026-09-21T10:30:42",
                        "corrected_rows": 1,
                        "rows_changed_this_run": 1,
                        "corrected_row_ids": earlier,
                    }
                }
            }
        )
    )
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")
    assert script.main() == 0
    assert set(earlier) < set(_recorded_ids(tmp_path))
    assert len(_recorded_ids(tmp_path)) == 2


def test_an_unreadable_state_entry_excludes_nothing_and_does_not_crash(
    script, tmp_path, monkeypatch, capsys
):
    _two_native_session_rows(tmp_path)
    shapes: list[Any] = [
        {"fields": []},
        {"fields": {"sessions.tokens_by_model": "yes"}},
        {"fields": {"sessions.tokens_by_model": {"corrected_row_ids": "abc"}}},
        {"fields": {"sessions.tokens_by_model": {"corrected_row_ids": [1, None]}}},
        [],
    ]
    for broken in shapes:
        _state_path(tmp_path).write_text(json.dumps(broken))
        _argv(monkeypatch, tmp_path)
        assert script.main() == 0
        out = capsys.readouterr().out
        assert "oracle: 2 natively written snapshots - 2 checked" in out, broken
        assert "written by an earlier backfill: 0" in out, broken


def _by_model_backup(tmp_path: Path, stamp: str, records: list[dict[str, Any]]) -> None:
    backups = tmp_path / "backups"
    backups.mkdir(exist_ok=True)
    (backups / f"sessions.jsonl.tokens-by-model-{stamp}.bak").write_text(
        "".join(json.dumps(record) + "\n" for record in records)
    )


def _backfilled_history(tmp_path: Path) -> list[dict[str, Any]]:
    """A live file whose rows 0 and 1 got their maps from two earlier
    by-model applies, each backed up just before it ran: the first wrote
    row 0, which is why it already carries its map in the second backup,
    and the second wrote row 1, which did not exist yet at the first. Row
    2 is native, and row 3 was appended after the last backup and never
    had a map."""
    _two_native_session_rows(tmp_path)
    live = [json.loads(line) for line in _metrics_text(tmp_path).splitlines()]
    no_map = {key: value for key, value in live[1].items() if key != "tokens_by_model"}
    without_first = {key: value for key, value in live[0].items() if key != "tokens_by_model"}
    live[0]["tokens_by_model"] = {"model-alpha": _bucket(input_tokens=10)}
    _by_model_backup(tmp_path, "2026-09-21-103042", [without_first])
    _by_model_backup(tmp_path, "2026-09-29-125049", [live[0], no_map, live[2]])
    tail = {**no_map, "timestamp_end": "2026-08-01T13:00:00Z"}
    _metrics_file(tmp_path, "sessions", [*live, tail])
    return [*live, tail]


def test_record_earlier_applies_derives_the_rows_from_the_by_model_backups(
    script, tmp_path, monkeypatch, capsys
):
    live = _backfilled_history(tmp_path)
    before = _metrics_text(tmp_path)

    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies")
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "2 row(s) written by earlier by-model applies" in out
    assert "DRY RUN" in out
    assert not _state_path(tmp_path).exists(), "a dry run records nothing"

    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 0
    capsys.readouterr()
    assert _recorded_ids(tmp_path) == sorted(script.row_identities(live, [0, 1], "session_id"))
    assert _metrics_text(tmp_path) == before, "recording never touches the metrics file"

    _argv(monkeypatch, tmp_path)
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "oracle: 1 natively written snapshots - 1 checked" in out
    assert "written by an earlier backfill: 2" in out


def test_record_earlier_applies_with_no_backups_records_nothing(
    script, tmp_path, monkeypatch, capsys
):
    _two_native_session_rows(tmp_path)
    (tmp_path / "backups").mkdir()
    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 0
    assert "0 row(s) written by earlier by-model applies" in capsys.readouterr().out
    assert not _state_path(tmp_path).exists()


def test_record_earlier_applies_refuses_a_backup_that_is_not_a_prefix_of_the_file(
    script, tmp_path, monkeypatch, capsys
):
    # Identities are positional among a unit's same-moment rows; a backup
    # whose rows are not the file's prefix names different rows.
    _backfilled_history(tmp_path)
    _by_model_backup(tmp_path, "2026-09-30-000000", [{"session_id": "other", "timestamp_end": "x"}])
    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 1
    assert "is not a prefix" in capsys.readouterr().err
    assert not _state_path(tmp_path).exists()


def test_a_state_write_that_fails_after_the_apply_warns_instead_of_raising(
    script, tmp_path, monkeypatch, capsys
):
    # The metrics file is already rewritten by then: a raise would turn a
    # finished apply into a traceback and a non-zero exit.
    _one_writable_session(tmp_path)

    def fail(*arguments: Any, **keywords: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(script, "write_field_state", fail)
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")

    assert script.main() == 0

    assert not _refused_to_write(tmp_path)
    warning = capsys.readouterr().err
    assert "the backfill state could not be recorded" in warning
    assert "disk full" in warning


# Every command the script prints is meant to be pasted. It is built from
# the paths this run actually used, shell-quoted, and each test below runs
# it - from a directory with a space in its name - rather than reading it.


def _printed_command(output: str, marker: str, stop: str = "\n") -> list[str]:
    """The words of the command printed after `marker`, as a shell reads
    them."""
    rest = output[output.index(marker) + len(marker) :]
    return shlex.split(rest[: rest.index(stop)] if stop in rest else rest)


def _run_printed(
    script: types.ModuleType, monkeypatch: pytest.MonkeyPatch, words: list[str]
) -> int:
    monkeypatch.setattr(sys, "argv", ["backfill-tokens-by-model.py", *words])
    result: int = script.main()
    return result


def _fail_state_write(script: types.ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*arguments: Any, **keywords: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(script, "write_field_state", fail)


def test_the_warning_names_a_recovery_that_records_the_rows(script, tmp_path, monkeypatch, capsys):
    base = tmp_path / "with space"
    base.mkdir()
    _one_writable_session(base)
    real_write = script.write_field_state
    _fail_state_write(script, monkeypatch)
    _applying_argv(monkeypatch, base, "--apply", "--expect-checked", "1")
    assert script.main() == 0
    warning = capsys.readouterr().err
    words = _printed_command(warning, "from the backup this run took: ")
    assert words[0] == "backfill-tokens-by-model.py"
    assert words[words.index("--metrics-dir") + 1] == str(base / "metrics")
    assert words[words.index("--backup-dir") + 1] == str(base / "backups")

    monkeypatch.setattr(script, "write_field_state", real_write)
    assert _run_printed(script, monkeypatch, words[1:]) == 0
    records = _live_records(base)
    assert _recorded_ids(base) == script.row_identities(records, [0], "session_id")


def test_the_recovery_command_names_no_backup_dir_the_run_did_not_get(
    script, tmp_path, monkeypatch, capsys
):
    _one_writable_session(tmp_path)
    _fail_state_write(script, monkeypatch)
    _argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")
    assert script.main() == 0
    words = _printed_command(capsys.readouterr().err, "from the backup this run took: ")
    assert "--backup-dir" not in words
    assert words[words.index("--metrics-dir") + 1] == str(tmp_path / "metrics")


def test_the_dry_run_prints_commands_aimed_at_the_files_it_read(
    script, tmp_path, monkeypatch, capsys
):
    # A dry run on a copy that printed a bare --file command would have the
    # operator paste one that runs against the real ~/.claude/metrics.
    base = tmp_path / "with space"
    base.mkdir()
    _backfilled_history(base)
    _applying_argv(monkeypatch, base, "--record-earlier-applies")
    assert script.main() == 0
    words = _printed_command(capsys.readouterr().out, "To record: ")
    assert words[words.index("--metrics-dir") + 1] == str(base / "metrics")
    assert words[words.index("--backup-dir") + 1] == str(base / "backups")
    assert _run_printed(script, monkeypatch, words) == 0
    assert len(_recorded_ids(base)) == 2

    _argv(monkeypatch, base)
    assert script.main() == 0
    words = _printed_command(capsys.readouterr().out, "To write: ", " (and read")
    assert words[:4] == ["--file", "sessions", "--apply", "--expect-checked"]
    assert words[words.index("--metrics-dir") + 1] == str(base / "metrics")
    assert words[words.index("--projects-dir") + 1] == str(base / "projects")


def test_an_explicit_metrics_file_and_state_file_carry_into_the_printed_command(
    script, tmp_path, monkeypatch, capsys
):
    # The dedup pass may run on a metrics file of any name, with its state
    # anywhere; the recording has to follow both, and find the backups by
    # the metrics file's own name.
    base = tmp_path / "with space"
    base.mkdir()
    live = _backfilled_history(base)
    renamed = base / "metrics" / "sessions copy.jsonl"
    (base / "metrics" / "sessions.jsonl").rename(renamed)
    for backup in (base / "backups").iterdir():
        backup.rename(backup.with_name(backup.name.replace("sessions.jsonl", renamed.name)))
    state = base / "state dir" / "state.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill-tokens-by-model.py",
            "--metrics-file",
            str(renamed),
            "--state-file",
            str(state),
            "--backup-dir",
            str(base / "backups"),
            "--record-earlier-applies",
        ],
    )
    assert script.main() == 0
    words = _printed_command(capsys.readouterr().out, "To record: ")
    assert words[words.index("--metrics-file") + 1] == str(renamed)
    assert words[words.index("--state-file") + 1] == str(state)

    assert _run_printed(script, monkeypatch, words) == 0
    entry = json.loads(state.read_text())["fields"]["sessions.tokens_by_model"]
    assert entry["corrected_row_ids"] == sorted(script.row_identities(live, [0, 1], "session_id"))
    assert not _state_path(base).exists(), "never the state beside the default metrics file"


def test_record_earlier_applies_run_again_records_nothing_new(
    script, tmp_path, monkeypatch, capsys
):
    # A second run finds the same rows in the same backups. Before, it
    # rewrote the entry anyway: a new applied_at, and rows_changed_this_run
    # set to the whole recorded set, as if it had just recorded them all.
    _backfilled_history(tmp_path)
    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 0
    capsys.readouterr()
    recorded = _state_path(tmp_path).read_bytes()

    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 0

    assert "nothing new to record" in capsys.readouterr().out
    assert _state_path(tmp_path).read_bytes() == recorded


def test_record_earlier_applies_records_only_the_rows_the_state_lacks(
    script, tmp_path, monkeypatch, capsys
):
    live = _backfilled_history(tmp_path)
    already = script.row_identities(live, [0], "session_id")
    _state_path(tmp_path).write_text(
        json.dumps(
            {
                "fields": {
                    "sessions.tokens_by_model": {
                        "cutoff": None,
                        "applied_at": "2026-09-29T14:07:40",
                        "corrected_rows": 1,
                        "rows_changed_this_run": 1,
                        "corrected_row_ids": already,
                    }
                }
            }
        )
    )

    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 0

    assert "1 of them not yet recorded" in capsys.readouterr().out
    entry = json.loads(_state_path(tmp_path).read_text())["fields"]["sessions.tokens_by_model"]
    assert entry["rows_changed_this_run"] == 1
    assert entry["corrected_rows"] == 2
    assert entry["corrected_row_ids"] == sorted(script.row_identities(live, [0, 1], "session_id"))


# ---------------------------------------------------------------------------
# Maps a dedup TOKEN pass rebuilt are not native either. The 2026-09-23 and
# 2026-09-25 subagent token passes rewrote tokens_by_model on existing
# rows, many outside the by-model set, and the oracle kept counting those
# as the hook's own. The dedup pass now names them in the state, and
# --record-earlier-applies derives the earlier ones from its backups.
# ---------------------------------------------------------------------------


def _rebuilt_state(tmp_path: Path, names: list[str]) -> None:
    _state_path(tmp_path).write_text(
        json.dumps({"fields": {"sessions.tokens_by_model_rebuilt": {"corrected_row_ids": names}}})
    )


def _live_records(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _metrics_text(tmp_path).splitlines()]


def test_the_oracle_leaves_out_the_maps_a_dedup_pass_rebuilt(script, tmp_path, monkeypatch, capsys):
    _two_native_session_rows(tmp_path)
    live = _live_records(tmp_path)
    _rebuilt_state(tmp_path, script.row_identities(live, [2], "session_id"))

    _argv(monkeypatch, tmp_path)
    assert script.main() == 0

    out = capsys.readouterr().out
    assert "oracle: 1 natively written snapshots - 1 checked" in out
    assert "oracle: maps rebuilt by a dedup pass: 1 " in out
    assert "written by an earlier backfill: 0" in out
    assert "--expect-checked 1" in out


def test_a_map_both_written_and_rebuilt_is_counted_once_as_written(script, tmp_path):
    _two_native_session_rows(tmp_path)
    live = _live_records(tmp_path)
    both = script.row_identities(live, [1], "session_id")
    state = {
        "fields": {
            "sessions.tokens_by_model": {"corrected_row_ids": both},
            "sessions.tokens_by_model_rebuilt": {"corrected_row_ids": both},
        }
    }
    written = script.backfilled_rows(live, "session_id", state, "sessions")
    rebuilt = script.rebuilt_rows(live, "session_id", state, "sessions")
    assert written == rebuilt == {1}

    report = script.check_against_native(live, "transcript_path", None, written, rebuilt)

    assert (report.backfilled, report.rebuilt) == (1, 0)


def _backup(tmp_path: Path, name: str, records: list[dict[str, Any]]) -> None:
    backups = tmp_path / "backups"
    backups.mkdir(exist_ok=True)
    (backups / name).write_text("".join(json.dumps(record) + "\n" for record in records))


def _without_map(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "tokens_by_model"}


def _rebuilt_history(tmp_path: Path) -> list[dict[str, Any]]:
    """Row 0's map was dropped by a dedup pass and written again by a
    by-model apply; row 1's native map was rebuilt by a dedup pass; row 2
    is native and untouched; row 3 was appended after every backup.

    Row 0 is what makes the NEXT state matter: diffed against the live
    file instead of the backup that followed it, its map reads as
    rebuilt, when the dedup pass removed it and a by-model apply wrote
    the one it has now."""
    _two_native_session_rows(tmp_path)
    live = _live_records(tmp_path)
    live[0]["tokens_by_model"] = {"model-alpha": _bucket(input_tokens=10)}
    tail = {**_without_map(live[1]), "timestamp_end": "2026-08-01T13:00:00Z"}
    _metrics_file(tmp_path, "sessions", [*live, tail])
    older = {"model-alpha": _bucket(input_tokens=7)}
    dropped = {"model-alpha": _bucket(input_tokens=99)}
    _backup(
        tmp_path,
        "sessions.jsonl.backfill-2026-09-23-213839.bak",
        [
            {**live[0], "tokens_by_model": dropped},
            {**live[1], "tokens_by_model": older},
            live[2],
        ],
    )
    _backup(
        tmp_path,
        "sessions.jsonl.tokens-by-model-2026-09-29-125049.bak",
        [_without_map(live[0]), live[1], live[2]],
    )
    _backup(tmp_path, "sessions.jsonl.backfill-2026-09-29-130000.bak", [*live, tail])
    return [*live, tail]


def test_record_earlier_applies_derives_the_rebuilt_maps_from_the_dedup_backups(
    script, tmp_path, monkeypatch, capsys
):
    live = _rebuilt_history(tmp_path)
    before = _metrics_text(tmp_path)

    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies")
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "1 row(s) whose map a dedup pass rebuilt, found in 2 dedup backup(s)" in out
    assert "1 row(s) written by earlier by-model applies" in out
    assert not _state_path(tmp_path).exists(), "a dry run records nothing"

    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 0
    capsys.readouterr()
    state = json.loads(_state_path(tmp_path).read_text())
    rebuilt = state["fields"]["sessions.tokens_by_model_rebuilt"]["corrected_row_ids"]
    assert rebuilt == script.row_identities(live, [1], "session_id")
    assert _recorded_ids(tmp_path) == script.row_identities(live, [0], "session_id")
    assert _metrics_text(tmp_path) == before, "recording never touches the metrics file"

    _argv(monkeypatch, tmp_path)
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "oracle: 1 natively written snapshots - 1 checked" in out
    assert "written by an earlier backfill: 1" in out
    assert "rebuilt by a dedup pass: 1 " in out

    recorded = _state_path(tmp_path).read_bytes()
    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 0
    assert "nothing new to record" in capsys.readouterr().out
    assert _state_path(tmp_path).read_bytes() == recorded


def test_record_earlier_applies_refuses_a_dedup_backup_its_next_state_does_not_extend(
    script, tmp_path, monkeypatch, capsys
):
    _rebuilt_history(tmp_path)
    _backup(
        tmp_path,
        "sessions.jsonl.backfill-2026-09-20-000000.bak",
        [{"session_id": "other", "timestamp_end": "x"}],
    )
    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    assert script.main() == 1
    assert "is not a prefix" in capsys.readouterr().err
    assert not _state_path(tmp_path).exists()


def test_a_default_path_spelled_differently_is_still_the_default(script, tmp_path, monkeypatch):
    # "~/.claude/metrics/" with a trailing slash, or reached through a
    # link, is the default directory; a command that names it is only
    # longer, but one that omits a directory that is NOT the default is
    # wrong, so the comparison is on the resolved paths.
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    projects = tmp_path / "projects"
    projects.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(metrics)
    monkeypatch.setattr(script, "METRICS_DIRECTORY", str(metrics))
    monkeypatch.setattr(script, "PROJECTS_DIRECTORY", str(projects))

    def options(metrics_directory: str, projects_directory: str) -> list[str]:
        arguments = argparse.Namespace(
            metrics_dir=metrics_directory,
            metrics_file=None,
            state_file=None,
            backup_dir=None,
            projects_dir=projects_directory,
        )
        found: list[str] = script.path_options(arguments, projects=True)
        return found

    assert options(f"{metrics}/", f"{projects}/") == []
    assert options(str(alias), str(projects)) == []
    assert options(str(tmp_path), str(projects)) == ["--metrics-dir", str(tmp_path)]


# ---------------------------------------------------------------------------
# A run given only --metrics-file reads and writes beside that file. Its
# state and its backups default there, never to --metrics-dir: that
# default is the real ~/.claude/metrics, and a run pointed at a copy must
# record into the copy. The stand-in below takes the real directory's place
# so a regression lands somewhere a test can see, not on real data.
# ---------------------------------------------------------------------------


def _beside_the_metrics_file(base: Path) -> tuple[Path, Path]:
    """_backfilled_history with its backups moved beside the metrics file,
    and a stand-in for the default metrics directory that must stay empty."""
    _backfilled_history(base)
    (base / "backups").rename(base / "metrics" / "backups")
    stand_in = base / "default metrics dir"
    stand_in.mkdir()
    return base / "metrics" / "sessions.jsonl", stand_in


def test_a_metrics_file_alone_finds_its_backups_and_state_beside_it(
    script, tmp_path, monkeypatch, capsys
):
    metrics, stand_in = _beside_the_metrics_file(tmp_path)
    live = _live_records(tmp_path)
    monkeypatch.setattr(script, "METRICS_DIRECTORY", str(stand_in))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill-tokens-by-model.py",
            "--metrics-file",
            str(metrics),
            "--record-earlier-applies",
            "--apply",
        ],
    )

    assert script.main() == 0

    assert "2 row(s) written by earlier by-model applies" in capsys.readouterr().out
    assert _recorded_ids(tmp_path) == sorted(script.row_identities(live, [0, 1], "session_id"))
    assert list(stand_in.iterdir()) == [], "nothing may land in the default directory"


def test_backups_found_by_a_metrics_file_name_with_glob_characters(
    script, tmp_path, monkeypatch, capsys
):
    # The name is matched literally: "copy[1]" is not a character class.
    # Both lookups are covered - the by-model backups, and the dedup
    # backups the rebuilt maps are found in.
    live = _rebuilt_history(tmp_path)
    metrics = tmp_path / "metrics" / "sessions.jsonl"
    renamed = metrics.with_name("copy[1].jsonl")
    metrics.rename(renamed)
    for backup in (tmp_path / "backups").iterdir():
        backup.rename(backup.with_name(backup.name.replace("sessions.jsonl", renamed.name)))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill-tokens-by-model.py",
            "--metrics-file",
            str(renamed),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--record-earlier-applies",
            "--apply",
        ],
    )

    assert script.main() == 0

    capsys.readouterr()
    fields = json.loads(_state_path(tmp_path).read_text())["fields"]
    written = fields["sessions.tokens_by_model"]["corrected_row_ids"]
    rebuilt = fields["sessions.tokens_by_model_rebuilt"]["corrected_row_ids"]
    assert written == script.row_identities(live, [0], "session_id")
    assert rebuilt == script.row_identities(live, [1], "session_id")


def test_a_backup_directory_that_does_not_exist_is_refused(script, tmp_path, monkeypatch, capsys):
    # A mistyped directory used to read as "0 found, nothing new to record"
    # and exit 0 - a recovery that reported success and recorded nothing.
    _backfilled_history(tmp_path)
    missing = tmp_path / "no such dir"
    for arguments in (["--record-earlier-applies"], ["--record-earlier-applies", "--apply"]):
        _argv(monkeypatch, tmp_path, "--backup-dir", str(missing), *arguments)
        assert script.main() == 1
        output = capsys.readouterr()
        assert f"no backup directory at {missing}" in output.err
        assert "nothing new to record" not in output.out
    assert not _state_path(tmp_path).exists()


# A backfill state that exists but cannot be read stops an apply before it
# writes anything: read as empty, the recording would replace every name
# the file held with this run's alone. A dry run says so and carries on.
UNREADABLE_STATES = {
    "malformed JSON": b"{not json",
    "a list at the top level": b"[1, 2]",
    "unreadable": b'{"fields": {}}',
}


def _make_state_unreadable(tmp_path: Path, shape: str) -> Path:
    path = _state_path(tmp_path)
    path.write_bytes(UNREADABLE_STATES[shape])
    if shape == "unreadable":
        path.chmod(0o000)
    return path


@pytest.mark.parametrize("shape", sorted(UNREADABLE_STATES))
def test_an_apply_refuses_a_state_it_cannot_read(script, tmp_path, monkeypatch, capsys, shape):
    _one_writable_session(tmp_path)
    metrics_before = _metrics_text(tmp_path)
    state = _make_state_unreadable(tmp_path, shape)
    _applying_argv(monkeypatch, tmp_path, "--apply", "--expect-checked", "1")
    try:
        assert script.main() == 1
    finally:
        state.chmod(0o644)
    error = capsys.readouterr().err
    assert f"refusing to apply: the backfill state at {state}" in error
    assert "move it aside or fix it" in error
    assert state.read_bytes() == UNREADABLE_STATES[shape]
    assert _metrics_text(tmp_path) == metrics_before
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("shape", sorted(UNREADABLE_STATES))
def test_record_earlier_applies_refuses_a_state_it_cannot_read(
    script, tmp_path, monkeypatch, capsys, shape
):
    _backfilled_history(tmp_path)
    state = _make_state_unreadable(tmp_path, shape)
    _applying_argv(monkeypatch, tmp_path, "--record-earlier-applies", "--apply")
    try:
        assert script.main() == 1
    finally:
        state.chmod(0o644)
    assert f"refusing to apply: the backfill state at {state}" in capsys.readouterr().err
    assert state.read_bytes() == UNREADABLE_STATES[shape]


@pytest.mark.parametrize("shape", sorted(UNREADABLE_STATES))
@pytest.mark.parametrize("record_earlier", [False, True])
def test_a_dry_run_warns_about_a_state_it_cannot_read(
    script, tmp_path, monkeypatch, capsys, shape, record_earlier
):
    _backfilled_history(tmp_path)
    state = _make_state_unreadable(tmp_path, shape)
    extra = ["--record-earlier-applies"] if record_earlier else []
    _applying_argv(monkeypatch, tmp_path, *extra)
    try:
        assert script.main() == 0
    finally:
        state.chmod(0o644)
    error = capsys.readouterr().err
    assert f"WARNING: the backfill state at {state}" in error
    assert "--apply will refuse" in error
    assert state.read_bytes() == UNREADABLE_STATES[shape]
