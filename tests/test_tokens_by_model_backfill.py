"""Covers scripts/backfill-tokens-by-model.py.

DANGER: this script's whole purpose is rewriting ~/.claude/metrics/*.jsonl,
which holds thousands of irreplaceable records that other running Claude
sessions append to continuously. Every test here builds its own records and
transcripts under tmp_path and never touches a real metrics file.
"""

import collections
import importlib.util
import json
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
    t = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-beta", {"input_tokens": 400}),
        ],
    )
    records = [
        {"session_id": "s1", "transcript_path": t, "timestamp_end": "2026-08-01T10:30:00Z"},
        {"session_id": "s1", "transcript_path": t, "timestamp_end": "2026-08-01T12:00:00Z"},
    ]

    plan, _stats = script.plan_backfill(records, {"s1": t}, "transcript_path")

    assert plan[0] == {"model-alpha": _bucket(input_tokens=10)}
    assert plan[1] == {
        "model-alpha": _bucket(input_tokens=10),
        "model-beta": _bucket(input_tokens=400),
    }


def test_plan_skips_a_session_when_any_snapshot_has_no_transcript(script, tmp_path):
    # All-or-nothing per session: correcting some snapshots and leaving others
    # inflates the delta at the seam. The dedup backfill measured that as an
    # invented 1.9-billion-token spike on a single day.
    t = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {"session_id": "s1", "transcript_path": t, "timestamp_end": "2026-08-01T10:30:00Z"},
        {"session_id": "s1", "transcript_path": None, "timestamp_end": "2026-08-01T12:00:00Z"},
    ]

    plan, stats = script.plan_backfill(records, {"s1": t}, "transcript_path")

    assert plan == {}
    assert sum(stats.values()) > 0


def test_plan_leaves_snapshots_that_already_have_the_field(script, tmp_path):
    t = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": t,
            "timestamp_end": "2026-08-01T10:30:00Z",
            "tokens_by_model": {"model-alpha": _bucket(input_tokens=10)},
        }
    ]

    plan, _stats = script.plan_backfill(records, {"s1": t}, "transcript_path")

    assert plan == {}, "a snapshot that already has the field is not rewritten"


