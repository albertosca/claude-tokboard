import argparse
import collections
import hashlib
import importlib.util
import json
import stat
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill-token-dedup.py"


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    # The filename has a hyphen, so it cannot be imported with a normal
    # `import` statement - load it as a module by path instead.
    spec = importlib.util.spec_from_file_location("backfill_token_dedup", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_valid_cutoff_rejects_a_string_that_does_not_even_parse(script):
    # Distinct from the round-trip rejection test below (a value that DOES
    # parse but is not zero-padded): this one never parses at all, hitting
    # strptime's own ValueError, not the strftime round-trip mismatch.
    with pytest.raises(argparse.ArgumentTypeError, match="--cutoff must be YYYY-MM-DD"):
        script._valid_cutoff("not-a-date")


def _day_record(session_id: str, day: str, tokens: int) -> dict[str, object]:
    """One snapshot for a session that has exactly one, so the delta
    expand_records_to_daily_deltas produces is the row's own token total,
    dated by its timestamp_end."""
    return {
        "session_id": session_id,
        "duration_seconds": 0,
        "timestamp_end": f"{day}T10:00:00",
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def test_print_daily_comparison_renders_the_whole_table_and_every_factor_state(script, capsys):
    # This is the report an operator reads before authorising a destructive
    # backfill against real metrics history, and its last line is a
    # data-loss detector. It was reachable at 100% coverage with nothing
    # asserted on it at all, so the entire body could be deleted in
    # silence. Pinning the rendered block verbatim - header, one row per
    # day, and the collapsed-days line - is what makes that impossible.
    #
    # The three days are chosen to cover all three states of the `factor`
    # column, which is a single ternary and therefore untestable one state
    # at a time from a real dataset:
    #   08-01  after non-zero -> the real before/after ratio
    #   08-02  after zero, before non-zero -> COLLAPSED (data would vanish)
    #   08-03  both zero -> "-" (nothing to compare)
    before = [
        _day_record("s1", "2026-08-01", 2000),
        _day_record("s2", "2026-08-02", 500),
        _day_record("s3", "2026-08-03", 0),
    ]
    after = [
        _day_record("s1", "2026-08-01", 1000),
        _day_record("s2", "2026-08-02", 0),
        _day_record("s3", "2026-08-03", 0),
    ]

    script.print_daily_comparison(before, after, 15)

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "",
        "Per-day token totals via expand_records_to_daily_deltas (last 15 days)",
        "  day                     before             after    factor",
        # 2000/1000 - the ratio is before/after, so a correction that halves
        # a day reads as 2.000x, never 0.500x.
        "  2026-08-01               2,000             1,000    2.000x",
        "  2026-08-02                 500                 0 COLLAPSED",
        "  2026-08-03                   0                 0         -",
        "  days that collapsed to zero: 1 ['2026-08-02']",
    ]


def test_print_summary_pins_every_bucket_boundary_on_both_sides(script, capsys):
    # The histogram has FOUR boundaries (1.001, 1.5, 1.95, 2.05), every
    # comparison a strict `<`. Each one gets a factor strictly BELOW it
    # (must land in the lower bucket) and a factor AT it (must land in the
    # upper bucket, since `<` excludes the boundary value itself) - so a
    # boundary moved in either direction breaks one exact per-bucket count.
    # A test built from mid-range values only (e.g. 1.2, 1.7) cannot tell
    # `< 1.5` from `< 1.4`, or `< 1.95` from `< 1.85`: every one of those
    # mid-range factors lands in the same bucket either way.
    factors = [
        0.5,
        0.998,  # 0.999 boundary: a first-occurrence sum raised
        0.999,
        1.000,
        1.001,  # 1.001 boundary
        1.499,
        1.5,  # 1.5 boundary
        1.94,
        1.95,  # 1.95 boundary
        2.049,
        2.05,  # 2.05 boundary
    ]
    expected_counts = {
        "<1.00x (undercount raised)": 2,  # 0.5, 0.998
        "1.00x (no duplicates)": 2,  # 0.999, 1.000
        "1.00-1.50x": 2,  # 1.001, 1.499
        "1.50-1.95x": 2,  # 1.5, 1.94
        "~2.00x": 2,  # 1.95, 2.049
        ">2.05x": 1,  # 2.05
    }
    script.print_summary(
        stats=collections.Counter({"corrected": len(factors)}),
        factors=factors,
        before_total=100,
        after_total=50,
        changed_lines=len(factors),
        total_lines=10,
    )
    out = capsys.readouterr().out
    for bucket, count in expected_counts.items():
        assert f"    {count:>6}  {bucket}" in out, f"expected {count} in {bucket!r} - got:\n{out}"


def test_print_summary_pins_every_line_it_prints_not_only_the_histogram(script, capsys):
    # Companion to the bucket-boundary test above, which asserts only the
    # five histogram lines and therefore leaves the rest of the function
    # free to print anything. Everything else print_summary emits is
    # decision-grade: the percentile line is what an operator reads to
    # judge whether the correction is sane, and "inflation removed" is the
    # headline figure. So this pins the WHOLE rendered block, in order.
    #
    # The inputs are chosen so each output can only come out right one way:
    #   - three dispositions with DISTINCT counts, passed in a Counter
    #     whose natural order is not the sorted one, so the `-kv[1]`
    #     descending sort is observable rather than incidental;
    #   - SIX factors, so `int(6 * 0.25) == 1` and `round(6 * 0.25) == 2`
    #     select different elements (1.200x vs 1.300x) - p25 is the only
    #     percentile of the three where truncation and rounding disagree;
    #   - factors passed UNSORTED, so the internal sort is what makes
    #     min/max come out at the ends;
    #   - before 1000 / after 400, an asymmetric pair, so before/after
    #     (2.500x) is distinguishable from after/before, and printing
    #     either total twice in the grand-total line is visible.
    script.print_summary(
        stats=collections.Counter(
            {"corrected": 6, "skipped: row after cutoff": 2, "skipped: no transcript": 1}
        ),
        factors=[1.40, 1.10, 1.60, 1.30, 1.20, 1.50],
        before_total=1000,
        after_total=400,
        changed_lines=6,
        total_lines=10,
    )
    assert capsys.readouterr().out.splitlines() == [
        "",
        "Row disposition",
        "       6  corrected",
        "       2  skipped: row after cutoff",
        "       1  skipped: no transcript",
        "",
        "Lines: 10 read, 6 would change",
        "Correction factor (stored / recomputed) over corrected rows:",
        "  min 1.100x  p25 1.200x  median 1.400x  p75 1.500x  max 1.600x",
        "         4  1.00-1.50x",
        "         2  1.50-1.95x",
        "",
        "Grand total tokens: before 1,000  after 400",
        "  overall inflation removed: 2.500x (600 tokens)",
    ]


def test_write_atomically_writes_new_lines_and_preserves_mode(tmp_path, script):
    path = tmp_path / "sessions.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    path.chmod(0o600)
    snapshot = path.read_bytes()

    appended = script.write_atomically(str(path), snapshot, [b'{"a": 2}'], 0o600)

    assert appended == 0
    assert path.read_bytes() == b'{"a": 2}\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_atomically_preserves_a_concurrent_append_verbatim(tmp_path, script):
    path = tmp_path / "sessions.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    snapshot = path.read_bytes()
    # Simulate a live Stop hook appending a row after the snapshot was read.
    with path.open("ab") as append_file:
        append_file.write(b'{"a": "live-append"}\n')

    appended = script.write_atomically(str(path), snapshot, [b'{"a": "corrected"}'], 0o644)

    assert appended == 1
    content = path.read_bytes()
    assert content == b'{"a": "corrected"}\n{"a": "live-append"}\n'


def _append_just_before_the_rename(
    monkeypatch: pytest.MonkeyPatch, target: Path, data: bytes
) -> None:
    """Make a 'live hook' append to the file at the worst moment: after the
    final read, right before the new file is renamed over it (the window the
    2026-09-23 review reproduced losing a line in)."""
    real_replace = Path.replace

    def replace(self: Path, destination: Path) -> Path:
        if Path(destination) == target:
            with target.open("ab") as append_file:
                append_file.write(data)
        return real_replace(self, destination)

    monkeypatch.setattr(Path, "replace", replace)


def test_write_atomically_carries_an_append_that_lands_just_before_the_rename(
    tmp_path, script, monkeypatch
):
    path = tmp_path / "subagents.jsonl"
    path.write_bytes(b'{"a": "old"}\n')
    snapshot = path.read_bytes()
    _append_just_before_the_rename(monkeypatch, path, b'{"a": "live-append"}\n')

    appended = script.write_atomically(str(path), snapshot, [b'{"a": "corrected"}'], 0o600)

    assert path.read_bytes() == b'{"a": "corrected"}\n{"a": "live-append"}\n'
    assert appended == 1


def test_write_atomically_saves_a_partial_late_append_and_fails_loudly(
    tmp_path, script, monkeypatch
):
    # Half a line cannot be appended after the new file's last line without
    # risking a hook's next line gluing onto it; it is saved beside the
    # file, byte for byte, and the run stops with its location.
    path = tmp_path / "subagents.jsonl"
    path.write_bytes(b'{"a": "old"}\n')
    snapshot = path.read_bytes()
    _append_just_before_the_rename(monkeypatch, path, b'{"a": "half')

    with pytest.raises(RuntimeError, match="late-append") as excinfo:
        script.write_atomically(str(path), snapshot, [b'{"a": "corrected"}'], 0o600)

    rescued = list(tmp_path.glob("subagents.jsonl.late-append-*.partial"))
    assert len(rescued) == 1
    assert rescued[0].read_bytes() == b'{"a": "half'
    assert str(rescued[0]) in str(excinfo.value)
    assert path.read_bytes() == b'{"a": "corrected"}\n'


def test_write_atomically_refuses_a_non_append_change(tmp_path, script):
    path = tmp_path / "sessions.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    snapshot = path.read_bytes()
    # Something rewrote the file's beginning - not a plain append.
    path.write_bytes(b'{"a": "tampered"}\n')

    with pytest.raises(RuntimeError, match="not a plain append"):
        script.write_atomically(str(path), snapshot, [b'{"a": "corrected"}'], 0o644)


def test_write_atomically_except_handler_tolerates_a_temp_file_already_gone(
    tmp_path, script, monkeypatch
):
    # The `if temporary.exists():` guard in the except handler exists for
    # the case where something else (another cleanup pass, a /tmp reaper)
    # already removed the temp file before this function's own cleanup
    # runs - unlink() on a path that is already gone would raise
    # FileNotFoundError and mask the real error being propagated. Simulated
    # here by unlinking the temp file the instant mkstemp hands it back
    # (the fd stays valid on POSIX even after the directory entry is
    # removed, so the write below still lands), then triggering the
    # existing "not a plain append" RuntimeError to reach the handler.
    path = tmp_path / "sessions.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    snapshot = path.read_bytes()
    path.write_bytes(b'{"a": "tampered"}\n')  # not a plain append

    real_mkstemp = tempfile.mkstemp

    def mkstemp_then_unlink(*arguments: Any, **keyword_arguments: Any) -> tuple[int, str]:
        # Forwards whatever tempfile.mkstemp's real callers pass (the
        # production code calls it with dir=, prefix=, suffix=) rather than
        # repeating stdlib's own "dir" keyword as a declared parameter name.
        handle, name = real_mkstemp(*arguments, **keyword_arguments)
        Path(name).unlink()
        return handle, name

    monkeypatch.setattr(script.tempfile, "mkstemp", mkstemp_then_unlink)

    with pytest.raises(RuntimeError, match="not a plain append"):
        script.write_atomically(str(path), snapshot, [b'{"a": "corrected"}'], 0o644)


def test_main_leaves_the_backfill_state_alone_when_an_apply_has_nothing_to_correct(
    tmp_path, script, monkeypatch, capsys
):
    # No transcript exists for "s1" here, so the plan is empty - what every
    # run after the file is caught up looks like. Such a run used to write
    # the state anyway, replacing the record of the run that DID correct
    # rows with corrected_rows 0 and earliest_corrected null: the retention
    # note both dashboards render would have lost its date. This test used
    # to assert exactly that overwrite.
    metrics_file = tmp_path / "sessions.jsonl"
    projects_directory = tmp_path / "projects"
    projects_directory.mkdir()
    backup_directory = tmp_path / "backups"
    state_path = tmp_path / "backfill_state.json"
    previous = (
        b'{"cutoff": "2026-08-21", "applied_at": "2026-08-21T19:49:23", '
        b'"corrected_rows": 2798, "earliest_corrected": "2026-07-22T09:00:00"}'
    )
    state_path.write_bytes(previous)
    metrics_file.write_text(
        '{"session_id": "s1", "timestamp_end": "2026-08-01T00:00:00", '
        '"input_tokens": 0, "output_tokens": 0, '
        '"cache_read_tokens": 0, "cache_creation_tokens": 0}\n'
    )
    before = metrics_file.read_bytes()

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
            "--backup-dir",
            str(backup_directory),
            "--apply",
            "--state-file",
            str(state_path),
        ],
    )
    exit_code = script.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "nothing to correct; leaving the file and the backfill state untouched" in out
    assert state_path.read_bytes() == previous
    assert metrics_file.read_bytes() == before
    assert not backup_directory.exists()


