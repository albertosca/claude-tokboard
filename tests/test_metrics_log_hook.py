"""Tests for hooks/metrics-log.py.

DANGER: this module's METRICS_DIRECTORY/METRICS_FILE constants resolve at
import time to Alberto's real, irreplaceable metrics history
(~/.claude/metrics/sessions.jsonl - thousands of records going back
months, and actively appended to by other running Claude sessions).
monkeypatch.setenv("HOME", ...) does NOT redirect them, since they are
already-resolved Path objects on the module object by the time any test
runs. Every test in this file that can write MUST monkeypatch.setattr both
METRICS_DIRECTORY and METRICS_FILE on the loaded `script` module before
calling anything that appends. Never point those constants at anything
under ~/.claude/.
"""

import importlib.util
import io
import json
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "hooks" / "metrics-log.py"


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    # The filename has a hyphen, so it cannot be imported with a normal
    # `import` statement - load it as a module by path instead.
    spec = importlib.util.spec_from_file_location("metrics_log_hook", SCRIPT_PATH)
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
    metrics_file = metrics_directory / "sessions.jsonl"
    monkeypatch.setattr(script, "METRICS_DIRECTORY", metrics_directory)
    monkeypatch.setattr(script, "METRICS_FILE", metrics_file)
    return metrics_file


def _write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# get_tmux_window
# ---------------------------------------------------------------------------


def test_get_tmux_window_returns_none_without_tmux_env(script, monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)

    assert script.get_tmux_window() is None


