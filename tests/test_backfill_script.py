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
        "1.00x (no duplicates)": 1,  # 1.000
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
    with path.open("ab") as f:
        f.write(b'{"a": "live-append"}\n')

    appended = script.write_atomically(str(path), snapshot, [b'{"a": "corrected"}'], 0o644)

    assert appended == 1
    content = path.read_bytes()
    assert content == b'{"a": "corrected"}\n{"a": "live-append"}\n'


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

    def mkstemp_then_unlink(
        suffix: str | None = None,
        prefix: str | None = None,
        dir: str | None = None,
        text: bool = False,
    ) -> tuple[int, str]:
        handle, name = real_mkstemp(suffix=suffix, prefix=prefix, dir=dir, text=text)
        Path(name).unlink()
        return handle, name

    monkeypatch.setattr(script.tempfile, "mkstemp", mkstemp_then_unlink)

    with pytest.raises(RuntimeError, match="not a plain append"):
        script.write_atomically(str(path), snapshot, [b'{"a": "corrected"}'], 0o644)


def test_main_writes_backfill_state_after_a_successful_apply(tmp_path, script, monkeypatch):
    # No transcript exists for "s1" here, so plan_corrections finds nothing
    # to correct and main() takes the `if not plan:` branch. This exercises
    # the state-write call on the EMPTY-plan path (still real: this is what
    # every run after the file is fully caught up looks like) - the
    # non-empty path, which drives the backup/write/verify block, is
    # covered separately below.
    metrics_file = tmp_path / "sessions.jsonl"
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    backup_dir = tmp_path / "backups"
    state_path = tmp_path / "backfill_state.json"
    metrics_file.write_text(
        '{"session_id": "s1", "timestamp_end": "2026-08-01T00:00:00", '
        '"input_tokens": 0, "output_tokens": 0, '
        '"cache_read_tokens": 0, "cache_creation_tokens": 0}\n'
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_dir),
            "--backup-dir",
            str(backup_dir),
            "--apply",
            "--state-file",
            str(state_path),
        ],
    )
    exit_code = script.main()

    assert exit_code == 0
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["cutoff"]
    assert "applied_at" in state
    assert state["corrected_rows"] == 0


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
    with path.open("w") as f:
        for timestamp, message_id, usage in entries:
            f.write(
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
    projects_dir = tmp_path / "projects"
    # index_transcripts globs <projects_dir>/*/<session_id>.jsonl.
    slug_dir = projects_dir / "some-project"
    slug_dir.mkdir(parents=True)
    backup_dir = tmp_path / "backups"
    state_path = tmp_path / "backfill_state.json"

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_dir / "s1.jsonl",
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
            str(projects_dir),
            "--backup-dir",
            str(backup_dir),
            "--apply",
            "--state-file",
            str(state_path),
        ],
    )
    exit_code = script.main()

    assert exit_code == 0

    # the backup block ran and left exactly one verified backup behind
    backups = list(backup_dir.glob("sessions.jsonl.backfill-*.bak"))
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
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_dir),
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
    projects_dir = tmp_path / "projects"
    slug_dir = projects_dir / "some-project"
    slug_dir.mkdir(parents=True)

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_dir / "s1.jsonl",
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
            str(projects_dir),
        ],
    )

    exit_code = script.main()

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "FIELD VERIFICATION FAILED" in err
    assert "key set changed" in err
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
    projects_dir = tmp_path / "projects"
    slug_dir = projects_dir / "some-project"
    slug_dir.mkdir(parents=True)

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
        slug_dir / "s1.jsonl",
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
            str(projects_dir),
        ],
    )

    exit_code = script.main()

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "MONOTONICITY REGRESSION in ['s1']" in err


def test_main_dry_run_returns_zero_and_writes_nothing(tmp_path, script, monkeypatch, capsys):
    metrics_file = tmp_path / "sessions.jsonl"
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
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
            str(projects_dir),
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
    projects_dir = tmp_path / "projects"
    slug_dir = projects_dir / "some-project"
    slug_dir.mkdir(parents=True)
    backup_dir = tmp_path / "backups"
    state_path = tmp_path / "backfill_state.json"

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_dir / "s1.jsonl",
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
            str(projects_dir),
            "--backup-dir",
            str(backup_dir),
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
    projects_dir = tmp_path / "projects"
    slug_dir = projects_dir / "some-project"
    slug_dir.mkdir(parents=True)
    backup_dir = tmp_path / "backups"
    state_path = tmp_path / "backfill_state.json"

    usage = {
        "input_tokens": 100,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    _write_transcript(
        slug_dir / "s1.jsonl",
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

    def lying_write_atomically(*args: object, **kwargs: object) -> int:
        real_write_atomically(*args, **kwargs)
        return 999

    monkeypatch.setattr(script, "write_atomically", lying_write_atomically)

    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics_file),
            "--projects-dir",
            str(projects_dir),
            "--backup-dir",
            str(backup_dir),
            "--apply",
            "--state-file",
            str(state_path),
        ],
    )

    exit_code = script.main()

    assert exit_code == 1
    assert "LINE COUNT MISMATCH: expected 1000, got 1" in capsys.readouterr().err
    assert not state_path.exists()


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