def test_main_records_uncorrectable_rows_beside_the_corrected_ones(tmp_path, script, monkeypatch):
    # The retention boundary's other half: rows no transcript can reach are
    # counted into the state of a run that corrected something.
    projects_directory = tmp_path / "projects"
    slug_directory = projects_directory / "some-project"
    slug_directory.mkdir(parents=True)
    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_directory / "s1.jsonl",
        [("2026-08-01T10:00:00", "msg-1", usage), ("2026-08-01T10:00:01", "msg-1", usage)],
    )
    metrics_file = tmp_path / "sessions.jsonl"
    rows = [
        {"session_id": "s1", "timestamp_end": "2026-08-01T10:00:01", "input_tokens": 200},
        {"session_id": "gone", "timestamp_end": "2026-08-01T10:00:01", "input_tokens": 5},
    ]
    for row in rows:
        row.update(output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0)
    metrics_file.write_text("".join(json.dumps(row) + "\n" for row in rows))
    state_path = tmp_path / "backfill_state.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--state-file",
            str(state_path),
            "--apply",
        ],
    )

    assert script.main() == 0

    state = json.loads(state_path.read_text())
    assert state["corrected_rows"] == 1
    assert state["uncorrectable_rows"] == 1
    # Named, not just counted, so a later run that corrects the same row
    # again does not count it twice.
    assert state["rows_changed_this_run"] == 1
    assert state["corrected_row_ids"] == script.row_identities(rows, [0], "session_id")