def test_get_tmux_window_returns_stripped_name_on_success(script, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")

    def fake_run(*arguments, **keyword_arguments):
        return subprocess.CompletedProcess(arguments, 0, stdout="  my-window  \n", stderr="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    assert script.get_tmux_window() == "my-window"


def test_get_tmux_window_returns_none_for_whitespace_only_output(script, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")

    def fake_run(*arguments, **keyword_arguments):
        return subprocess.CompletedProcess(arguments, 0, stdout="   \n", stderr="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    assert script.get_tmux_window() is None


def test_get_tmux_window_returns_none_when_subprocess_raises(script, monkeypatch):
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,1234,0")

    def fake_run(*arguments, **keyword_arguments):
        raise OSError("tmux not found")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    assert script.get_tmux_window() is None


# ---------------------------------------------------------------------------
# get_internal_name
# ---------------------------------------------------------------------------


def test_get_internal_name_returns_none_for_falsy_session_id(script, tmp_path):
    assert script.get_internal_name(None, tmp_path) is None
    assert script.get_internal_name("", tmp_path) is None


def test_get_internal_name_defaults_sessions_dir_to_the_real_claude_sessions_path(script):
    # sessions_directory is optional; when omitted it must default to
    # ~/.claude/sessions. This only *reads* that directory (glob + json.load
    # on whatever real session files exist, if any) - it never writes
    # anything, so it is safe to exercise against the real path. A session
    # id that cannot plausibly exist keeps the result deterministic
    # regardless of what real sessions happen to be on this machine.
    assert script.get_internal_name("definitely-not-a-real-session-id-000") is None


def test_get_internal_name_finds_match_via_ppid_file_first(script, tmp_path, monkeypatch):
    sessions_directory = tmp_path / "sessions"
    sessions_directory.mkdir()
    monkeypatch.setattr(script.os, "getppid", lambda: 4242)
    (sessions_directory / "4242.json").write_text(
        json.dumps({"sessionId": "target", "nameSource": None, "name": "from-ppid"})
    )

    assert script.get_internal_name("target", sessions_directory) == "from-ppid"


def test_get_internal_name_falls_through_to_glob_when_ppid_file_missing(
    script, tmp_path, monkeypatch
):
    sessions_directory = tmp_path / "sessions"
    sessions_directory.mkdir()
    monkeypatch.setattr(script.os, "getppid", lambda: 999999)  # no such file
    (sessions_directory / "other.json").write_text(
        json.dumps({"sessionId": "target", "nameSource": None, "name": "from-glob"})
    )

    assert script.get_internal_name("target", sessions_directory) == "from-glob"


def test_get_internal_name_skips_unparseable_json_without_crashing(script, tmp_path, monkeypatch):
    sessions_directory = tmp_path / "sessions"
    sessions_directory.mkdir()
    monkeypatch.setattr(script.os, "getppid", lambda: 999999)
    (sessions_directory / "broken.json").write_text("not valid json{{{")

    assert script.get_internal_name("target", sessions_directory) is None


def test_get_internal_name_skips_when_session_id_does_not_match(script, tmp_path, monkeypatch):
    sessions_directory = tmp_path / "sessions"
    sessions_directory.mkdir()
    monkeypatch.setattr(script.os, "getppid", lambda: 999999)
    (sessions_directory / "other.json").write_text(
        json.dumps({"sessionId": "someone-else", "nameSource": None, "name": "nope"})
    )

    assert script.get_internal_name("target", sessions_directory) is None


def test_get_internal_name_skips_when_name_source_is_not_none(script, tmp_path, monkeypatch):
    # Directly targets the named mutation of flipping `nameSource is None`
    # to `nameSource is not None`: under the mutation this record would
    # match and the test would fail.
    sessions_directory = tmp_path / "sessions"
    sessions_directory.mkdir()
    monkeypatch.setattr(script.os, "getppid", lambda: 999999)
    (sessions_directory / "other.json").write_text(
        json.dumps({"sessionId": "target", "nameSource": "cli-flag", "name": "user-named"})
    )

    assert script.get_internal_name("target", sessions_directory) is None


def test_get_internal_name_returns_none_when_matched_name_is_not_a_string(
    script, tmp_path, monkeypatch
):
    sessions_directory = tmp_path / "sessions"
    sessions_directory.mkdir()
    monkeypatch.setattr(script.os, "getppid", lambda: 999999)
    (sessions_directory / "other.json").write_text(
        json.dumps({"sessionId": "target", "nameSource": None, "name": 42})
    )

    assert script.get_internal_name("target", sessions_directory) is None


def test_get_internal_name_does_not_reparse_the_ppid_file_seen_again_via_glob(
    script, tmp_path, monkeypatch
):
    # The ppid candidate also shows up in the `*.json` glob (same file).
    # This pins the `seen` dedup: json.load must be called exactly once for
    # that path, not twice.
    sessions_directory = tmp_path / "sessions"
    sessions_directory.mkdir()
    monkeypatch.setattr(script.os, "getppid", lambda: 7777)
    (sessions_directory / "7777.json").write_text(
        json.dumps({"sessionId": "someone-else", "nameSource": None, "name": "irrelevant"})
    )

    real_load = script.json.load
    calls = []

    def counting_load(file_handle):
        calls.append(1)
        if len(calls) > 1:
            raise AssertionError("ppid candidate file was parsed more than once")
        return real_load(file_handle)

    monkeypatch.setattr(script.json, "load", counting_load)

    result = script.get_internal_name("target", sessions_directory)

    assert result is None
    assert len(calls) == 1


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

    assert parsed["turn_count"] == 1
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

    assert parsed["turn_count"] == 1
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


def test_parse_transcript_last_entry_carrying_git_branch_wins(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"type": "user", "gitBranch": "first-branch"}),
            json.dumps({"type": "user"}),  # no gitBranch key: must not clear it
            json.dumps({"type": "user", "gitBranch": "second-branch"}),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["git_branch"] == "second-branch"


# ---------------------------------------------------------------------------
# parse_transcript: claude_code_version
# ---------------------------------------------------------------------------


def test_parse_transcript_claude_code_version_absent_when_no_entry_carries_one(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(path, [json.dumps({"type": "user"}), json.dumps({"type": "assistant"})])

    parsed = script.parse_transcript(str(path))

    assert parsed["claude_code_version"] is None
    assert parsed["claude_code_version_first"] is None


def test_parse_transcript_claude_code_version_read_from_any_entry_type(script, tmp_path):
    # version sits at the same top level as gitBranch, not inside message -
    # a "user" entry carries it exactly like an "assistant" one.
    path = tmp_path / "t.jsonl"
    _write_lines(path, [json.dumps({"type": "user", "version": "2.1.260"})])

    parsed = script.parse_transcript(str(path))

    assert parsed["claude_code_version"] == "2.1.260"


def test_parse_transcript_claude_code_version_last_entry_wins_and_first_is_dropped_when_equal(
    script, tmp_path
):
    # Every entry agrees on the version: claude_code_version_first would
    # only ever repeat it, so it stays absent - "keep records small".
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"type": "user", "version": "2.1.260"}),
            json.dumps({"type": "user"}),  # no version key: must not clear it
            json.dumps({"type": "user", "version": "2.1.260"}),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["claude_code_version"] == "2.1.260"
    assert parsed["claude_code_version_first"] is None


def test_parse_transcript_claude_code_version_first_recorded_only_when_it_differs(script, tmp_path):
    # A long session spanning an upgrade mid-way: the LAST version is what
    # produced the tail end of the session's tokens (claude_code_version),
    # and the FIRST is kept alongside it precisely because it differs.
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            json.dumps({"type": "user", "version": "2.1.260"}),
            json.dumps({"type": "user"}),  # no version key: must not clear either bound
            json.dumps({"type": "user", "version": "2.1.284"}),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["claude_code_version"] == "2.1.284"
    assert parsed["claude_code_version_first"] == "2.1.260"


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

    assert parsed["turn_count"] == 1
    assert parsed["input_tokens"] == 5


def test_parse_transcript_triple_duplicate_message_id_counted_exactly_once(script, tmp_path):
    # This is the replay inflation bug the module's own comment cites: a
    # resumed/compacted session replays the same assistant message several
    # times. A repeat of an already-seen message.id must add neither a turn
    # nor its usage a second (or third) time.
    path = tmp_path / "t.jsonl"
    entry = {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"id": "dup", "usage": {"input_tokens": 10, "output_tokens": 20}},
    }
    _write_lines(path, [json.dumps(entry)] * 3)

    parsed = script.parse_transcript(str(path))

    assert parsed["turn_count"] == 1
    assert parsed["input_tokens"] == 10
    assert parsed["output_tokens"] == 20


def _streamed(message_id: str, outputs: list[int], model: str = "model-alpha") -> list[str]:
    # A streamed response is written as one transcript entry per content
    # block, all sharing the message id. Input and cache usage repeat
    # unchanged; output_tokens GROWS block by block (checked 2026-09-23 over
    # every subagent transcript on one disk: the repeated ids all differ only
    # in output_tokens).
    return [
        json.dumps(
            {
                "type": "assistant",
                "timestamp": f"2026-01-01T00:00:{second:02d}Z",
                "message": {
                    "id": message_id,
                    "model": model,
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": output,
                        "cache_read_input_tokens": 1000,
                        "cache_creation_input_tokens": 50,
                    },
                },
            }
        )
        for second, output in enumerate(outputs)
    ]


