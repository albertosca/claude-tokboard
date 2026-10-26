"""growth-report.py, instruction-size-history.py and fixed-context-probe.py."""

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from metrics_core.calls_store import CALLS_FILE_NAME, append_rows

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(file_name: str, module_name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def growth() -> types.ModuleType:
    return _load("growth-report.py", "growth_report")


@pytest.fixture(scope="module")
def history() -> types.ModuleType:
    return _load("instruction-size-history.py", "instruction_size_history")


@pytest.fixture(scope="module")
def probe() -> types.ModuleType:
    return _load("fixed-context-probe.py", "fixed_context_probe")


def _git(repository: Path, *arguments: str, date: str | None = None) -> None:
    environment = {**os.environ}
    if date:
        environment.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-c",
            "commit.gpgsign=false",
            *arguments,
        ],
        check=True,
        capture_output=True,
        env=environment,
    )


def _commit_file(repository: Path, name: str, text: str, date: str) -> None:
    (repository / name).write_text(text)
    _git(repository, "add", name)
    _git(repository, "commit", "-q", "-m", "change", date=date)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = tmp_path / "my-api"
    path.mkdir()
    _git(path, "init", "-q")
    return path


def _call(message_id: str, timestamp: str, cache_5m: int, cache_1h: int) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "timestamp": timestamp,
        "input": 1,
        "output": 1,
        "cache_read": 0,
        "cache_5m": cache_5m,
        "cache_1h": cache_1h,
    }


def test_growth_report_joins_weekly_calls_with_commits(
    growth: types.ModuleType,
    repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _commit_file(repository, "a.txt", "1", "2026-10-06T10:00:00+00:00")
    _commit_file(repository, "a.txt", "2", "2026-10-07T10:00:00+00:00")
    metrics = tmp_path / "metrics"
    append_rows(
        metrics / CALLS_FILE_NAME,
        [
            _call("m1", "2026-10-06T09:00:00.000Z", 100, 50),
            _call("m2", "2026-10-07T09:00:00.000Z", 0, 50),
            _call("m3", "2026-10-14T09:00:00.000Z", 10, 0),
        ],
    )
    arguments = ["--metrics-dir", str(metrics), "--repo", str(repository)]
    monkeypatch.setattr(sys, "argv", ["growth-report.py", *arguments])
    assert growth.main() == 0
    lines = capsys.readouterr().out.splitlines()
    first = lines[1].split()
    assert first[:5] == ["2026-W41", "2", "200", "2", "1"]
    assert first[5] == "100"
    assert lines[2].split() == ["2026-W42", "1", "10", "0", "-", "-"]


def test_growth_report_without_repositories_says_commit_columns_are_empty(
    growth: types.ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(sys, "argv", ["growth-report.py", "--metrics-dir", str(tmp_path)])
    assert growth.main() == 0
    assert "no --repo given" in capsys.readouterr().out


def test_growth_report_module_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["growth-report.py", "--metrics-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as excinfo:
        _load("growth-report.py", "__main__")
    assert excinfo.value.code == 0
    capsys.readouterr()


def test_instruction_size_history_flags_a_month_that_grew_past_the_threshold(
    history: types.ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _commit_file(repository, "CLAUDE.md", "x" * 100, "2026-08-10T10:00:00+00:00")
    _commit_file(repository, "CLAUDE.md", "x" * 110, "2026-08-20T10:00:00+00:00")
    _commit_file(repository, "CLAUDE.md", "x" * 120, "2026-09-10T10:00:00+00:00")
    _commit_file(repository, "CLAUDE.md", "x" * 300, "2026-10-10T10:00:00+00:00")
    assert history.monthly_sizes(repository / "CLAUDE.md") == [
        ("2026-08", 110),
        ("2026-09", 120),
        ("2026-10", 300),
    ]
    arguments = ["--file", str(repository / "CLAUDE.md")]
    monkeypatch.setattr(sys, "argv", ["instruction-size-history.py", *arguments])
    assert history.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert "grew" not in lines[0] and "grew" not in lines[1]
    assert "grew more than 25%" in lines[2]


def test_instruction_size_history_fails_for_an_untracked_file(
    history: types.ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (repository / "loose.md").write_text("x")
    monkeypatch.setattr(sys, "argv", ["h", "--file", str(repository / "loose.md")])
    assert history.main() == 1
    assert "no history found" in capsys.readouterr().err


def test_instruction_size_history_module_guard(
    repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (repository / "loose.md").write_text("x")
    monkeypatch.setattr(sys, "argv", ["h", "--file", str(repository / "loose.md")])
    with pytest.raises(SystemExit) as excinfo:
        _load("instruction-size-history.py", "__main__")
    assert excinfo.value.code == 1
    capsys.readouterr()


def _reply(sizes: dict[str, int]) -> Any:
    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        flags = command[command.index("json") + 1 :]
        size = sizes[" ".join(flags)]
        payload = {"usage": {"input_tokens": 1, "cache_creation_input_tokens": size - 1}}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    return run


def test_probe_dry_run_lists_variants_and_sends_nothing(
    probe: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("a dry run must not call claude")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(sys, "argv", ["fixed-context-probe.py"])
    assert probe.main() == 0
    output = capsys.readouterr().out
    assert "dry run" in output and "bare, no tools, minimal prompt" in output


def test_probe_run_attributes_the_difference_between_variants(
    probe: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sizes = {" ".join(flags): 1000 + 10 * index for index, (_, flags) in enumerate(probe.VARIANTS)}
    monkeypatch.setattr(subprocess, "run", _reply(sizes))
    monkeypatch.setattr(sys, "argv", ["fixed-context-probe.py", "--run"])
    assert probe.main() == 0
    output = capsys.readouterr().out
    # full is index 0 (1000) and "no skills" index 1 (1010): the skills list is -10
    assert "skills list" in output and "-10" in output


def test_probe_variant_failures_become_none(
    probe: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: subprocess.CompletedProcess([], 1, "", "boom")
    )
    assert probe.run_variant([], "m") is None
    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: subprocess.CompletedProcess([], 0, "not json", "")
    )
    assert probe.run_variant([], "m") is None

    def missing(*_: Any, **__: Any) -> None:
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", missing)
    assert probe.run_variant([], "m") is None


def test_probe_attribution_shows_a_dash_for_a_failed_variant(
    probe: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert probe.attribute({"full": 10, "no skills": None})[0] == ("skills list", None)
    monkeypatch.setattr(
        subprocess, "run", lambda *_, **__: subprocess.CompletedProcess([], 1, "", "")
    )
    monkeypatch.setattr(sys, "argv", ["fixed-context-probe.py", "--run"])
    assert probe.main() == 0
    assert "ERROR" in capsys.readouterr().out


def test_probe_module_guard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["fixed-context-probe.py"])
    with pytest.raises(SystemExit) as excinfo:
        _load("fixed-context-probe.py", "__main__")
    assert excinfo.value.code == 0
    capsys.readouterr()


def test_a_month_that_ended_with_the_file_deleted_is_skipped(
    history: types.ModuleType, repository: Path
) -> None:
    _commit_file(repository, "CLAUDE.md", "x" * 100, "2026-08-10T10:00:00+00:00")
    _git(repository, "rm", "-q", "CLAUDE.md")
    _git(repository, "commit", "-q", "-m", "remove", date="2026-09-10T10:00:00+00:00")
    assert history.monthly_sizes(repository / "CLAUDE.md") == [("2026-08", 100)]