def test_main_rejects_a_malformed_cutoff(script, monkeypatch, capsys):
    # A malformed value like "2026-8-21" (missing zero-pad) would otherwise
    # sort lexicographically wrong against timestamp_end strings, silently
    # making every row in the file eligible regardless of the operator's
    # real intent - argparse's type= conversion must catch this before
    # plan_corrections ever runs.
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--cutoff",
            "2026-8-21",
        ],
    )

    with pytest.raises(SystemExit) as excinfo:
        script.main()

    assert excinfo.value.code == 2
    assert "--cutoff must be YYYY-MM-DD" in capsys.readouterr().err


def _write_transcript(path: Path, entries: list[tuple[str, str, dict[str, int]]]) -> None:
    """entries: list of (timestamp, message_id, usage_dict). Mirrors
    tests/test_backfill.py's write_transcript/usage helpers - see that
    file for the JSON shape index_transcripts/scan_transcript expect."""
    with path.open("w") as transcript_file:
        for timestamp, message_id, usage in entries:
            transcript_file.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": timestamp,
                        "message": {"id": message_id, "usage": usage},
                    }
                )
                + "\n"
            )


def test_main_rewrites_the_file_through_the_real_backup_and_write_path(
    tmp_path, script, monkeypatch
):
    # Unlike the test above, this seeds a real transcript with a duplicated
    # message id, and stores the RAW (duplicate-inflated) sum in the
    # metrics row - so plan_corrections returns a non-empty plan and main()
    # actually runs the backup-creation, backup-verification, and
    # write_atomically block, not just the `if not plan:` shortcut.
    metrics_file = tmp_path / "sessions.jsonl"
    projects_directory = tmp_path / "projects"
    # index_transcripts globs <projects_directory>/*/<session_id>.jsonl.
    slug_directory = projects_directory / "some-project"
    slug_directory.mkdir(parents=True)
    backup_directory = tmp_path / "backups"
    state_path = tmp_path / "backfill_state.json"

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_directory / "s1.jsonl",
        [
            ("2026-08-01T10:00:00", "msg-1", usage),
            ("2026-08-01T10:00:01", "msg-1", usage),  # replay: the broken hook's bug
        ],
    )
    # deduped as-of sum is 100 (one real message); the broken hook summed
    # both copies and stored the raw sum, 200.
    metrics_file.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "timestamp_end": "2026-08-01T10:00:01",
                "input_tokens": 200,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        )
        + "\n"
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
            "--backup-dir",
            str(backup_directory),
            "--apply",
            "--state-file",
            str(state_path),
        ],
    )
    exit_code = script.main()

    assert exit_code == 0

    # the backup block ran and left exactly one verified backup behind
    backups = list(backup_directory.glob("sessions.jsonl.backfill-*.bak"))
    assert len(backups) == 1
    assert (
        backups[0].read_bytes() == b'{"session_id": "s1", "timestamp_end": '
        b'"2026-08-01T10:00:01", "input_tokens": 200, "output_tokens": 0, '
        b'"cache_read_tokens": 0, "cache_creation_tokens": 0}\n'
    )

    # write_atomically actually ran, through main(): the live file now
    # holds the corrected (deduped) value, not the original raw one.
    corrected = json.loads(metrics_file.read_text().strip())
    assert corrected["input_tokens"] == 100

    state = json.loads(state_path.read_text())
    assert state["corrected_rows"] == 1
    assert state["cutoff"]
    assert "applied_at" in state
    assert state["earliest_corrected"] == "2026-08-01T10:00:01"
    assert state["uncorrectable_rows"] == 0