def test_parse_transcript_counts_a_streamed_message_at_its_peak_usage(script, tmp_path):
    # Keeping the FIRST entry of the id counted 3 output tokens for a message
    # that produced 179 - the undercount that left subagent output several times too low.
    path = tmp_path / "t.jsonl"
    _write_lines(path, _streamed("m1", [3, 40, 179]))

    parsed = script.parse_transcript(str(path))

    assert parsed["turn_count"] == 1
    assert parsed["input_tokens"] == 3
    assert parsed["output_tokens"] == 179
    assert parsed["cache_read_tokens"] == 1000
    assert parsed["cache_creation_tokens"] == 50
    assert parsed["tokens_by_model"] == {
        "model-alpha": {
            "input_tokens": 3,
            "output_tokens": 179,
            "cache_read_tokens": 1000,
            "cache_creation_tokens": 50,
        }
    }


def test_parse_transcript_takes_the_peak_even_when_the_last_entry_is_lower(script, tmp_path):
    # Found: ids in a subagent transcript whose streamed series was
    # replayed interleaved (2, 270, 2, 270, 2). Neither first nor last is
    # right in general; the per-field maximum is.
    path = tmp_path / "t.jsonl"
    _write_lines(path, _streamed("m1", [2, 270, 2, 270, 2]))

    parsed = script.parse_transcript(str(path))

    assert parsed["output_tokens"] == 270
    assert parsed["input_tokens"] == 3
    assert parsed["tokens_by_model"]["model-alpha"]["output_tokens"] == 270


