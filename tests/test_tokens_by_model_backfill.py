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

    assert script.check_against_native(records, "transcript_path") == []


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

    problems = script.check_against_native(records, "transcript_path")

    assert len(problems) == 1
    assert "s1" in problems[0]


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
    assert script.check_against_native(records, "transcript_path") == []


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
    assert script.check_against_native(native_rows, "transcript_path") == []
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
    assert "oracle: 1 of 1 natively written snapshots still have their transcript" in out
    assert "oracle: all 1 reconstructions agree with the native value" in out
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
    assert "oracle: 0 of 0 natively written snapshots still have their transcript" in out
    assert "Units planned: 1 of 1" in out


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