def test_main_aborts_when_no_metrics_file_exists_at_the_given_path(
    tmp_path, script, monkeypatch, capsys
):
    missing = tmp_path / "does-not-exist.jsonl"
    monkeypatch.setattr("sys.argv", ["backfill-token-dedup.py", "--metrics-file", str(missing)])

    exit_code = script.main()

    assert exit_code == 1
    assert f"no metrics file at {missing}" in capsys.readouterr().err


def test_main_aborts_on_a_line_that_is_not_valid_json(tmp_path, script, monkeypatch, capsys):
    metrics_file = tmp_path / "sessions.jsonl"
    metrics_file.write_text("not json at all\n")
    projects_directory = tmp_path / "projects"
    projects_directory.mkdir()

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
        ],
    )

    exit_code = script.main()

    assert exit_code == 1
    assert "line 1 is not valid JSON, aborting" in capsys.readouterr().err


def test_main_aborts_when_field_verification_reports_a_problem(
    tmp_path, script, monkeypatch, capsys
):
    # A row from an older schema that never wrote cache_creation_tokens at
    # all. plan_corrections still computes a correction for it (it reads
    # every token field with .get(field, 0)), but apply_plan's rewrite ADDS
    # that key to the corrected copy - changing the record's key SET, which
    # is exactly what verify_fields exists to catch before anything is
    # written.
    metrics_file = tmp_path / "sessions.jsonl"
    projects_directory = tmp_path / "projects"
    slug_directory = projects_directory / "some-project"
    slug_directory.mkdir(parents=True)

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_directory / "s1.jsonl",
        [
            ("2026-08-01T10:00:00", "msg-1", usage),
            ("2026-08-01T10:00:01", "msg-1", usage),  # replay
        ],
    )
    metrics_file.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "timestamp_end": "2026-08-01T10:00:01",
                "input_tokens": 200,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                # cache_creation_tokens deliberately absent
            }
        )
        + "\n"
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
        ],
    )

    exit_code = script.main()

    assert exit_code == 1
    error_output = capsys.readouterr().err
    assert "FIELD VERIFICATION FAILED" in error_output
    assert "key set changed" in error_output
    # dry run (no --apply): the live file must be untouched either way.
    assert "cache_creation_tokens" not in metrics_file.read_text()