def test_parse_transcript_entries_with_no_id_are_always_counted(script, tmp_path):
    path = tmp_path / "t.jsonl"
    entry = {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"usage": {"input_tokens": 7}},
    }
    _write_lines(path, [json.dumps(entry)] * 2)

    parsed = script.parse_transcript(str(path))

    assert parsed["turn_count"] == 2
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

    assert parsed["turn_count"] == 3
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


def test_parse_transcript_counts_tool_calls_carried_by_later_entries_of_a_message(script, tmp_path):
    # A streamed message is one entry per content block, all sharing the
    # message id: the thinking block first, each tool_use in an entry of its
    # own after it. Counting only the first entry of an id counted 0 here
    # (over real subagent transcripts it counted about a fifth of the
    # distinct tool_use blocks). A replayed entry repeats a block id
    # and must not count twice.
    def entry(second: int, block: dict[str, object]) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "timestamp": f"2026-01-01T00:00:0{second}Z",
                "message": {"id": "m1", "content": [block]},
            }
        )

    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            entry(0, {"type": "thinking", "thinking": "..."}),
            entry(1, {"type": "tool_use", "id": "toolu_1", "name": "Read"}),
            entry(2, {"type": "tool_use", "id": "toolu_2", "name": "Bash"}),
            entry(3, {"type": "tool_use", "id": "toolu_1", "name": "Read"}),  # replay
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["tool_call_count"] == 2
    assert parsed["turn_count"] == 1


def _message(message_id: str, output: int, stop_reason: str | None = None) -> str:
    """One assistant entry; `stop_reason` set marks the entry Claude Code
    writes when a message is final (measured 2026-09-25: every id that has
    one carries its largest output there)."""
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {
                "id": message_id,
                "model": "model-alpha",
                "stop_reason": stop_reason,
                "usage": {"input_tokens": 1, "output_tokens": output},
            },
        }
    )


def test_parse_transcript_counts_messages_that_never_got_a_final_entry(script, tmp_path):
    # m1 streamed placeholders and was never finalised, but m2 came after it,
    # so m1 is over: its usage will never be written.
    path = tmp_path / "t.jsonl"
    _write_lines(path, [_message("m1", 1), _message("m1", 3), _message("m2", 50, "end_turn")])

    parsed = script.parse_transcript(str(path))

    assert parsed["incomplete_usage_messages"] == 1


def test_parse_transcript_does_not_flag_a_streamed_message_that_was_finalised(script, tmp_path):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [_message("m1", 1), _message("m1", 40, "tool_use"), _message("m2", 9, "end_turn")],
    )

    assert script.parse_transcript(str(path))["incomplete_usage_messages"] == 0


def test_parse_transcript_does_not_flag_the_last_message_which_may_still_be_streaming(
    script, tmp_path
):
    # A snapshot taken mid-response sees the newest message without its final
    # entry yet; flagging it would mark every in-progress snapshot.
    path = tmp_path / "t.jsonl"
    _write_lines(path, [_message("m1", 9, "end_turn"), _message("m2", 1)])

    assert script.parse_transcript(str(path))["incomplete_usage_messages"] == 0


def test_parse_transcript_counts_an_unfinalised_message_even_when_it_is_replayed_last(
    script, tmp_path
):
    # "Last" is the message first seen last, not the last line: a replay of
    # an older message at the end of the file does not make it in progress.
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [_message("m1", 2), _message("m2", 9, "end_turn"), _message("m1", 2)],
    )

    assert script.parse_transcript(str(path))["incomplete_usage_messages"] == 1


def test_parse_transcript_replayed_final_entries_do_not_flag_or_double_count(script, tmp_path):
    path = tmp_path / "t.jsonl"
    final = _message("m1", 9, "end_turn")
    _write_lines(path, [final, _message("m2", 1), _message("m2", 5, "end_turn"), final])

    assert script.parse_transcript(str(path))["incomplete_usage_messages"] == 0


