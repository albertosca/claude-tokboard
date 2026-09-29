"""Tests for scripts/check-hooks-py39-compat.py.

The real behaviour this script exists for - catching a hook that fails to
import under Python 3.9 - is verified against the actual system Python
directly (see CLAUDE.md's Gotchas and .github/workflows/ci.yml's
hooks-py39 job), not from here: this repo's own test suite runs under
whatever interpreter invoked pytest, which is never 3.9 in CI. What these
tests cover instead is the script's own logic - every branch of
check_hook() and main() - using fake, throwaway "hook" scripts under
tmp_path so a real 3.9 install isn't a precondition for the ordinary
suite (and the 100% coverage gate) to run at all.
"""

import importlib.util
import stat
import sys
import types
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check-hooks-py39-compat.py"


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    # The filename has a hyphen, so it cannot be imported with a normal
    # `import` statement - load it as a module by path instead.
    spec = importlib.util.spec_from_file_location("check_hooks_py39_compat", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fake_hook(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env python3\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_real_hooks_pass_under_the_interpreter_running_this_suite(script: types.ModuleType) -> None:
    # The actual hooks, run for real - the same check main() runs by
    # default. This is also a real (if incidental) regression test for the
    # two hooks themselves: it would fail if either stopped importing or
    # writing a record, on ANY interpreter, not just 3.9.
    assert script.main() == 0


def test_check_hook_reports_a_nonzero_exit(tmp_path: Path, script: types.ModuleType) -> None:
    hook = tmp_path / "broken_import.py"
    _write_fake_hook(hook, "import sys\nsys.exit(1)\n")

    failure = script.check_hook(hook, "sessions.jsonl", "transcript_path", sys.executable)

    assert failure is not None
    assert "exited 1" in failure


def test_check_hook_reports_a_missing_metrics_file(
    tmp_path: Path, script: types.ModuleType
) -> None:
    hook = tmp_path / "does_nothing.py"
    _write_fake_hook(hook, "")  # exits 0, writes nothing

    failure = script.check_hook(hook, "sessions.jsonl", "transcript_path", sys.executable)

    assert failure is not None
    assert "did not write" in failure


def test_check_hook_reports_the_wrong_number_of_records(
    tmp_path: Path, script: types.ModuleType
) -> None:
    hook = tmp_path / "writes_two_lines.py"
    _write_fake_hook(
        hook,
        "import os\n"
        "from pathlib import Path\n"
        'metrics = Path(os.environ["HOME"]) / ".claude" / "metrics" / "sessions.jsonl"\n'
        "metrics.parent.mkdir(parents=True, exist_ok=True)\n"
        'metrics.write_text(\'{"input_tokens": 10, "output_tokens": 5}\\n\' * 2)\n',
    )

    failure = script.check_hook(hook, "sessions.jsonl", "transcript_path", sys.executable)

    assert failure is not None
    assert "expected exactly one record" in failure


def test_check_hook_reports_an_unexpected_record(tmp_path: Path, script: types.ModuleType) -> None:
    hook = tmp_path / "writes_wrong_values.py"
    _write_fake_hook(
        hook,
        "import os, json\n"
        "from pathlib import Path\n"
        'metrics = Path(os.environ["HOME"]) / ".claude" / "metrics" / "sessions.jsonl"\n'
        "metrics.parent.mkdir(parents=True, exist_ok=True)\n"
        'metrics.write_text(json.dumps({"input_tokens": 999, "output_tokens": 1}) + "\\n")\n',
    )

    failure = script.check_hook(hook, "sessions.jsonl", "transcript_path", sys.executable)

    assert failure is not None
    assert "unexpected record" in failure


def test_check_hook_reports_a_missing_incomplete_usage_messages_value(
    tmp_path: Path, script: types.ModuleType
) -> None:
    # The synthetic transcript's first message is never finalised and a
    # second message follows it, so a hook that correctly runs
    # parse_transcript must write incomplete_usage_messages=1 - a fake hook
    # that writes the right token totals but omits it (or gets the count
    # wrong) is exactly the shape of a hook whose incomplete-usage branch
    # silently regressed, and must be caught here, not just by the real
    # hooks happening to still get it right.
    hook = tmp_path / "omits_incomplete_usage.py"
    _write_fake_hook(
        hook,
        "import os, json\n"
        "from pathlib import Path\n"
        'metrics = Path(os.environ["HOME"]) / ".claude" / "metrics" / "sessions.jsonl"\n'
        "metrics.parent.mkdir(parents=True, exist_ok=True)\n"
        'metrics.write_text(json.dumps({"input_tokens": 10, "output_tokens": 5}) + "\\n")\n',
    )

    failure = script.check_hook(hook, "sessions.jsonl", "transcript_path", sys.executable)

    assert failure is not None
    assert "incomplete_usage_messages" in failure


def test_check_hook_reports_a_missing_claude_code_version_value(
    tmp_path: Path, script: types.ModuleType
) -> None:
    # Backlog 2026-09-28 #5: both synthetic entries carry the same
    # "version", so a hook that correctly runs parse_transcript must write
    # claude_code_version equal to it - a fake hook that writes the right
    # token totals and incomplete_usage_messages but omits the version (or
    # gets it wrong) is exactly the shape of a hook whose new field
    # silently regressed.
    hook = tmp_path / "omits_claude_code_version.py"
    _write_fake_hook(
        hook,
        "import os, json\n"
        "from pathlib import Path\n"
        'metrics = Path(os.environ["HOME"]) / ".claude" / "metrics" / "sessions.jsonl"\n'
        "metrics.parent.mkdir(parents=True, exist_ok=True)\n"
        "metrics.write_text(json.dumps({\n"
        '    "input_tokens": 10, "output_tokens": 5, "incomplete_usage_messages": 1\n'
        '}) + "\\n")\n',
    )

    failure = script.check_hook(hook, "sessions.jsonl", "transcript_path", sys.executable)

    assert failure is not None
    assert "claude_code_version" in failure


def test_main_reports_every_failing_hook_on_stderr_and_exits_1(
    tmp_path: Path, script: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    hook = tmp_path / "broken_import.py"
    _write_fake_hook(hook, "import sys\nsys.exit(1)\n")
    fake_hooks: list[tuple[Path, str, str]] = [(hook, "sessions.jsonl", "transcript_path")]

    exit_code = script.main(hooks=fake_hooks, python_executable=sys.executable)

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "exited 1" in captured.err


def test_main_passing_prints_the_interpreter_it_checked(
    script: types.ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = script.main(python_executable=sys.executable)

    assert exit_code == 0
    captured = capsys.readouterr()
    assert sys.executable in captured.out


def test_module_guard_calls_main_and_exits_with_its_return_code() -> None:
    # `if __name__ == "__main__": sys.exit(main())` is never exercised by
    # loading this module under any other name, including the shared
    # `script` fixture (see the same technique in tests/test_backfill_script.py
    # and friends). Re-loading it with __name__ pre-set to "__main__" runs
    # the real file with the real HOOKS list - safe, because main()'s
    # default path is check_hook(), which isolates each hook it launches
    # behind its own throwaway HOME already (see the passing case above).
    spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 0
