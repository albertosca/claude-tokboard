"""scripts/backfill-calls.py: seed calls.jsonl and limit-hits.jsonl from the transcripts on disk."""

import hashlib
import importlib.util
import json
import shlex
import sys
import types
from pathlib import Path

import pytest
from calls_fixtures import assistant_entry, limit_entry, write_transcript
from metrics_core import calls_store
from pasted_command import (
    WORKING_DIRECTORIES,
    enter_working_directory,
    names_script,
    run_as_pasted,
)

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill-calls.py"


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("backfill_calls", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, Path]:
    """(projects directory, metrics directory) holding two transcripts: the
    second replays m1 (a resumed session) and adds m3 plus a rejection."""
    projects = tmp_path / "projects" / "my-api"
    projects.mkdir(parents=True)
    write_transcript(
        projects / "session-1.jsonl",
        [
            assistant_entry("m1", timestamp="2026-10-01T10:00:00.000Z"),
            assistant_entry("m2", timestamp="2026-10-02T10:00:00.000Z"),
        ],
    )
    write_transcript(
        projects / "session-2.jsonl",
        [
            assistant_entry("m1", timestamp="2026-10-01T10:00:00.000Z"),
            assistant_entry("m3", timestamp="2026-10-03T10:00:00.000Z"),
            limit_entry(),
        ],
    )
    metrics = tmp_path / "metrics"
    metrics.mkdir()
    (metrics / "sessions.jsonl").write_text('{"session_id": "session-1"}\n')
    return tmp_path / "projects", metrics


def _argv(world: tuple[Path, Path], *extra: str) -> list[str]:
    projects, metrics = world
    return [
        "backfill-calls.py",
        "--projects-dir",
        str(projects),
        "--metrics-dir",
        str(metrics),
        *extra,
    ]