def test_parse_transcript_entries_without_an_id_are_never_flagged(script, tmp_path):
    path = tmp_path / "t.jsonl"
    no_id = json.dumps({"type": "assistant", "message": {"usage": {"output_tokens": 2}}})
    _write_lines(path, [no_id, _message("m2", 9, "end_turn"), no_id])

    assert script.parse_transcript(str(path))["incomplete_usage_messages"] == 0


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
    # A session that logs exactly one entry before dying is exactly what
    # reaches this hook on Stop for a very short-lived session. timestamp_end
    # is not just a duration input: filter_sessions_by_window uses it to
    # decide which period a session belongs to, so a None here silently
    # drops the whole record out of every period view rather than just
    # reporting a different duration. Assert on timestamp_end specifically -
    # a test that only checked duration_seconds would still pass under a
    # mutation that breaks this field while leaving the duration arithmetic
    # intact.
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
# parse_transcript: tokens_by_model (per-model attribution)
# ---------------------------------------------------------------------------


def _assistant(message_id: str, model: str, **usage: int) -> str:
    """One assistant entry carrying a model and a usage block. Mirrors the
    real transcript shape: usage keys are the API's own names, which are
    NOT the record's field names (cache_read_input_tokens vs
    cache_read_tokens)."""
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"id": message_id, "model": model, "usage": usage},
        }
    )


def test_parse_transcript_tokens_by_model_attributes_tokens_to_the_message_s_own_model(
    script, tmp_path
):
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            _assistant("a1", "model-alpha", input_tokens=1, output_tokens=10),
            _assistant("a2", "model-beta", input_tokens=2, output_tokens=20),
            _assistant("a3", "model-alpha", cache_read_input_tokens=100),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["tokens_by_model"] == {
        "model-alpha": {
            "input_tokens": 1,
            "output_tokens": 10,
            "cache_read_tokens": 100,
            "cache_creation_tokens": 0,
        },
        "model-beta": {
            "input_tokens": 2,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    }


def test_parse_transcript_tokens_by_model_totals_reconcile_with_the_record_totals(script, tmp_path):
    # The spec's primary invariant: for each token field, the per-model
    # breakdown sums back to the record's own total. This catches a
    # dropped message and double counting - it does NOT catch
    # mis-attribution (every message landing under the wrong model still
    # sums to the same total), which is exactly what
    # test_parse_transcript_tokens_by_model_attributes_tokens_to_the_message_s_own_model
    # exists to catch instead. Neither test subsumes the other.
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            _assistant("a1", "model-alpha", input_tokens=1, cache_creation_input_tokens=7),
            _assistant("a2", "model-beta", output_tokens=20, cache_read_input_tokens=300),
            _assistant("a3", "model-gamma", input_tokens=5, output_tokens=6),
        ],
    )

    parsed = script.parse_transcript(str(path))

    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        assert (
            sum(bucket[field] for bucket in parsed["tokens_by_model"].values()) == parsed[field]
        ), field


def test_parse_transcript_tokens_by_model_replayed_message_counts_once_too(script, tmp_path):
    # The dedup guard must cover the per-model accumulation as well: a
    # resumed/compacted transcript replays earlier turns, and attributing
    # them again would inflate exactly the model that was replayed.
    path = tmp_path / "t.jsonl"
    entry = _assistant("dup", "model-alpha", input_tokens=10, output_tokens=20)
    _write_lines(path, [entry] * 3)

    parsed = script.parse_transcript(str(path))

    assert parsed["tokens_by_model"]["model-alpha"]["input_tokens"] == 10
    assert parsed["tokens_by_model"]["model-alpha"]["output_tokens"] == 20
    assert (
        sum(bucket["input_tokens"] for bucket in parsed["tokens_by_model"].values())
        == parsed["input_tokens"]
    )