def test_main_aborts_on_a_monotonicity_regression_introduced_by_the_rewrite(
    tmp_path, script, monkeypatch, capsys
):
    # Two rows of the same session, sorted by duration_seconds (the key
    # find_nonmonotonic_sessions uses) rather than by timestamp_end (the
    # key the correction is computed as-of). Before correction the raw
    # values happen to be non-decreasing in duration order (150, then
    # 200). One of the two rows (the one with the LARGER duration_seconds)
    # gets corrected down to 100 - which is now LESS than the other row's
    # untouched 150, introducing a decrease that was not there before.
    metrics_file = tmp_path / "sessions.jsonl"
    projects_directory = tmp_path / "projects"
    slug_directory = projects_directory / "some-project"
    slug_directory.mkdir(parents=True)

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    usage_50 = {
        "input_tokens": 50,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_directory / "s1.jsonl",
        [
            ("2026-08-01T10:00:00", "msg-1", usage),
            ("2026-08-01T10:00:01", "msg-1", usage),  # replay: raw-as-of(T1)=200, deduped=100
            ("2026-08-01T10:00:02", "msg-2", usage_50),  # raw-as-of(T2)=250, deduped=150
        ],
    )
    rows = [
        {  # duration_seconds smaller -> sorts first; already correct, untouched
            "session_id": "s1",
            "timestamp_end": "2026-08-01T10:00:02",
            "duration_seconds": 10,
            "input_tokens": 150,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        {  # duration_seconds larger -> sorts second; stored = raw, gets corrected to 100
            "session_id": "s1",
            "timestamp_end": "2026-08-01T10:00:01",
            "duration_seconds": 100,
            "input_tokens": 200,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    ]
    metrics_file.write_text("".join(json.dumps(row) + "\n" for row in rows))

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
        ],
    )

    exit_code = script.main()

    assert exit_code == 1
    error_output = capsys.readouterr().err
    assert "MONOTONICITY REGRESSION in ['s1']" in error_output


def test_main_dry_run_returns_zero_and_writes_nothing(tmp_path, script, monkeypatch, capsys):
    metrics_file = tmp_path / "sessions.jsonl"
    projects_directory = tmp_path / "projects"
    projects_directory.mkdir()
    state_path = tmp_path / "backfill_state.json"
    metrics_file.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "timestamp_end": "2026-08-01T00:00:00",
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        )
        + "\n"
    )
    before = metrics_file.read_bytes()

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
            "--state-file",
            str(state_path),
        ],
    )

    exit_code = script.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "DRY RUN - nothing written" in out
    # The header names the digest as sha256 and it is the operator's only
    # record of which bytes this run read. Computed here with hashlib
    # directly, never by calling the script's own sha256_bytes: comparing
    # that function against itself would pass under any algorithm.
    assert f"  sha256 {hashlib.sha256(before).hexdigest()}" in out
    assert metrics_file.read_bytes() == before
    assert not state_path.exists()


def test_main_aborts_when_the_backup_does_not_verify(tmp_path, script, monkeypatch, capsys):
    metrics_file = tmp_path / "sessions.jsonl"
    projects_directory = tmp_path / "projects"
    slug_directory = projects_directory / "some-project"
    slug_directory.mkdir(parents=True)
    backup_directory = tmp_path / "backups"
    state_path = tmp_path / "backfill_state.json"

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_directory / "s1.jsonl",
        [
            ("2026-08-01T10:00:00", "msg-1", usage),
            ("2026-08-01T10:00:01", "msg-1", usage),  # replay
        ],
    )
    metrics_file.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "timestamp_end": "2026-08-01T10:00:01",
                "input_tokens": 200,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        )
        + "\n"
    )

    # Fake a corrupted-copy race: sha256_of is a seam imported straight into
    # this module's namespace, so patching it here (never the real repo
    # source) makes the source and backup hash differently despite
    # shutil.copy2 having copied the bytes faithfully - the exact situation
    # this check exists to catch.
    monkeypatch.setattr(script, "sha256_of", lambda path: f"hash-of-{path}")

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
            "--backup-dir",
            str(backup_directory),
            "--apply",
            "--state-file",
            str(state_path),
        ],
    )

    exit_code = script.main()

    assert exit_code == 1
    assert "BACKUP VERIFICATION FAILED" in capsys.readouterr().err
    # aborted before any backfill state was recorded
    assert not state_path.exists()


