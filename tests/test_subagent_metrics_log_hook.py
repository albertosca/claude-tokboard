"""Tests for hooks/subagent-metrics-log.py.

DANGER: this module's METRICS_DIRECTORY/METRICS_FILE constants resolve at
import time to Alberto's real, irreplaceable metrics history
(~/.claude/metrics/subagents.jsonl), actively appended to by other running
Claude sessions. monkeypatch.setenv("HOME", ...) does NOT redirect them,
since they are already-resolved Path objects on the module object by the
time any test runs. Every test in this file that can write MUST
monkeypatch.setattr both METRICS_DIRECTORY and METRICS_FILE on the loaded
`script` module before calling anything that appends. Never point those
constants at anything under ~/.claude/.

This module is a near-clone of hooks/metrics-log.py (see
tests/test_metrics_log_hook.py), but it is deliberately NOT shared with
that test file and does NOT import from it: a shared helper would keep
passing while one hook silently drifted from the other, which is exactly
the failure this coverage phase exists to catch. Properties are re-derived
here against this file, not assumed from the twin - this one has no
turn_count and no git_branch, and build_record carries agent_id/agent_type/
agent_transcript_path instead.
"""

import importlib.util
import io
import json
import stat
import sys
import types
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "hooks" / "subagent-metrics-log.py"


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    # The filename has a hyphen, so it cannot be imported with a normal
    # `import` statement - load it as a module by path instead.
    spec = importlib.util.spec_from_file_location("subagent_metrics_log_hook", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolate_metrics(
    monkeypatch: pytest.MonkeyPatch, script: types.ModuleType, tmp_path: Path
) -> Path:
    """Redirect METRICS_DIRECTORY/METRICS_FILE to a throwaway location under
    tmp_path. Must be called by every test that can reach append_record."""
    metrics_directory = tmp_path / "metrics"
    metrics_file = metrics_directory / "subagents.jsonl"
    monkeypatch.setattr(script, "METRICS_DIRECTORY", metrics_directory)
    monkeypatch.setattr(script, "METRICS_FILE", metrics_file)
    return metrics_file


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# parse_transcript
# ---------------------------------------------------------------------------


def test_parse_transcript_skips_blank_lines(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            "",
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "message": {"id": "m1", "usage": {"input_tokens": 1}},
                }
            ),
            "   ",
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["input_tokens"] == 1


def test_parse_transcript_skips_malformed_json_without_aborting(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            "{not json",
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:01Z",
                    "message": {"id": "m1", "usage": {"input_tokens": 3}},
                }
            ),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["input_tokens"] == 3


def test_parse_transcript_first_and_last_non_empty_timestamps_bound_the_range(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"type": "user"}),  # no timestamp at all
            json.dumps({"type": "assistant", "timestamp": "2026-01-01T00:00:00+00:00"}),
            json.dumps({"type": "assistant", "timestamp": "2026-01-01T00:05:00+00:00"}),
            json.dumps({"type": "assistant", "timestamp": "2026-01-01T00:10:00+00:00"}),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["timestamp_start"] == "2026-01-01T00:00:00+00:00"
    assert parsed["timestamp_end"] == "2026-01-01T00:10:00+00:00"


def test_parse_transcript_only_assistant_entries_count(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps(
                {
                    "type": "user",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "message": {"id": "u1", "usage": {"input_tokens": 999}},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:01Z",
                    "message": {"id": "a1", "usage": {"input_tokens": 5}},
                }
            ),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["input_tokens"] == 5


def test_parse_transcript_triple_duplicate_message_id_counted_exactly_once(script, tmp_path):
    # This is the 1.89x inflation bug metrics-log.py's comment cites: a
    # resumed/compacted session replays the same assistant message several
    # times. A repeat of an already-seen message.id must not add its usage
    # or tool calls a second (or third) time.
    path = tmp_path / "t.jsonl"
    entry = {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {
            "id": "dup",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "content": [{"type": "tool_use", "name": "Read"}],
        },
    }
    _write_lines(path, [json.dumps(entry)] * 3)

    parsed = script.parse_transcript(str(path))

    assert parsed["input_tokens"] == 10
    assert parsed["output_tokens"] == 20
    assert parsed["tool_call_count"] == 1


def test_parse_transcript_entries_with_no_id_are_always_counted(script, tmp_path):
    path = tmp_path / "t.jsonl"
    entry = {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"usage": {"input_tokens": 7}},
    }
    _write_lines(path, [json.dumps(entry)] * 2)

    parsed = script.parse_transcript(str(path))

    assert parsed["input_tokens"] == 14