def test_parse_transcript_tokens_by_model_excludes_the_synthetic_pseudo_model(script, tmp_path):
    # `<synthetic>` appears in real transcripts carrying zero in all four
    # fields. Carrying nothing, it gets
    # no bucket, so it can never render as a permanently-empty series. The
    # exclusion is conditional on that emptiness, not on the model id - see
    # the test below for the one that does carry tokens.
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            _assistant("a1", "model-alpha", input_tokens=4),
            _assistant("a2", "<synthetic>"),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert "<synthetic>" not in parsed["tokens_by_model"]
    assert parsed["tokens_by_model"] == {
        "model-alpha": {
            "input_tokens": 4,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
    }
    # The synthetic entry still counts as a turn - this filter is about
    # attribution only, and must not change any pre-existing number.
    assert parsed["turn_count"] == 2


def test_parse_transcript_tokens_by_model_buckets_a_synthetic_message_that_carries_tokens(
    script, tmp_path
):
    # The exclusion above is a zero-contribution filter, not a deny-list: a
    # `<synthetic>` message carrying tokens has its usage summed into the
    # record's four totals like any other, so skipping its bucket would
    # break the reconciliation invariant with no error and no signal
    # (reproduced by review: record input_tokens 1005, per-model sum 5).
    # Bucketing it only when it carries something keeps the invariant
    # structural while still never creating an empty series.
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            _assistant("a1", "model-alpha", input_tokens=5),
            _assistant("a2", "<synthetic>", input_tokens=1000, cache_read_input_tokens=7),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["tokens_by_model"]["<synthetic>"] == {
        "input_tokens": 1000,
        "output_tokens": 0,
        "cache_read_tokens": 7,
        "cache_creation_tokens": 0,
    }
    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        assert (
            sum(bucket[field] for bucket in parsed["tokens_by_model"].values()) == parsed[field]
        ), field


def test_parse_transcript_tokens_by_model_is_empty_when_there_are_no_assistant_entries(
    script, tmp_path
):
    path = tmp_path / "t.jsonl"
    _write_lines(path, [json.dumps({"type": "user", "message": {"id": "u1"}})])

    parsed = script.parse_transcript(str(path))

    assert parsed["tokens_by_model"] == {}


def test_parse_transcript_tokens_by_model_buckets_a_missing_model_under_unknown(script, tmp_path):
    # An assistant message with no "model" field must still land somewhere:
    # dropping it silently would let its tokens vanish from the breakdown
    # while the record's own totals (and turn_count) keep counting it -
    # reproduced by review: a message with no model field carrying 1000/2000
    # tokens disappeared from tokens_by_model entirely under the earlier
    # `isinstance(model, str) and ...` guard, with no exception and no
    # reconciliation failure, because that guard skipped the bucket instead
    # of choosing one. "unknown" makes every message land in *some* bucket,
    # so the invariant below holds as a property of the loop's shape, not of
    # what happens to be present in a given transcript.
    path = tmp_path / "t.jsonl"
    _write_lines(
        path,
        [
            _assistant("a1", "model-alpha", input_tokens=1, output_tokens=10),
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "message": {
                        "id": "a2",
                        "usage": {"input_tokens": 1000, "output_tokens": 2000},
                    },
                }
            ),
        ],
    )

    parsed = script.parse_transcript(str(path))

    assert parsed["tokens_by_model"]["unknown"] == {
        "input_tokens": 1000,
        "output_tokens": 2000,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
        assert (
            sum(bucket[field] for bucket in parsed["tokens_by_model"].values()) == parsed[field]
        ), field


# ---------------------------------------------------------------------------
# build_record
# ---------------------------------------------------------------------------

_PARSED_STUB = {
    "timestamp_start": "2026-01-01T00:00:00Z",
    "timestamp_end": "2026-01-01T00:01:00Z",
    "duration_seconds": 60.0,
    "git_branch": "main",
    "claude_code_version": None,
    "claude_code_version_first": None,
    "input_tokens": 1,
    "output_tokens": 2,
    "cache_read_tokens": 3,
    "cache_creation_tokens": 4,
    "turn_count": 5,
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
    "timestamp_start",
    "timestamp_end",
    "duration_seconds",
    "project_path",
    "git_branch",
    "tag",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "turn_count",
    "tool_call_count",
    "tmux_window",
    "internal_name",
    "transcript_path",
    "tokens_by_model",
}


def test_build_record_has_exact_key_set_and_correct_values(script, monkeypatch):
    monkeypatch.setattr(script, "get_tmux_window", lambda: "my-window")
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: "my-name"
    )
    monkeypatch.setenv("CLAUDE_METRIC_TAG", "my-tag")

    hook_input = {
        "session_id": "sess-1",
        "cwd": "/some/project",
        "transcript_path": "/some/transcript.jsonl",
    }

    record = script.build_record(hook_input, _PARSED_STUB)

    # The exact key set is the emitted-identifier signal for this
    # function: a renamed key would be a silent dashboard regression that
    # a subset check would never catch.
    assert set(record.keys()) == _EXPECTED_RECORD_KEYS
    assert record["session_id"] == "sess-1"
    assert record["project_path"] == "/some/project"
    assert record["transcript_path"] == "/some/transcript.jsonl"
    assert record["tag"] == "my-tag"
    assert record["tmux_window"] == "my-window"
    assert record["internal_name"] == "my-name"
    assert record["duration_seconds"] == 60.0
    assert record["git_branch"] == "main"