def test_main_aborts_on_a_written_line_count_mismatch(tmp_path, script, monkeypatch, capsys):
    metrics_file = tmp_path / "sessions.jsonl"
    projects_directory = tmp_path / "projects"
    slug_directory = projects_directory / "some-project"
    slug_directory.mkdir(parents=True)
    backup_directory = tmp_path / "backups"
    state_path = tmp_path / "backfill_state.json"

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_directory / "s1.jsonl",
        [
            ("2026-08-01T10:00:00", "msg-1", usage),
            ("2026-08-01T10:00:01", "msg-1", usage),  # replay
        ],
    )
    metrics_file.write_text(
        json.dumps(
            {
                "session_id": "s1",
                "timestamp_end": "2026-08-01T10:00:01",
                "input_tokens": 200,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            }
        )
        + "\n"
    )

    # write_atomically itself is a proven seam (covered directly above);
    # here it is left to do the real write, only its returned "appended"
    # count is corrupted, so the expected/actual line-count arithmetic in
    # main() disagrees exactly the way a lost or duplicated write would.
    real_write_atomically = script.write_atomically

    def lying_write_atomically(*arguments: object, **keyword_arguments: object) -> int:
        real_write_atomically(*arguments, **keyword_arguments)
        return 999

    monkeypatch.setattr(script, "write_atomically", lying_write_atomically)

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_directory),
            "--backup-dir",
            str(backup_directory),
            "--apply",
            "--state-file",
            str(state_path),
        ],
    )

    exit_code = script.main()

    assert exit_code == 1
    assert "LINE COUNT MISMATCH: expected 1000, got 1" in capsys.readouterr().err
    # The file WAS rewritten before the check failed, so the correction is
    # recorded: without it a re-run finds every row already corrected, says
    # "nothing to correct" and the run is never recorded at all. (This test
    # used to assert the state was absent - pinning exactly that.)
    assert json.loads(state_path.read_text())["corrected_rows"] == 1


def test_main_records_the_state_when_a_post_write_check_raises(
    tmp_path, script, monkeypatch, capsys
):
    # A half line landing during the rename makes write_atomically raise
    # AFTER the corrected file is in place. The state must already say so.
    paths = _subagents_fixture(tmp_path)
    _append_just_before_the_rename(monkeypatch, paths["metrics"], b'{"half')
    monkeypatch.setattr("sys.argv", _subagents_argv(paths, "--apply"))

    with pytest.raises(RuntimeError, match="late-append"):
        script.main()

    rows = [json.loads(line) for line in paths["metrics"].read_text().splitlines()]
    assert [row["input_tokens"] for row in rows] == [100, 50, 20]
    state = json.loads(paths["state"].read_text())
    assert state["subagents"]["corrected_rows"] == 2


def test_module_guard_calls_main_and_exits_with_its_return_code(tmp_path, monkeypatch):
    # `if __name__ == "__main__": sys.exit(main())` is never exercised by
    # loading this module under any other name, including the shared
    # `script` fixture. Re-loading it with __name__ pre-set to "__main__"
    # runs the real file - so this test drives argv at a metrics file
    # under tmp_path that does not exist, which makes main() take its very
    # first abort branch (no metrics file at the given path) before it
    # touches anything else. This is safe: it can never reach the real
    # ~/.claude/metrics/sessions.jsonl default, because a value is always
    # supplied for --metrics-file.
    missing = tmp_path / "does-not-exist.jsonl"
    monkeypatch.setattr(sys, "argv", ["backfill-token-dedup.py", "--metrics-file", str(missing)])

    spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 1


# --- subagents.jsonl: units are agent_ids, never the parent session_id ----


def _subagent_row(
    agent_id: str, timestamp_end: str, duration: int, tokens: int, path: str
) -> dict[str, object]:
    # Mirrors hooks/subagent-metrics-log.py build_record: every subagent row
    # carries its PARENT's session_id beside its own agent_id.
    return {
        "session_id": "parent",
        "agent_id": agent_id,
        "agent_type": "general-purpose",
        "timestamp_end": timestamp_end,
        "duration_seconds": duration,
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "agent_transcript_path": path,
    }


