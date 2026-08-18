import importlib.util
import json
import os
import stat

import pytest

SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "backfill-token-dedup.py"
)


@pytest.fixture(scope="module")
def script():
    # The filename has a hyphen, so it cannot be imported with a normal
    # `import` statement - load it as a module by path instead.
    spec = importlib.util.spec_from_file_location("backfill_token_dedup", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_write_atomically_writes_new_lines_and_preserves_mode(tmp_path, script):
    path = tmp_path / "sessions.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    os.chmod(path, 0o600)
    snapshot = path.read_bytes()

    appended = script.write_atomically(str(path), snapshot, [b'{"a": 2}'], 0o600)

    assert appended == 0
    assert path.read_bytes() == b'{"a": 2}\n'
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_write_atomically_preserves_a_concurrent_append_verbatim(tmp_path, script):
    path = tmp_path / "sessions.jsonl"
    path.write_bytes(b'{"a": 1}\n')
    snapshot = path.read_bytes()
    # Simulate a live Stop hook appending a row after the snapshot was read.
    with open(path, "ab") as f:
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
    metrics_file.write_text('{"session_id": "s1", "timestamp_end": "2026-08-01T00:00:00", '
                             '"input_tokens": 0, "output_tokens": 0, '
                             '"cache_read_tokens": 0, "cache_creation_tokens": 0}\n')

    monkeypatch.setattr("sys.argv", [
        "backfill-token-dedup.py",
        "--metrics-file", str(metrics_file),
        "--projects-dir", str(projects_dir),
        "--backup-dir", str(backup_dir),
        "--apply",
        "--state-file", str(state_path),
    ])
    exit_code = script.main()

    assert exit_code == 0
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    assert state["cutoff"]
    assert "applied_at" in state
    assert state["corrected_rows"] == 0


def _write_transcript(path, entries):
    """entries: list of (timestamp, message_id, usage_dict). Mirrors
    tests/test_backfill.py's write_transcript/usage helpers - see that
    file for the JSON shape index_transcripts/scan_transcript expect."""
    with open(path, "w") as f:
        for timestamp, message_id, usage in entries:
            f.write(json.dumps({
                "type": "assistant",
                "timestamp": timestamp,
                "message": {"id": message_id, "usage": usage},
            }) + "\n")


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
        "input_tokens": 100, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }
    _write_transcript(slug_dir / "s1.jsonl", [
        ("2026-08-01T10:00:00", "msg-1", usage),
        ("2026-08-01T10:00:01", "msg-1", usage),  # replay: the broken hook's bug
    ])
    # deduped as-of sum is 100 (one real message); the broken hook summed
    # both copies and stored the raw sum, 200.
    metrics_file.write_text(json.dumps({
        "session_id": "s1", "timestamp_end": "2026-08-01T10:00:01",
        "input_tokens": 200, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
    }) + "\n")

    monkeypatch.setattr("sys.argv", [
        "backfill-token-dedup.py",
        "--metrics-file", str(metrics_file),
        "--projects-dir", str(projects_dir),
        "--backup-dir", str(backup_dir),
        "--apply",
        "--state-file", str(state_path),
    ])
    exit_code = script.main()

    assert exit_code == 0

    # the backup block ran and left exactly one verified backup behind
    backups = list(backup_dir.glob("sessions.jsonl.backfill-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b'{"session_id": "s1", "timestamp_end": ' \
        b'"2026-08-01T10:00:01", "input_tokens": 200, "output_tokens": 0, ' \
        b'"cache_read_tokens": 0, "cache_creation_tokens": 0}\n'

    # write_atomically actually ran, through main(): the live file now
    # holds the corrected (deduped) value, not the original raw one.
    corrected = json.loads(metrics_file.read_text().strip())
    assert corrected["input_tokens"] == 100

    state = json.loads(state_path.read_text())
    assert state["corrected_rows"] == 1
    assert state["cutoff"]
    assert "applied_at" in state