def test_build_record_tag_is_none_when_env_var_unset(script, monkeypatch):
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )
    monkeypatch.delenv("CLAUDE_METRIC_TAG", raising=False)

    record = script.build_record({"session_id": "s", "cwd": "/x"}, _PARSED_STUB)

    assert record["tag"] is None


def test_build_record_carries_tokens_by_model_through(script, monkeypatch):
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )
    parsed = dict(_PARSED_STUB)
    parsed["tokens_by_model"] = {
        "model-alpha": {
            "input_tokens": 1,
            "output_tokens": 10,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        "model-beta": {
            "input_tokens": 2,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    }

    record = script.build_record({"session_id": "s1", "cwd": "/work/my-api"}, parsed)

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


def test_main_returns_silently_when_transcript_path_is_missing(script, tmp_path, monkeypatch):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    monkeypatch.setattr(script.sys, "stdin", io.StringIO(json.dumps({"session_id": "s1"})))

    script.main()

    assert not metrics_file.exists()


def test_main_returns_silently_when_transcript_path_does_not_exist(script, tmp_path, monkeypatch):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    hook_input = {"session_id": "s1", "transcript_path": str(tmp_path / "missing.jsonl")}
    monkeypatch.setattr(script.sys, "stdin", io.StringIO(json.dumps(hook_input)))

    # Spy on parse_transcript rather than asserting on the file alone: an
    # `and`/`or` swap in the guard (`if not transcript_path or not
    # Path(transcript_path).exists():`) still leaves the file unwritten
    # here, because parse_transcript() would go on to raise
    # FileNotFoundError on the missing path and main()'s blanket
    # `except Exception: return` swallows that too - the file-absence
    # assertion alone cannot tell "guard returned early" apart from "guard
    # was bypassed and the call inside failed instead". Only "was
    # parse_transcript even invoked" distinguishes the two.
    calls = []
    monkeypatch.setattr(script, "parse_transcript", lambda path: calls.append(path))

    script.main()

    assert calls == []
    assert not metrics_file.exists()


def test_main_appends_exactly_one_record_on_the_happy_path(script, tmp_path, monkeypatch):
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )

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
    hook_input = {
        "session_id": "s1",
        "cwd": "/proj",
        "transcript_path": str(transcript),
    }
    monkeypatch.setattr(script.sys, "stdin", io.StringIO(json.dumps(hook_input)))

    script.main()

    lines = metrics_file.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["session_id"] == "s1"
    assert record["input_tokens"] == 5


def test_module_guard_calls_main_and_exits_zero_when_run_as_a_script(tmp_path, monkeypatch):
    # `if __name__ == "__main__": main(); sys.exit(0)` is never exercised by
    # loading this module under any other name, including the shared
    # `script` fixture above. This does not edit hooks/metrics-log.py - it
    # loads a second, independent copy of it with __name__ pre-set to
    # "__main__", which is a test technique, not a production change.
    # HOME is redirected to tmp_path BEFORE exec, because
    # METRICS_DIRECTORY/METRICS_FILE are computed the moment this fresh copy
    # executes its top-level code - there is no chance to
    # monkeypatch.setattr them afterwards the way every other test here
    # does. The hook input carries no transcript_path, so main() takes its
    # early-return path and nothing is ever written, in tmp_path or
    # anywhere else.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": "s1"})))

    spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 0
    assert not (tmp_path / ".claude" / "metrics" / "sessions.jsonl").exists()


def test_main_swallows_exceptions_and_writes_nothing(script, tmp_path, monkeypatch):
    # main()'s `except Exception: return` is a coverage trap: an assertion
    # raised from inside its own try block would be swallowed too. Assert
    # only on the file's contents afterwards, never on the absence of an
    # exception.
    metrics_file = _isolate_metrics(monkeypatch, script, tmp_path)
    monkeypatch.setattr(script.sys, "stdin", io.StringIO("not valid json"))

    script.main()

    assert not metrics_file.exists()


# --- project_root ------------------------------------------------------
#
# The cwd alone cannot say where the project is: it is often a
# subdirectory, and a subdirectory is what ends up naming the project in
# the dashboard. Claude Code hands hooks CLAUDE_PROJECT_DIR, which is the
# project root the session started in - authoritative where every
# heuristic is a guess. Stored RAW, beside the cwd rather than instead of
# it, so the interpretation stays in the display layer.


def test_build_record_records_the_project_root_when_claude_provides_it(script, monkeypatch):
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/work/my-api")

    record = script.build_record({"session_id": "s", "cwd": "/work/my-api/docs"}, _PARSED_STUB)

    assert record["project_root"] == "/work/my-api"
    assert record["project_path"] == "/work/my-api/docs", "the cwd is still the cwd"


def test_build_record_omits_the_project_root_when_claude_does_not_provide_it(script, monkeypatch):
    # Every Claude Code old enough to predate the variable, and any other
    # caller of the hook, lands here. An ABSENT key, not a null one: a
    # null would be a value the reader has to interpret, and the whole
    # historical corpus has no key at all.
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)

    record = script.build_record({"session_id": "s", "cwd": "/x"}, _PARSED_STUB)

    assert "project_root" not in record