def _replayed(
    timestamp: str, message_id: str, tokens: int, copies: int
) -> list[tuple[str, str, dict[str, int]]]:
    usage = {
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    return [(timestamp, message_id, usage)] * copies


def _subagents_fixture(tmp_path: Path) -> dict[str, Path]:
    projects = tmp_path / "projects"
    agents = projects / "some-project" / "parent" / "subagents"
    agents.mkdir(parents=True)
    # a1: one message stored twice -> raw 200, deduped 100.
    _write_transcript(agents / "agent-a1.jsonl", _replayed("2026-08-01T10:00:00", "m1", 100, 2))
    # a2: one message stored five times -> raw 250, deduped 50. Its row
    # records a path that has since gone stale; the by-id index finds it.
    _write_transcript(agents / "agent-a2.jsonl", _replayed("2026-08-01T11:00:00", "m2", 50, 5))
    # a3: inflated the same way, but written after the cutoff.
    _write_transcript(agents / "agent-a3.jsonl", _replayed("2026-08-22T09:00:00", "m3", 10, 2))
    metrics = tmp_path / "subagents.jsonl"
    rows = [
        _subagent_row("a1", "2026-08-01T10:00:00", 10, 200, str(agents / "agent-a1.jsonl")),
        _subagent_row("a2", "2026-08-01T11:00:00", 20, 250, str(tmp_path / "moved" / "x.jsonl")),
        _subagent_row("a3", "2026-08-22T09:00:00", 5, 20, str(agents / "agent-a3.jsonl")),
    ]
    # Durations are chosen so that, merged under "parent" and ordered by
    # duration, the stored series (20, 200, 250) climbs and the corrected
    # one (20, 100, 50) drops: a monotonicity check grouped by session_id
    # would report a regression this rewrite did not introduce.
    metrics.write_text("".join(json.dumps(row) + "\n" for row in rows))
    state = tmp_path / "backfill_state.json"
    state.write_text(
        json.dumps(
            {"cutoff": "2026-08-21", "applied_at": "2026-08-21T19:49:23", "corrected_rows": 2798}
        )
    )
    return {
        "projects": projects,
        "metrics": metrics,
        "state": state,
        "backups": tmp_path / "backups",
    }


def _subagents_argv(paths: dict[str, Path], *extra: str) -> list[str]:
    return [
        "backfill-token-dedup.py",
        "--kind",
        "subagents",
        "--metrics-file",
        str(paths["metrics"]),
        "--projects-dir",
        str(paths["projects"]),
        "--backup-dir",
        str(paths["backups"]),
        "--state-file",
        str(paths["state"]),
        "--cutoff",
        "2026-08-21",
        *extra,
    ]


def test_main_dedups_each_subagent_against_its_own_transcript(
    tmp_path, script, monkeypatch, capsys
):
    # The contamination test for this script: two subagents of one parent
    # session, each corrected against ITS OWN transcript. Grouped by the
    # parent session_id they would share one unit that no transcript is
    # keyed by, and the corrected pair (100 then 50, in duration order)
    # would read as a series that goes down.
    paths = _subagents_fixture(tmp_path)
    monkeypatch.setattr("sys.argv", _subagents_argv(paths, "--apply"))

    exit_code = script.main()

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    assert "indexed 3 subagent transcripts" in captured.out
    rows = [json.loads(line) for line in paths["metrics"].read_text().splitlines()]
    assert [row["input_tokens"] for row in rows] == [100, 50, 20]
    # a3 sits past the cutoff: inflated, and left alone on purpose.
    assert rows[2]["input_tokens"] == 20

    backups = list(paths["backups"].glob("subagents.jsonl.backfill-*.bak"))
    assert len(backups) == 1
    assert not list(paths["backups"].glob("sessions.jsonl*"))

    state = json.loads(paths["state"].read_text())
    # The sessions entry the dashboards read is untouched...
    assert state["cutoff"] == "2026-08-21"
    assert state["applied_at"] == "2026-08-21T19:49:23"
    assert state["corrected_rows"] == 2798
    # ...and this run is recorded beside it.
    assert state["subagents"]["corrected_rows"] == 2
    assert state["subagents"]["cutoff"] == "2026-08-21"
    assert state["subagents"]["earliest_corrected"] == "2026-08-01T10:00:00"
    assert state["subagents"]["uncorrectable_rows"] == 0


def test_main_subagents_dry_run_writes_nothing(tmp_path, script, monkeypatch, capsys):
    paths = _subagents_fixture(tmp_path)
    before = paths["metrics"].read_bytes()
    state_before = paths["state"].read_bytes()
    monkeypatch.setattr("sys.argv", _subagents_argv(paths))

    assert script.main() == 0

    out = capsys.readouterr().out
    assert "DRY RUN - nothing written" in out
    assert "Lines: 3 read, 2 would change" in out
    assert paths["metrics"].read_bytes() == before
    assert paths["state"].read_bytes() == state_before
    assert not paths["backups"].exists()


def test_main_refuses_a_subagents_run_on_a_sessions_file(tmp_path, script, monkeypatch, capsys):
    paths = _subagents_fixture(tmp_path)
    paths["metrics"].write_text(
        json.dumps({"session_id": "s1", "timestamp_end": "2026-08-01T00:00:00", "input_tokens": 9})
        + "\n"
    )
    before = paths["metrics"].read_bytes()
    monkeypatch.setattr("sys.argv", _subagents_argv(paths, "--apply"))

    assert script.main() == 1

    error_output = capsys.readouterr().err
    assert "refusing: --kind subagents" in error_output
    assert "1 of 1 rows have no agent_id" in error_output
    assert paths["metrics"].read_bytes() == before
    assert not paths["backups"].exists()
    assert "subagents" not in json.loads(paths["state"].read_text())


def test_main_refuses_a_sessions_run_on_a_subagents_file(tmp_path, script, monkeypatch, capsys):
    # --kind defaults to sessions, so pointing --metrics-file at
    # subagents.jsonl without it - how this file would have been run before
    # --kind existed - refuses instead of grouping on the parent session_id.
    paths = _subagents_fixture(tmp_path)
    before = paths["metrics"].read_bytes()
    argv = _subagents_argv(paths, "--apply")
    del argv[1:3]
    monkeypatch.setattr("sys.argv", argv)

    assert script.main() == 1

    error_output = capsys.readouterr().err
    assert "refusing: --kind sessions" in error_output
    assert "3 of 3 rows carry an agent_id" in error_output
    assert paths["metrics"].read_bytes() == before
    assert not paths["backups"].exists()


def test_main_defaults_the_metrics_file_from_the_kind(tmp_path, script, monkeypatch, capsys):
    paths = _subagents_fixture(tmp_path)
    monkeypatch.setitem(script.METRICS_FILES, "subagents", str(paths["metrics"]))
    argv = _subagents_argv(paths)
    del argv[3:5]  # no --metrics-file
    monkeypatch.setattr("sys.argv", argv)

    assert script.main() == 0

    assert f"read 3 lines from {paths['metrics']}" in capsys.readouterr().out


def _streamed_fixture(tmp_path: Path) -> dict[str, Path]:
    """One subagent whose single message was streamed as three entries
    (output 3 -> 40 -> 179), stored the way the 2026-08-11 hook wrote it:
    the FIRST entry only, with a per-model map built the same way."""
    paths = _subagents_fixture(tmp_path)
    agents = paths["projects"] / "some-project" / "parent" / "subagents"
    entries = [
        (
            f"2026-08-02T10:00:0{second}",
            "s1",
            {
                "input_tokens": 2,
                "output_tokens": out,
                "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 50,
            },
        )
        for second, out in enumerate([3, 40, 179])
    ]
    _write_transcript(agents / "agent-s1.jsonl", entries)
    row = _subagent_row("s1", "2026-08-02T10:00:02", 40, 2, str(agents / "agent-s1.jsonl"))
    row.update(output_tokens=3, cache_read_tokens=1000, cache_creation_tokens=50)
    row["tokens_by_model"] = {
        "unknown": {
            "input_tokens": 2,
            "output_tokens": 3,
            "cache_read_tokens": 1000,
            "cache_creation_tokens": 50,
        }
    }
    paths["metrics"].write_text(json.dumps(row) + "\n")
    return paths


def test_main_raises_a_row_that_stored_the_first_occurrence_sum(
    tmp_path, script, monkeypatch, capsys
):
    paths = _streamed_fixture(tmp_path)
    monkeypatch.setattr("sys.argv", _subagents_argv(paths, "--apply"))

    assert script.main() == 0

    out = capsys.readouterr().out
    assert "1  corrected: stored the first-occurrence sum" in out
    assert "0 raw-sum row(s) only fell, 1 first-occurrence row(s) only rose" in out
    assert "  output_tokens                         +176\n" in out
    assert "  cache_read_tokens                       +0\n" in out
    row = json.loads(paths["metrics"].read_text())
    assert [row[field] for field in ("input_tokens", "output_tokens")] == [2, 179]
    assert row["tokens_by_model"] == {
        "unknown": {
            "input_tokens": 2,
            "output_tokens": 179,
            "cache_read_tokens": 1000,
            "cache_creation_tokens": 50,
        }
    }
    assert list(row)[-2:] == ["agent_transcript_path", "tokens_by_model"]  # key kept in place


def test_main_refuses_a_rewritten_map_that_does_not_reconcile(
    tmp_path, script, monkeypatch, capsys
):
    # The planner builds the totals and the map by the same rule, so they
    # agree; this is the check that proves it on every run instead of
    # assuming it. A map off by one token must stop the write.
    paths = _streamed_fixture(tmp_path)
    before = paths["metrics"].read_bytes()
    real_apply_plan = script.apply_plan

    def off_by_one(records, plan, model_maps=None):
        out = real_apply_plan(records, plan, model_maps)
        for index in plan:
            out[index]["tokens_by_model"]["unknown"]["output_tokens"] += 1
        return out

    monkeypatch.setattr(script, "apply_plan", off_by_one)
    monkeypatch.setattr("sys.argv", _subagents_argv(paths, "--apply"))

    assert script.main() == 1

    error_output = capsys.readouterr().err
    assert "line 1: tokens_by_model output_tokens sums to 180, the row says 179" in error_output
    assert paths["metrics"].read_bytes() == before


def test_main_names_ambiguous_subagent_transcripts(tmp_path, script, monkeypatch, capsys):
    paths = _subagents_fixture(tmp_path)
    twin = paths["projects"] / "other-project" / "parent" / "subagents"
    twin.mkdir(parents=True)
    # A copy that DIFFERS (one replay fewer), under a directory named for the
    # same parent session - so neither the content nor the row's parent can
    # tell the two apart.
    _write_transcript(twin / "agent-a2.jsonl", _replayed("2026-08-01T11:00:00", "m2", 50, 4))
    monkeypatch.setattr("sys.argv", _subagents_argv(paths))

    assert script.main() == 0

    out = capsys.readouterr().out
    assert (
        "1 id(s) found under more than one project slug with differing copies, left "
        "unindexed (a row may still pick its copy by its parent session): ['a2']"
    ) in out
    # a2's recorded path is stale and its id is ambiguous: uncorrectable.
    assert "Lines: 3 read, 1 would change" in out


def test_main_corrects_a_subagent_whose_copies_under_two_slugs_are_identical(
    tmp_path, script, monkeypatch, capsys
):
    paths = _subagents_fixture(tmp_path)
    twin = paths["projects"] / "other-project" / "parent" / "subagents"
    twin.mkdir(parents=True)
    _write_transcript(twin / "agent-a2.jsonl", _replayed("2026-08-01T11:00:00", "m2", 50, 5))
    monkeypatch.setattr("sys.argv", _subagents_argv(paths))

    assert script.main() == 0

    out = capsys.readouterr().out
    assert "differing copies" not in out
    # a2's stale path now resolves to the one transcript both copies are.
    assert "Lines: 3 read, 2 would change" in out