def test_parse_transcript_missing_or_null_usage_coerces_to_zero(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps(
                {"type": "assistant", "timestamp": "2026-01-01T00:00:00Z", "message": {"id": "a"}}
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:01Z",
                    "message": {"id": "b", "usage": None},
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:02Z",
                    "message": {
                        "id": "c",
                        "usage": {
                            "input_tokens": None,
                            "cache_read_input_tokens": None,
                            "cache_creation_input_tokens": None,
                        },
                    },
                }
            ),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["input_tokens"] == 0
    assert parsed["output_tokens"] == 0
    assert parsed["cache_read_tokens"] == 0
    assert parsed["cache_creation_tokens"] == 0


def test_parse_transcript_tool_call_count_counts_tool_use_blocks_only(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "message": {
                        "id": "a",
                        "content": [
                            {"type": "text", "text": "hi"},
                            {"type": "tool_use", "name": "Read"},
                            "not-a-dict",
                            {"type": "tool_use", "name": "Bash"},
                        ],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:01Z",
                    # non-list content must be ignored, not crash
                    "message": {"id": "b", "content": "plain string content"},
                }
            ),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["tool_call_count"] == 2


def test_parse_transcript_duration_is_difference_of_first_and_last_timestamp(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00+00:00"}),
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:01:30+00:00"}),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["duration_seconds"] == 90.0


def test_parse_transcript_single_timestamped_entry_sets_end_equal_to_start(script, tmp_path):
    # A single-entry transcript is a real shape for a very short-lived
    # subagent. timestamp_end feeds every downstream period filter, so a
    # mutation that leaves it None here would silently drop the record from
    # every period view rather than just reporting a different duration.
    # Assert on timestamp_end specifically - a test that only checked
    # duration_seconds would still pass under such a mutation.
    path = tmp_path / "t.jsonl"
    _write_lines(path, [json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00+00:00"})])

    parsed = script.parse_transcript(str(path))

    assert parsed["timestamp_end"] == "2026-01-01T00:00:00+00:00"
    assert parsed["timestamp_start"] == parsed["timestamp_end"]
    assert parsed["duration_seconds"] == 0.0


def test_parse_transcript_duration_is_none_when_no_timestamps_present(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(path, [json.dumps({"type": "user"})])

    parsed = script.parse_transcript(str(path))

    assert parsed["timestamp_start"] is None
    assert parsed["timestamp_end"] is None
    assert parsed["duration_seconds"] is None


def test_parse_transcript_duration_is_none_when_timestamps_are_unparseable(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"type": "user", "timestamp": "not-a-timestamp"}),
            json.dumps({"type": "user", "timestamp": "also-not-one"}),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["duration_seconds"] is None


# ---------------------------------------------------------------------------
# build_record
# ---------------------------------------------------------------------------

_PARSED_STUB = {
    "timestamp_start": "2026-01-01T00:00:00Z",
    "timestamp_end": "2026-01-01T00:01:00Z",
    "duration_seconds": 60.0,
    "input_tokens": 1,
    "output_tokens": 2,
    "cache_read_tokens": 3,
    "cache_creation_tokens": 4,
    "tool_call_count": 6,
    "incomplete_usage_messages": 0,
    "tokens_by_model": {
        "model-alpha": {
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_tokens": 3,
            "cache_creation_tokens": 4,
        }
    },
}

_EXPECTED_RECORD_KEYS = {
    "session_id",
    "agent_id",
    "agent_type",
    "project_path",
    "timestamp_start",
    "timestamp_end",
    "duration_seconds",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "tool_call_count",
    "agent_transcript_path",
    "tokens_by_model",
}


def test_build_record_has_exact_key_set_and_correct_values(script):
    hook_input = {
        "session_id": "sess-1",
        "agent_id": "agent-42",
        "agent_type": "general-purpose",
        "cwd": "/some/project",
        "agent_transcript_path": "/some/agent-transcript.jsonl",
    }

    record = script.build_record(hook_input, _PARSED_STUB)

    # The exact key set is the emitted-identifier signal for this
    # function: agent_id, agent_type and agent_transcript_path are what
    # the dashboard's subagent folding joins on, and a rename would be a
    # silent join failure that a subset check would never catch.
    assert set(record.keys()) == _EXPECTED_RECORD_KEYS
    assert record["session_id"] == "sess-1"
    assert record["agent_id"] == "agent-42"
    assert record["agent_type"] == "general-purpose"
    assert record["project_path"] == "/some/project"
    assert record["agent_transcript_path"] == "/some/agent-transcript.jsonl"
    assert record["duration_seconds"] == 60.0
    assert record["input_tokens"] == 1
    assert record["tool_call_count"] == 6


def test_build_record_missing_optional_fields_default_to_none(script):
    record = script.build_record({}, _PARSED_STUB)

    assert record["session_id"] is None
    assert record["agent_id"] is None
    assert record["agent_type"] is None
    assert record["project_path"] is None
    assert record["agent_transcript_path"] is None


def test_build_record_carries_tokens_by_model_through(script):
    parsed = dict(_PARSED_STUB)
    parsed["tokens_by_model"] = {
        "model-alpha": {
            "input_tokens": 1,
            "output_tokens": 10,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
    }

    record = script.build_record(
        {"session_id": "sess-1", "agent_id": "agent-42", "cwd": "/work/my-api"}, parsed
    )

    assert record["tokens_by_model"] == parsed["tokens_by_model"]


# ---------------------------------------------------------------------------
# append_record
# ---------------------------------------------------------------------------


def test_append_record_creates_directory_and_writes_the_record(script, tmp_path, monkeypatch):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    assert not metrics_file.parent.exists()

    script.append_record({"a": 1})

    assert metrics_file.exists()
    assert json.loads(metrics_file.read_text().strip()) == {"a": 1}


def test_append_record_appends_one_line_per_call_without_truncating(script, tmp_path, monkeypatch):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)

    script.append_record({"a": 1})
    script.append_record({"a": 2})

    lines = metrics_file.read_text().splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"a": 2}]


def test_append_record_sets_mode_0600_only_when_file_is_new(script, tmp_path, monkeypatch):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)

    script.append_record({"a": 1})
    assert stat.S_IMODE(metrics_file.stat().st_mode) == 0o600

    # An existing file's mode must be left alone on subsequent appends.
    metrics_file.chmod(0o644)
    script.append_record({"a": 2})
    assert stat.S_IMODE(metrics_file.stat().st_mode) == 0o644


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_returns_silently_when_agent_transcript_path_is_missing(script, tmp_path, monkeypatch):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    monkeypatch.setattr(script.sys, "stdin", io.StringIO(json.dumps({"session_id": "s1"})))

    script.main()

    assert not metrics_file.exists()