def test_the_dry_run_reports_and_writes_nothing(
    script: types.ModuleType,
    world: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _digest(world[1])
    monkeypatch.setattr("sys.argv", _argv(world))

    assert script.main() == 0

    output = capsys.readouterr().out
    assert "transcripts scanned: 2" in output
    assert "unique calls: 3" in output
    assert "duplicates across transcripts: 1" in output
    assert "calls to write: 3" in output
    assert "limit hits to write: 1" in output
    assert "earliest day reachable: 2026-10-01" in output
    assert "dry run" in output
    assert _digest(world[1]) == before


def test_apply_writes_each_call_once_and_seeds_the_cursor(
    script: types.ModuleType, world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", _argv(world, "--apply"))

    assert script.main() == 0

    metrics = world[1]
    raw = (metrics / "calls.jsonl").read_text().splitlines()
    assert len(raw) == 3, "the replayed m1 is written once"
    assert [row["message_id"] for row in calls_store.read_calls(metrics / "calls.jsonl")] == [
        "m1",
        "m2",
        "m3",
    ]
    assert len(calls_store.read_limit_hits(metrics / "limit-hits.jsonl")) == 1
    assert set(calls_store.load_cursor(metrics)) == {
        str(world[0] / "my-api" / "session-1.jsonl"),
        str(world[0] / "my-api" / "session-2.jsonl"),
    }


def test_a_second_apply_writes_nothing(
    script: types.ModuleType,
    world: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.argv", _argv(world, "--apply"))
    script.main()
    after_first = _digest(world[1])
    capsys.readouterr()

    assert script.main() == 0

    assert "calls to write: 0" in capsys.readouterr().out
    assert _digest(world[1]) == after_first


def test_ids_the_hooks_already_stored_are_skipped(
    script: types.ModuleType, world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = calls_store.call_row(assistant_entry("m2"), "session-1", 0)
    assert stored is not None
    calls_store.append_rows(world[1] / "calls.jsonl", [stored])
    monkeypatch.setattr("sys.argv", _argv(world, "--apply"))

    script.main()

    rows = (world[1] / "calls.jsonl").read_text().splitlines()
    assert sorted(json.loads(line)["message_id"] for line in rows) == ["m1", "m2", "m3"]


def test_apply_never_touches_the_existing_metrics_files(
    script: types.ModuleType, world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = world[1] / "sessions.jsonl"
    before = hashlib.sha256(sessions.read_bytes()).hexdigest()
    monkeypatch.setattr("sys.argv", _argv(world, "--apply"))

    script.main()

    assert hashlib.sha256(sessions.read_bytes()).hexdigest() == before


def test_hooks_continue_from_the_seeded_cursor(
    script: types.ModuleType, world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", _argv(world, "--apply"))
    script.main()
    transcript = world[0] / "my-api" / "session-1.jsonl"
    with transcript.open("a") as handle:
        handle.write(json.dumps(assistant_entry("m4", timestamp="2026-10-04T10:00:00.000Z")) + "\n")

    calls_store.log_transcript(str(transcript), world[1])

    raw = (world[1] / "calls.jsonl").read_text().splitlines()
    assert len(raw) == 4
    assert json.loads(raw[-1])["message_id"] == "m4"


def test_a_missing_projects_directory_is_an_empty_run(
    script: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-calls.py",
            "--projects-dir",
            str(tmp_path / "nope"),
            "--metrics-dir",
            str(tmp_path / "m"),
        ],
    )

    assert script.main() == 0

    output = capsys.readouterr().out
    assert "transcripts scanned: 0" in output
    assert "earliest day reachable: none" in output


def test_a_cursor_that_loads_empty_is_called_out(
    script: types.ModuleType,
    world: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (world[1] / calls_store.CURSOR_FILE_NAME).write_text("not json")
    monkeypatch.setattr("sys.argv", _argv(world))

    script.main()

    assert "cursor file could not be read" in capsys.readouterr().out


@pytest.mark.parametrize("where", WORKING_DIRECTORIES)
def test_the_printed_apply_command_runs_when_pasted(
    script: types.ModuleType,
    world: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    where: str,
) -> None:
    directory = enter_working_directory(where, tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", _argv(world))
    script.main()
    printed = capsys.readouterr().out
    marker = "to apply, run: "
    words = shlex.split(printed[printed.index(marker) + len(marker) :].splitlines()[0])
    assert words[0] == "python3"
    assert names_script(words[1], SCRIPT_PATH, directory)

    recording = run_as_pasted(words, tmp_path, directory)

    assert recording.returncode == 0, recording.stderr
    assert len(calls_store.read_calls(world[1] / "calls.jsonl")) == 3


def test_an_unreadable_transcript_is_skipped_not_fatal(
    script: types.ModuleType, world: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A directory named like a transcript cannot be opened as a file, on any platform.
    (world[0] / "my-api" / "broken.jsonl").mkdir()
    monkeypatch.setattr("sys.argv", _argv(world, "--apply"))

    assert script.main() == 0

    assert len(calls_store.read_calls(world[1] / "calls.jsonl")) == 3


def test_calls_without_any_id_are_keyed_by_position_and_counted(
    script: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    projects = tmp_path / "projects"
    projects.mkdir()
    anonymous = assistant_entry("x")
    del anonymous["message"]["id"], anonymous["requestId"], anonymous["uuid"]
    undated = assistant_entry("y")
    del undated["timestamp"]
    write_transcript(projects / "session-1.jsonl", [anonymous, undated])
    metrics = tmp_path / "metrics"
    monkeypatch.setattr(
        "sys.argv",
        ["backfill-calls.py", "--projects-dir", str(projects), "--metrics-dir", str(metrics)],
    )

    script.main()

    output = capsys.readouterr().out
    assert "calls keyed by a fallback id (no message id): 1" in output
    assert "earliest day reachable: 2026-10-01" in output


def test_module_guard_calls_main_and_exits_with_its_return_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Re-load the real file as __main__; the paths point under tmp_path, so the
    # dry run can never reach the real ~/.claude.
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill-calls.py",
            "--projects-dir",
            str(tmp_path / "none"),
            "--metrics-dir",
            str(tmp_path / "metrics"),
        ],
    )
    spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 0