def test_build_record_ignores_an_empty_project_root(script, monkeypatch):
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "")

    record = script.build_record({"session_id": "s", "cwd": "/x"}, _PARSED_STUB)

    assert "project_root" not in record


def test_build_record_records_incomplete_usage_messages_only_when_there_are_some(
    script, monkeypatch
):
    # Absent means zero: the whole historical corpus has no such key, and a
    # key on every row would only grow the file.
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )
    flagged = dict(_PARSED_STUB, incomplete_usage_messages=3)

    assert script.build_record({"session_id": "s"}, flagged)["incomplete_usage_messages"] == 3
    assert "incomplete_usage_messages" not in script.build_record({"session_id": "s"}, _PARSED_STUB)


# --- claude_code_version -------------------------------------------------
#
# Backlog 2026-09-28 #5: absent means unknown, same reasoning as
# incomplete_usage_messages above - the whole historical corpus predates
# this key.


def test_build_record_omits_claude_code_version_when_the_transcript_never_carried_one(
    script, monkeypatch
):
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )

    record = script.build_record({"session_id": "s"}, _PARSED_STUB)

    assert "claude_code_version" not in record
    assert "claude_code_version_first" not in record


def test_build_record_carries_claude_code_version_when_known(script, monkeypatch):
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )
    parsed = dict(_PARSED_STUB, claude_code_version="2.1.284")

    record = script.build_record({"session_id": "s"}, parsed)

    assert record["claude_code_version"] == "2.1.284"
    assert "claude_code_version_first" not in record


def test_build_record_carries_claude_code_version_first_only_alongside_the_last(
    script, monkeypatch
):
    # claude_code_version_first can only appear together with
    # claude_code_version - parse_transcript already guarantees it is None
    # whenever claude_code_version is, so there is no first-without-last
    # case to defend against here; this pins the alongside case.
    monkeypatch.setattr(script, "get_tmux_window", lambda: None)
    monkeypatch.setattr(
        script, "get_internal_name", lambda session_id, sessions_directory=None: None
    )
    parsed = dict(_PARSED_STUB, claude_code_version="2.1.284", claude_code_version_first="2.1.260")

    record = script.build_record({"session_id": "s"}, parsed)

    assert record["claude_code_version"] == "2.1.284"
    assert record["claude_code_version_first"] == "2.1.260"