def test_main_wrong_key_transcript_path_takes_the_early_return(script, tmp_path, monkeypatch):
    # main() reads agent_transcript_path, not transcript_path. A test that
    # feeds transcript_path must exercise the early-return path, even
    # though the key superficially looks like the "right" one and a real
    # file exists at it.
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"id": "m1", "usage": {"input_tokens": 5}},
            }
        )
        + "\n"
    )
    hook_input = {"session_id": "s1", "transcript_path": str(transcript)}
    monkeypatch.setattr(script.sys, "stdin", io.StringIO(json.dumps(hook_input)))

    # Spy on parse_transcript rather than asserting on the file alone: an
    # `and`/`or` swap in the guard still leaves the file unwritten here,
    # because parse_transcript() would go on to raise on the (nonexistent
    # under this wrong key) path and main()'s blanket `except Exception:
    # return` swallows that too. Only "was parse_transcript even invoked"
    # distinguishes "guard returned early" from "guard was bypassed".
    calls = []
    monkeypatch.setattr(script, "parse_transcript", lambda path: calls.append(path))

    script.main()

    assert calls == []
    assert not metrics_file.exists()


def test_main_returns_silently_when_agent_transcript_path_does_not_exist(
    script, tmp_path, monkeypatch
):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    hook_input = {
        "session_id": "s1",
        "agent_transcript_path": str(tmp_path / "missing.jsonl"),
    }
    monkeypatch.setattr(script.sys, "stdin", io.StringIO(json.dumps(hook_input)))

    calls = []
    monkeypatch.setattr(script, "parse_transcript", lambda path: calls.append(path))

    script.main()

    assert calls == []
    assert not metrics_file.exists()


