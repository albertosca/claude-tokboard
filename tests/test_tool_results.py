"""metrics_core.tool_results and scripts/tool-results-report.py: tool output volume."""

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from metrics_core.tool_results import bash_kind, summarize, tool_result_sizes

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "tool-results-report.py"


def _use(identifier: str, name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": identifier, "name": name, "input": arguments}]
        },
    }


def _result(identifier: str, content: Any, *, sidechain: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "isSidechain": sidechain,
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": identifier, "content": content}]
        },
    }


def _write(path: Path, entries: list[Any], extra_lines: tuple[str, ...] = ()) -> Path:
    lines = [json.dumps(entry) for entry in entries] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.mark.parametrize(
    ("command", "kind"),
    [
        ("grep -rn foo src", "search"),
        ("rg foo", "search"),
        ("/usr/bin/find . -name x", "search"),
        ("cat file.txt", "read"),
        ("sed -n 1,5p file", "read"),
        ("claude -p hi", "claude"),
        ("git status", "git"),
        ("FOO=1 BAR=2 git log", "git"),
        ("pytest tests/", "tests"),
        ("python3 -B -m pytest tests/", "tests"),
        ("npm test", "tests"),
        ("mix test", "tests"),
        ("go test ./...", "tests"),
        ("npm install", "other"),
        ("python3 script.py", "other"),
        ("python3 -m http.server", "other"),
        ("python3 -m", "other"),
        ("", "other"),
        ("FOO=1", "other"),
        (None, "other"),
        (42, "other"),
    ],
)
def test_bash_kind(command: Any, kind: str) -> None:
    assert bash_kind(command) == kind


def test_sizes_pair_use_with_result_and_measure_both_content_shapes(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "a.jsonl",
        [
            _use("t1", "Bash", command="grep x ."),
            _result("t1", "x" * 10),
            _use("t2", "Read", file_path="f"),
            _result(
                "t2", [{"type": "text", "text": "abc"}, {"type": "image"}, "stray", {"text": 5}]
            ),
            _use("t3", "Bash", command="git log"),
            _result("t3", None, sidechain=True),
        ],
    )
    rows = list(tool_result_sizes(path, set()))
    assert rows == [
        {"tool": "Bash", "kind": "search", "characters": 10, "sidechain": False},
        {"tool": "Read", "kind": None, "characters": 3, "sidechain": False},
        {"tool": "Bash", "kind": "git", "characters": 0, "sidechain": True},
    ]


def test_a_call_id_seen_in_an_earlier_file_is_counted_once(tmp_path: Path) -> None:
    entries = [_use("t1", "Read", file_path="f"), _result("t1", "abcd")]
    first = _write(tmp_path / "a.jsonl", entries)
    replay = _write(tmp_path / "b.jsonl", entries)
    seen: set[str] = set()
    assert len(list(tool_result_sizes(first, seen))) == 1
    assert list(tool_result_sizes(replay, seen)) == []


def test_malformed_and_unrelated_lines_are_skipped(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "a.jsonl",
        [
            "tool_use but a bare string",
            {"message": "tool_result as text"},
            {"message": {"content": "tool_use"}},
            {"message": {"content": ["tool_result", {"type": "tool_use", "id": 7, "name": "X"}]}},
            _result("never-announced", "zz"),
            {
                "message": {
                    "content": [{"type": "tool_use", "id": "t9", "name": "Bash", "input": 1}]
                }
            },
            _result("t9", "yy"),
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": 3}]}},
            {"type": "assistant", "text": "no markers here"},
        ],
        extra_lines=("{not json tool_use",),
    )
    assert list(tool_result_sizes(path, set())) == [
        {"tool": "Bash", "kind": "other", "characters": 2, "sidechain": False}
    ]


def _rows() -> list[dict[str, Any]]:
    def row(
        tool: str, kind: str | None, characters: int, sidechain: bool = False
    ) -> dict[str, Any]:
        return {"tool": tool, "kind": kind, "characters": characters, "sidechain": sidechain}

    return [
        row("Bash", "search", 9_000),
        row("Bash", "git", 1_000, sidechain=True),
        row("Read", None, 30_000),
        row("Read", None, 10_000),
        row("Edit", None, 0),
    ]


def test_summarize_totals_shares_and_cap_simulation() -> None:
    summary = summarize(_rows())
    assert summary["calls"] == 5
    assert summary["characters"] == 50_000
    assert summary["estimated_tokens"] == 16_666
    assert summary["sidechain_share"] == pytest.approx(0.02)
    assert [entry["tool"] for entry in summary["tools"]] == ["Read", "Bash", "Edit"]
    read = summary["tools"][0]
    assert read["calls"] == 2
    assert read["characters"] == 40_000
    assert read["mean_characters"] == 20_000
    assert read["largest_characters"] == 30_000
    assert read["share"] == pytest.approx(0.8)
    assert [(kind["kind"], kind["share"]) for kind in summary["bash_kinds"]] == [
        ("search", pytest.approx(0.9)),
        ("git", pytest.approx(0.1)),
    ]
    assert summary["caps"] == [
        {"tool": "Bash", "cap": 8_000, "trimmed_characters": 1_000, "trimmed_share": 0.1},
        {"tool": "Read", "cap": 20_000, "trimmed_characters": 10_000, "trimmed_share": 0.25},
    ]


def test_summarize_takes_custom_caps_and_survives_no_rows() -> None:
    assert summarize(_rows(), {"Read": 5_000})["caps"][0]["trimmed_characters"] == 30_000
    empty = summarize([])
    assert empty["calls"] == 0
    assert empty["sidechain_share"] == 0.0
    assert empty["caps"][0]["trimmed_share"] == 0.0


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("tool_results_report", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_report_reads_every_project_once_and_writes_nothing(
    script: types.ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "my-api"
    project.mkdir(parents=True)
    entries = [_use("t1", "Bash", command="cat f"), _result("t1", "x" * 12_000)]
    _write(project / "one.jsonl", entries)
    _write(project / "two.jsonl", entries)
    before = sorted(path.name for path in tmp_path.rglob("*"))
    arguments = ["--projects-dir", str(tmp_path / "projects"), "--bash-cap", "10000"]
    monkeypatch.setattr("sys.argv", ["tool-results-report.py", *arguments])
    assert script.main() == 0
    output = capsys.readouterr().out
    assert "1 tool calls, 12,000 characters (~4,000 tokens, estimated)" in output
    assert "Bash" in output and "read" in output
    assert "cap 10,000: trims 2,000 characters (16.7%)" in output
    assert sorted(path.name for path in tmp_path.rglob("*")) == before


def test_module_guard_calls_main_and_exits_with_its_return_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["tool-results-report.py", "--projects-dir", str(tmp_path)])
    spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 0