def test_check_against_native_is_silent_when_the_reconstruction_agrees(script, tmp_path):
    t = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": t,
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
    t = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {
            "session_id": "s1",
            "transcript_path": t,
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
    t = _one_entry_transcript(tmp_path)
    records = [_native_row(t, {"model-alpha": _bucket(input_tokens=100_500)})]

    report = script.check_against_native(records, "transcript_path")

    assert report.problems == []
    assert (report.exact, report.tolerated) == (0, 1)


def test_the_oracle_reports_a_shortfall_past_the_tolerance(script, tmp_path):
    # 2% low. Same direction as the accepted case, an order of magnitude
    # bigger, and the floor is what separates them.
    t = _one_entry_transcript(tmp_path)
    records = [_native_row(t, {"model-alpha": _bucket(input_tokens=102_000)})]

    report = script.check_against_native(records, "transcript_path")

    assert len(report.problems) == 1
    assert "past the 1% floor" in report.problems[0]
    assert (report.exact, report.tolerated) == (0, 0)


def test_the_oracle_reports_an_over_count_however_small(script, tmp_path):
    # THE test the asymmetry exists for. 0.01% HIGH - two hundred times
    # smaller than the shortfall accepted above, and refused anyway, because
    # this repo's worst backfill outcome was inventing 1.9 billion tokens and
    # a fabricated bar is loud where a sub-1% shortfall is invisible. If this
    # test ever passes with a symmetric comparison, the tolerance has quietly
    # become "close enough" in both directions.
    t = _one_entry_transcript(tmp_path)
    records = [_native_row(t, {"model-alpha": _bucket(input_tokens=99_990)})]

    report = script.check_against_native(records, "transcript_path")

    assert len(report.problems) == 1
    assert "ABOVE native" in report.problems[0]
    assert "never tolerated" in report.problems[0]
    assert (report.exact, report.tolerated) == (0, 0)


def test_the_oracle_reports_a_model_bucket_that_exists_on_only_one_side(script, tmp_path):
    # A missing or invented series is not a rounding difference, so it is
    # reported in both directions and never tolerated - even when the numbers
    # attached to it are tiny enough to sit inside the 1% floor.
    t = _one_entry_transcript(tmp_path)
    missing_from_reconstruction = [
        _native_row(
            t,
            {
                "model-alpha": _bucket(input_tokens=100_000),
                "model-ghost": _bucket(input_tokens=1),
            },
        )
    ]
    invented_by_reconstruction = [
        _native_row(t, {}),
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
    measured over the live tree, 1,005 of 1,005 files carry it and 2,124 of
    2,124 ids do not. An earlier version of this helper named the file after
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
    # --file subagents. Measured 2026-09-16: 44 native subagent snapshots
    # were reported as having no transcript while all 44 files were on disk.
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
    projects = tmp_path / "projects"
    _subagent_tree(projects, "slug-a", "sess-1", "dup")
    _subagent_tree(projects, "slug-b", "sess-1", "dup")
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
    t = _one_entry_transcript(tmp_path)
    native = {"model-alpha": {**_bucket(input_tokens=100_000), "thinking_tokens": 5}}
    records = [_native_row(t, native)]

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
    t = _one_entry_transcript(tmp_path)
    records = [
        _native_row(t, {"model-alpha": _bucket(input_tokens=100_000)}),
        {
            "session_id": "s2",
            "transcript_path": t,
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
    t = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    records = [
        {"session_id": "s1", "transcript_path": t, "timestamp_end": "2026-08-01T10:30:00Z"},
        {"session_id": "s1", "transcript_path": t, "timestamp_end": None},
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
    t = _transcript(
        tmp_path / "t.jsonl",
        [
            ("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10}),
            ("2026-08-01T11:00:00Z", "a2", "model-beta", {"input_tokens": 400}),
        ],
    )
    records = [
        {"session_id": "s1", "transcript_path": t, "timestamp_end": "2026-08-01T10:30:00Z"},
        {
            "session_id": "s1",
            "transcript_path": t,
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
    t = _transcript(
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
            "transcript_path": t,
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
    t = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    native = {"model-alpha": _bucket(input_tokens=10)}
    stamps = [f"2026-08-01T1{hour}:00:00Z" for hour in (1, 2, 3)]
    scans: list[str] = []
    real_scan = script.scan_transcript

    def counting_scan(path: str) -> Any:
        scans.append(path)
        return real_scan(path)

    monkeypatch.setattr(script, "scan_transcript", counting_scan)

    native_rows = [
        {
            "session_id": "s1",
            "transcript_path": t,
            "timestamp_end": stamp,
            "tokens_by_model": native,
        }
        for stamp in stamps
    ]
    assert script.check_against_native(native_rows, "transcript_path").problems == []
    assert scans == [t]

    pending_rows = [
        {"session_id": "s1", "transcript_path": t, "timestamp_end": stamp} for stamp in stamps
    ]
    plan, _stats = script.plan_backfill(pending_rows, {}, "transcript_path")

    assert len(plan) == 3
    assert scans == [t, t]


def test_plan_summary_of_an_empty_plan_over_a_file_with_no_tokens(script, capsys):
    # The degenerate end of the report: nothing to write, and no volume to
    # take a share of. The percentage must not divide by zero on the way to
    # saying so.
    script.print_plan_summary([{"session_id": "s1"}], {}, collections.Counter(), "session_id")

    out = capsys.readouterr().out
    assert "Units planned: 0 of 1" in out
    assert "Rows to write: 0 of 1" in out
    assert "Token volume covered: 0 of 0 (-)" in out
    assert "Earliest snapshot" not in out


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
    t = _transcript(
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
                "transcript_path": t,
                "timestamp_end": "2026-08-01T10:30:00Z",
                "input_tokens": 10,
            },
            {
                "session_id": "s1",
                "transcript_path": t,
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
    assert "READ-ONLY" in out


def test_main_aborts_and_plans_nothing_when_a_reconstruction_disagrees(
    script, tmp_path, monkeypatch, capsys
):
    # The whole reason the read-only half runs first. The message carries
    # both values, because deciding WHICH of the two is wrong is the question
    # the oracle exists to raise, not one this script can answer.
    t = _transcript(
        tmp_path / "t.jsonl", [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"input_tokens": 10})]
    )
    _metrics_file(
        tmp_path,
        "sessions",
        [
            {
                "session_id": "s1",
                "transcript_path": t,
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
    t = _transcript(
        tmp_path / "agent.jsonl",
        [("2026-08-01T10:00:00Z", "a1", "model-alpha", {"output_tokens": 7})],
    )
    _metrics_file(
        tmp_path,
        "subagents",
        [
            {
                "agent_id": "ag1",
                "agent_transcript_path": t,
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
    _subagent_tree(projects, "slug-b", "sess-1", "dup")
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