def test_main_appends_exactly_one_record_on_the_happy_path(script, tmp_path, monkeypatch):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)

    transcript = tmp_path / "agent-transcript.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"id": "m1", "usage": {"input_tokens": 5}},
            }
        )
        + "\n"
    )
    hook_input = {
        "session_id": "s1",
        "agent_id": "agent-1",
        "agent_type": "general-purpose",
        "cwd": "/proj",
        "agent_transcript_path": str(transcript),
    }
    monkeypatch.setattr(script.sys, "stdin", io.StringIO(json.dumps(hook_input)))

    script.main()

    lines = metrics_file.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["session_id"] == "s1"
    assert record["agent_id"] == "agent-1"
    assert record["agent_type"] == "general-purpose"
    assert record["input_tokens"] == 5


def test_main_swallows_exceptions_and_writes_nothing(script, tmp_path, monkeypatch):
    # main()'s `except Exception: return` is a coverage trap: an assertion
    # raised from inside its own try block would be swallowed too. Assert
    # only on the file's contents afterwards, never on the absence of an
    # exception.
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    monkeypatch.setattr(script.sys, "stdin", io.StringIO("not valid json"))

    script.main()

    assert not metrics_file.exists()


def test_module_guard_calls_main_and_exits_zero_when_run_as_a_script(tmp_path, monkeypatch):
    # `if __name__ == "__main__": main(); sys.exit(0)` is never exercised by
    # loading this module under any other name, including the shared
    # `script` fixture above. This does not edit
    # hooks/subagent-metrics-log.py - it loads a second, independent copy
    # of it with __name__ pre-set to "__main__", which is a test
    # technique, not a production change. HOME is redirected to tmp_path
    # BEFORE exec, because METRICS_DIRECTORY/METRICS_FILE are computed the
    # moment this fresh copy executes its top-level code - there is no
    # chance to monkeypatch.setattr them afterwards the way every other
    # test here does. The hook input carries no agent_transcript_path, so
    # main() takes its early-return path and nothing is ever written, in
    # tmp_path or anywhere else.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s1"})))

    spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 0
    assert not (tmp_path / ".claude" / "metrics" / "subagents.jsonl").exists()


# --- project_root ------------------------------------------------------
#
# Mirror of the session hook's own three cases. Written out here rather
# than trusted to the sibling file: these are two separate scripts with
# two separate build_record functions, and a test in the other file passes
# just as happily when this one is missing the field entirely.


def test_build_record_records_the_project_root_when_claude_provides_it(script, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/work/my-api")

    record = script.build_record({"agent_id": "a", "cwd": "/work/my-api/docs"}, _PARSED_STUB)

    assert record["project_root"] == "/work/my-api"
    assert record["project_path"] == "/work/my-api/docs", "the cwd is still the cwd"


def test_build_record_omits_the_project_root_when_claude_does_not_provide_it(script, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    record = script.build_record({"agent_id": "a", "cwd": "/x"}, _PARSED_STUB)

    assert "project_root" not in record


def test_build_record_ignores_an_empty_project_root(script, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "")

    record = script.build_record({"agent_id": "a", "cwd": "/x"}, _PARSED_STUB)

    assert "project_root" not in record


def test_build_record_records_incomplete_usage_messages_only_when_there_are_some(script):
    # Absent means zero, as in the session hook.
    flagged = dict(_PARSED_STUB, incomplete_usage_messages=2)

    assert script.build_record({"agent_id": "a1"}, flagged)["incomplete_usage_messages"] == 2
    assert "incomplete_usage_messages" not in script.build_record({"agent_id": "a1"}, _PARSED_STUB)


def test_parse_transcript_counts_unfinalised_subagent_messages(script, tmp_path):
    # The shape Claude Code 2.1.278+ leaves in subagent transcripts: a
    # streamed message with a 1-4 token placeholder and no final entry.
    path = tmp_path / "agent.jsonl"
    entries = [
        {"id": "m1", "stop_reason": None, "usage": {"output_tokens": 2}},
        {"id": "m2", "stop_reason": "end_turn", "usage": {"output_tokens": 80}},
    ]
    path.write_text(
        "".join(json.dumps({"type": "assistant", "message": entry}) + "\n" for entry in entries)
    )

    assert script.parse_transcript(str(path))["incomplete_usage_messages"] == 1
