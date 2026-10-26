"""scripts/official-usage.py: the opt-in reader of Claude Code's own /usage report."""

import importlib.util
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "official-usage.py"

REPORT = """You are currently using your subscription

Current session: 12% used · resets Oct 25 at 3am (UTC)
Current week (all models): 40% used · resets Oct 30 at 1am (UTC)
Current week (Fable): 7% used
Not a meter: 9% used
"""


def _load(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    return _load("official_usage")


def test_meter_lines_are_parsed_in_order(script: types.ModuleType) -> None:
    assert script.parse_meters(REPORT) == [
        {"label": "Current session", "percent": "12", "resets": "resets Oct 25 at 3am (UTC)"},
        {
            "label": "Current week (all models)",
            "percent": "40",
            "resets": "resets Oct 30 at 1am (UTC)",
        },
        {"label": "Current week (Fable)", "percent": "7", "resets": ""},
    ]
    assert script.parse_meters("") == []


def _fake_run(returncode: int, stdout: str = "") -> Any:
    def run(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout, "")

    return run


def test_read_usage_returns_the_text_only_on_success(
    script: types.ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(0, REPORT))
    assert script.read_usage() == REPORT
    monkeypatch.setattr(subprocess, "run", _fake_run(1, REPORT))
    assert script.read_usage() is None


@pytest.mark.parametrize("error", [FileNotFoundError("claude"), subprocess.TimeoutExpired("c", 1)])
def test_read_usage_survives_a_missing_or_hung_binary(
    script: types.ModuleType, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    def run(*_: Any, **__: Any) -> None:
        raise error

    monkeypatch.setattr(subprocess, "run", run)
    assert script.read_usage() is None


def test_main_prints_the_meters_then_the_raw_report(
    script: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(0, REPORT))
    assert script.main() == 0
    output = capsys.readouterr().out
    assert output.startswith("Current session: 12% used (resets Oct 25 at 3am (UTC))")
    assert "Not a meter: 9% used" in output
    assert "no meter lines" not in output


def test_main_says_so_when_the_format_has_no_meter_lines(
    script: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(0, "something new\n"))
    assert script.main() == 0
    assert "no meter lines found" in capsys.readouterr().out


def test_main_fails_with_a_message_when_the_report_cannot_be_read(
    script: types.ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(2))
    assert script.main() == 1
    assert "could not read /usage" in capsys.readouterr().err


def test_module_guard_exits_with_main_return_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run(0, REPORT))
    with pytest.raises(SystemExit) as excinfo:
        _load("__main__")
    assert excinfo.value.code == 0
