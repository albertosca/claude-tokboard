"""AI-generated label caching/generation - direct coverage of
lib/metrics_core/labels.py, the only module that shells out to the real
`claude` CLI (via subprocess.run) and reads/writes the two label caches
under ~/.claude/metrics/.

Two isolation mechanisms apply to EVERY test in this file, enforced by
autouse fixtures below:

1. subprocess.run is monkeypatched to a hard-failing stub by default, so
   any code path that reaches the real CLI without a test-local override
   fails loudly instead of shelling out. Patching the `subprocess` module
   object imported here works because labels.py does `import subprocess`
   too - both names refer to the same singleton in sys.modules, so
   monkeypatch.setattr(subprocess, "run", ...) here is visible to
   labels.py's own `subprocess.run(...)` call.
2. TASK_LABELS_FILE/AGENT_LABELS_FILE - module-level constants resolved
   from HOME at import time - are monkeypatched directly to a throwaway
   tmp_path location. monkeypatch.setenv("HOME", ...) would NOT work here:
   the constants were already computed once at import time, so re-pointing
   HOME afterwards has no effect on them.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import metrics_core as mr
import metrics_core.labels as labels_module
import pytest


class FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture(autouse=True)
def isolated_label_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    task_file = tmp_path / "task_labels.json"
    agent_file = tmp_path / "agent_labels.json"
    monkeypatch.setattr(labels_module, "TASK_LABELS_FILE", str(task_file))
    monkeypatch.setattr(labels_module, "AGENT_LABELS_FILE", str(agent_file))
    return task_file, agent_file


@pytest.fixture(autouse=True)
def forbid_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("real subprocess.run must never be called in tests")

    monkeypatch.setattr(subprocess, "run", fail)


def write_transcript(path: Path, lines: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


def recording_fake_run(calls: list[list[str]]) -> Any:
    """A fake subprocess.run that records the command it was called with
    and returns a fixed successful result - used to assert a code path
    does NOT reach _run_label_prompt (calls stays empty)."""

    def fake_run(cmd: list[str], **kwargs: Any) -> FakeCompletedProcess:
        calls.append(cmd)
        return FakeCompletedProcess(0, "x")

    return fake_run


# --- isolation self-checks (Step 2) ---------------------------------------


def test_isolation_never_touches_real_label_cache_files() -> None:
    real_task_file = Path("~/.claude/metrics/task_labels.json").expanduser()
    real_agent_file = Path("~/.claude/metrics/agent_labels.json").expanduser()
    task_before = real_task_file.stat().st_mtime if real_task_file.exists() else None
    agent_before = real_agent_file.stat().st_mtime if real_agent_file.exists() else None

    mr.save_label_cache({"probe": {"pt": "value"}})
    mr.save_agent_label_cache({"probe": {"pt": "value"}})

    if task_before is None:
        assert not real_task_file.exists()
    else:
        assert real_task_file.stat().st_mtime == task_before
    if agent_before is None:
        assert not real_agent_file.exists()
    else:
        assert real_agent_file.stat().st_mtime == agent_before


def test_run_label_prompt_invokes_the_monkeypatched_fake_not_the_real_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    result = labels_module._run_label_prompt("some prompt")
    assert result == "x"
    assert len(calls) == 1


# --- find_away_summary -----------------------------------------------------


def test_find_away_summary_returns_the_last_summary_when_multiple_present(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "system", "subtype": "away_summary", "content": "first summary"},
            {"type": "system", "subtype": "away_summary", "content": "second summary"},
        ],
    )
    assert mr.find_away_summary(str(path)) == "second summary"


def test_find_away_summary_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert mr.find_away_summary(str(tmp_path / "nope.jsonl")) is None


def test_find_away_summary_returns_none_for_transcript_with_no_summary(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "user", "message": {"content": "hi"}}])
    assert mr.find_away_summary(str(path)) is None


def test_find_away_summary_skips_malformed_lines_but_still_finds_summary(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        "not json at all\n"
        '{"type": "system", "subtype": "away_summary", "content": "real summary"}\n'
        "\n"
    )
    assert mr.find_away_summary(str(path)) == "real summary"


def test_find_away_summary_returns_none_for_none_path() -> None:
    assert mr.find_away_summary(None) is None


def test_find_away_summary_returns_none_for_unreadable_path(tmp_path: Path) -> None:
    a_directory = tmp_path / "adir"
    a_directory.mkdir()
    assert mr.find_away_summary(str(a_directory)) is None


# --- cold_summary_source -----------------------------------------------------


def test_cold_summary_source_joins_the_first_turns_content(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "user", "message": {"content": "turn0"}},
            {"type": "assistant", "message": {"content": "turn1"}},
        ],
    )
    assert mr.cold_summary_source(str(path)) == "turn0 turn1"


def test_cold_summary_source_respects_max_turns_boundary(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [{"type": "user", "message": {"content": f"turn{i}"}} for i in range(4)],
    )
    result = mr.cold_summary_source(str(path), maximum_turns=3)
    assert result is not None
    assert "turn2" in result
    assert "turn3" not in result


def test_cold_summary_source_extracts_first_text_block_from_list_content(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "other"},
                        {"type": "text", "text": "first block"},
                        {"type": "text", "text": "second block"},
                    ]
                },
            }
        ],
    )
    assert mr.cold_summary_source(str(path)) == "first block"


def test_cold_summary_source_skips_entries_that_are_not_user_or_assistant(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "system", "message": {"content": "ignored"}}])
    assert mr.cold_summary_source(str(path)) is None


def test_cold_summary_source_skips_blank_lines_between_turns(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type": "user", "message": {"content": "turn0"}}\n'
        "\n"
        '{"type": "user", "message": {"content": "turn1"}}\n'
    )
    assert mr.cold_summary_source(str(path)) == "turn0 turn1"


def test_cold_summary_source_skips_a_malformed_json_line_between_turns(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type": "user", "message": {"content": "turn0"}}\n'
        "not json at all\n"
        '{"type": "user", "message": {"content": "turn1"}}\n'
    )
    assert mr.cold_summary_source(str(path)) == "turn0 turn1"


def test_cold_summary_source_ignores_an_empty_content_list(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "assistant", "message": {"content": []}},
            {"type": "user", "message": {"content": "turn1"}},
        ],
    )
    assert mr.cold_summary_source(str(path)) == "turn1"


def test_cold_summary_source_ignores_an_entry_whose_content_is_neither_str_nor_list(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "user", "message": {"content": 42}},
            {"type": "user", "message": {"content": "turn1"}},
        ],
    )
    assert mr.cold_summary_source(str(path)) == "turn1"


def test_cold_summary_source_ignores_a_content_list_with_no_text_block(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}},
            {"type": "user", "message": {"content": "turn1"}},
        ],
    )
    assert mr.cold_summary_source(str(path)) == "turn1"


def test_cold_summary_source_returns_none_for_missing_file(tmp_path: Path) -> None:
    assert mr.cold_summary_source(str(tmp_path / "nope.jsonl")) is None


def test_cold_summary_source_returns_none_for_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    assert mr.cold_summary_source(str(path)) is None


def test_cold_summary_source_returns_none_for_none_path() -> None:
    assert mr.cold_summary_source(None) is None


def test_cold_summary_source_returns_none_for_unreadable_path(tmp_path: Path) -> None:
    a_directory = tmp_path / "adir"
    a_directory.mkdir()
    assert mr.cold_summary_source(str(a_directory)) is None


def test_cold_summary_source_returns_none_when_all_turn_content_is_blank(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "user", "message": {"content": ""}}])
    assert mr.cold_summary_source(str(path)) is None


# --- _looks_like_bad_label ---------------------------------------------------


@pytest.mark.parametrize("marker", labels_module._REFUSAL_MARKERS)
def test_looks_like_bad_label_true_for_every_listed_refusal_marker(marker: str) -> None:
    assert labels_module._looks_like_bad_label(f"prefix {marker} suffix") is True


@pytest.mark.parametrize("marker", labels_module._REFUSAL_MARKERS)
def test_looks_like_bad_label_true_for_every_marker_capitalized(marker: str) -> None:
    # Real refusals are capitalized sentences ("I cannot complete this
    # request", "Não há texto para resumir") - the parametrized test above
    # feeds markers already lowercased, which can't tell whether the
    # `.lower()` case-fold in _looks_like_bad_label actually runs. Pin it
    # with the casing a real refusal would use.
    assert labels_module._looks_like_bad_label(f"Prefix {marker.capitalize()} suffix") is True


def test_looks_like_bad_label_true_for_a_verbatim_capitalized_real_refusal() -> None:
    assert labels_module._looks_like_bad_label("I cannot complete this request") is True
    assert labels_module._looks_like_bad_label("Não há texto para resumir") is True


def test_looks_like_bad_label_false_for_a_normal_short_label() -> None:
    assert labels_module._looks_like_bad_label("fix the login bug") is False


def test_looks_like_bad_label_false_at_exactly_120_chars() -> None:
    assert labels_module._looks_like_bad_label("a" * 120) is False


def test_looks_like_bad_label_true_at_121_chars() -> None:
    assert labels_module._looks_like_bad_label("a" * 121) is True


# --- _run_label_prompt --------------------------------------------------------


def test_run_label_prompt_returns_stripped_label_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeCompletedProcess(0, "  a label  "))
    assert labels_module._run_label_prompt("prompt") == "a label"


def test_run_label_prompt_strips_surrounding_double_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: FakeCompletedProcess(0, '"quoted label"'),
    )
    assert labels_module._run_label_prompt("prompt") == "quoted label"


def test_run_label_prompt_strips_surrounding_single_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: FakeCompletedProcess(0, "'quoted label'"),
    )
    assert labels_module._run_label_prompt("prompt") == "quoted label"


def test_run_label_prompt_returns_none_on_nonzero_returncode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeCompletedProcess(1, "a label"))
    assert labels_module._run_label_prompt("prompt") is None


def test_run_label_prompt_returns_none_on_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeCompletedProcess(0, ""))
    assert labels_module._run_label_prompt("prompt") is None


def test_run_label_prompt_returns_none_on_whitespace_only_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeCompletedProcess(0, "   \n  "))
    assert labels_module._run_label_prompt("prompt") is None


def test_run_label_prompt_returns_none_when_subprocess_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_it(cmd: list[str], **kw: Any) -> FakeCompletedProcess:
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(subprocess, "run", raise_it)
    assert labels_module._run_label_prompt("prompt") is None


def test_run_label_prompt_passes_the_cost_control_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kw: Any) -> FakeCompletedProcess:
        captured["cmd"] = cmd
        captured["kwargs"] = kw
        return FakeCompletedProcess(0, "a label")

    monkeypatch.setattr(subprocess, "run", fake_run)
    labels_module._run_label_prompt("prompt")
    cmd = captured["cmd"]
    # `nice -n 15` MUST prefix the actual command, not just appear
    # somewhere in argv - it's what stops the labeling subprocess from
    # competing for CPU with interactive work (labels.py:116-118).
    assert cmd[:3] == ["nice", "-n", "15"]
    assert "--setting-sources" in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--tools" in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--system-prompt" in cmd
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "haiku"
    # A hung CLI process without a timeout would block dashboard
    # generation forever - argv alone can't carry this, it's a
    # subprocess.run kwarg.
    assert captured["kwargs"]["timeout"] == 30


# --- compress_to_label --------------------------------------------------------


def test_compress_to_label_returns_label_for_good_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: FakeCompletedProcess(0, "good label"),
    )
    assert mr.compress_to_label("some source text", lang="pt") == "good label"


def test_compress_to_label_returns_none_when_both_attempts_return_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeCompletedProcess(1, ""))
    assert mr.compress_to_label("some source text", lang="pt") is None


def test_compress_to_label_retries_and_recovers_from_a_bad_first_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        FakeCompletedProcess(0, "i cannot do that"),
        FakeCompletedProcess(0, "recovered label"),
    ]

    def fake_run(cmd: list[str], **kw: Any) -> FakeCompletedProcess:
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mr.compress_to_label("some source text", lang="pt") == "recovered label"


def test_compress_to_label_returns_none_when_both_attempts_look_bad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        FakeCompletedProcess(0, "i cannot do that"),
        FakeCompletedProcess(0, "i'm sorry still cannot"),
    ]

    def fake_run(cmd: list[str], **kw: Any) -> FakeCompletedProcess:
        return responses.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert mr.compress_to_label("some source text", lang="pt") is None


def test_compress_to_label_returns_none_for_empty_source() -> None:
    assert mr.compress_to_label("", lang="pt") is None


def test_compress_to_label_returns_none_for_none_source() -> None:
    assert mr.compress_to_label(None, lang="pt") is None


def test_compress_to_label_returns_none_for_non_string_source() -> None:
    assert mr.compress_to_label(123, lang="pt") is None


def test_compress_to_label_bounds_the_prompt_length_for_a_very_long_source_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unbounded prompt is a direct cost multiplier (every extra input
    # token is billed) - source_text[:2000] caps it regardless of how
    # long the real source (an away summary or joined transcript turns)
    # actually is.
    captured: dict[str, str] = {}

    def fake_run(cmd: list[str], **kw: Any) -> FakeCompletedProcess:
        captured["prompt"] = cmd[cmd.index("-p") + 1]
        return FakeCompletedProcess(0, "a label")

    monkeypatch.setattr(subprocess, "run", fake_run)
    very_long_source = "x" * 10_000
    mr.compress_to_label(very_long_source, lang="pt")
    # The prompt is the fixed instruction text plus the (possibly
    # truncated) source - assert the source contribution is bounded at
    # 2000 chars, not the full 10,000-char input.
    assert "x" * 2000 in captured["prompt"]
    assert "x" * 2001 not in captured["prompt"]


def test_compress_to_label_truncates_a_long_but_not_bad_label_with_ellipsis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mid_label = ("lorem " * 15).strip()
    assert 60 < len(mid_label) <= 120
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeCompletedProcess(0, mid_label))
    result = mr.compress_to_label("some source text", lang="pt")
    assert result is not None
    assert result.endswith("…")
    assert len(result) == 60


# --- cache load/save: task labels --------------------------------------------


def test_load_label_cache_missing_file_returns_empty_dict() -> None:
    assert mr.load_label_cache() == {}


def test_save_then_load_label_cache_round_trips() -> None:
    mr.save_label_cache({"session-1": {"pt": "a nice label"}})
    assert mr.load_label_cache() == {"session-1": {"pt": "a nice label"}}


def test_load_label_cache_corrupt_file_returns_empty_dict() -> None:
    Path(labels_module.TASK_LABELS_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(labels_module.TASK_LABELS_FILE).write_text("not valid json{")
    assert mr.load_label_cache() == {}


# --- cache load/save: agent labels -------------------------------------------


def test_load_agent_label_cache_missing_file_returns_empty_dict() -> None:
    assert mr.load_agent_label_cache() == {}


def test_save_then_load_agent_label_cache_round_trips() -> None:
    mr.save_agent_label_cache({"agent-1": {"pt": "a nice label"}})
    assert mr.load_agent_label_cache() == {"agent-1": {"pt": "a nice label"}}


def test_load_agent_label_cache_corrupt_file_returns_empty_dict() -> None:
    Path(labels_module.AGENT_LABELS_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(labels_module.AGENT_LABELS_FILE).write_text("not valid json{")
    assert mr.load_agent_label_cache() == {}


# --- LabelBudget --------------------------------------------------------------


def test_label_budget_none_limit_allows_unboundedly() -> None:
    budget = mr.LabelBudget(None)
    assert [budget.try_consume() for _ in range(5)] == [True] * 5
    assert budget.remaining is None


def test_label_budget_finite_limit_allows_exactly_n_then_refuses() -> None:
    budget = mr.LabelBudget(2)
    assert budget.try_consume() is True
    assert budget.remaining == 1
    assert budget.try_consume() is True
    assert budget.remaining == 0
    assert budget.try_consume() is False
    assert budget.remaining == 0


def test_label_budget_zero_limit_refuses_immediately() -> None:
    budget = mr.LabelBudget(0)
    assert budget.try_consume() is False
    assert budget.remaining == 0


# --- get_task_label ------------------------------------------------------------


def test_get_task_label_returns_none_without_session_id() -> None:
    assert mr.get_task_label({}, {}, mr.LabelBudget(None), lang="pt") is None


def test_get_task_label_cache_hit_returns_cached_value_without_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache = {"s1": {"pt": "cached label"}}
    result = mr.get_task_label({"session_id": "s1"}, cache, mr.LabelBudget(None), lang="pt")
    assert result == "cached label"
    assert calls == []


def test_get_task_label_cache_hit_no_label_marker_returns_none_without_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache = {"s1": {"pt": "__no_label__"}}
    result = mr.get_task_label({"session_id": "s1"}, cache, mr.LabelBudget(None), lang="pt")
    assert result is None
    assert calls == []


def test_get_task_label_short_internal_name_used_directly_without_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache: mr.LabelCache = {}
    record = {"session_id": "s1", "internal_name": "short name here"}
    result = mr.get_task_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result == "short name here"
    assert cache == {"s1": {"pt": "short name here"}}
    assert calls == []


def test_get_task_label_six_word_internal_name_used_directly_without_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pins the exact boundary: real sessions.jsonl has 261 six-word
    # internal_names (out of {1: 293, 4: 159, 5: 195, 6: 261, 7: 8}) -
    # `<= 6` keeps them all free/verbatim; flipping to `< 6` would push
    # every one of them into a paid `claude -p --model haiku` call. The
    # existing tests above/below use 3-word and 7-word names, which
    # straddle nothing.
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache: mr.LabelCache = {}
    six_word_name = "one two three four five six"
    record = {"session_id": "s1", "internal_name": six_word_name}
    result = mr.get_task_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result == six_word_name
    assert cache == {"s1": {"pt": six_word_name}}
    assert calls == []


def test_get_task_label_long_internal_name_is_used_as_source_text_for_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Surprising: an internal_name over 6 words is NOT discarded in favor
    # of the transcript - it becomes the source text fed to
    # compress_to_label directly (source = internal_name or ...), so the
    # transcript at a nonexistent path is never even read.
    captured: dict[str, str] = {}

    def fake_run(cmd: list[str], **kw: Any) -> FakeCompletedProcess:
        captured["prompt"] = cmd[cmd.index("-p") + 1]
        return FakeCompletedProcess(0, "generated label")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cache: mr.LabelCache = {}
    long_name = "one two three four five six seven"
    record = {
        "session_id": "s1",
        "internal_name": long_name,
        "transcript_path": "/nonexistent/does-not-exist.jsonl",
    }
    result = mr.get_task_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result == "generated label"
    assert long_name in captured["prompt"]
    assert cache == {"s1": {"pt": "generated label"}}


def test_get_task_label_falls_back_to_away_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path, [{"type": "system", "subtype": "away_summary", "content": "an away summary"}]
    )
    captured: dict[str, str] = {}

    def fake_run(cmd: list[str], **kw: Any) -> FakeCompletedProcess:
        captured["prompt"] = cmd[cmd.index("-p") + 1]
        return FakeCompletedProcess(0, "generated label")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cache: mr.LabelCache = {}
    record = {"session_id": "s1", "transcript_path": str(path)}
    result = mr.get_task_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result == "generated label"
    assert "an away summary" in captured["prompt"]


def test_get_task_label_falls_back_to_cold_summary_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "user", "message": {"content": "first turn text"}}])
    captured: dict[str, str] = {}

    def fake_run(cmd: list[str], **kw: Any) -> FakeCompletedProcess:
        captured["prompt"] = cmd[cmd.index("-p") + 1]
        return FakeCompletedProcess(0, "generated label")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cache: mr.LabelCache = {}
    record = {"session_id": "s1", "transcript_path": str(path)}
    result = mr.get_task_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result == "generated label"
    assert "first turn text" in captured["prompt"]


def test_get_task_label_with_no_source_caches_no_label_marker_without_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache: mr.LabelCache = {}
    result = mr.get_task_label({"session_id": "s1"}, cache, mr.LabelBudget(None), lang="pt")
    assert result is None
    assert cache == {"s1": {"pt": "__no_label__"}}
    assert calls == []


def test_get_task_label_budget_exhausted_leaves_session_uncached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path, [{"type": "system", "subtype": "away_summary", "content": "an away summary"}]
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache: mr.LabelCache = {}
    record = {"session_id": "s1", "transcript_path": str(path)}
    result = mr.get_task_label(record, cache, mr.LabelBudget(0), lang="pt")
    assert result is None
    assert cache == {}
    assert calls == []


def test_get_task_label_generates_and_caches_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path, [{"type": "system", "subtype": "away_summary", "content": "an away summary"}]
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: FakeCompletedProcess(0, "generated label"),
    )
    cache: mr.LabelCache = {}
    record = {"session_id": "s1", "transcript_path": str(path)}
    result = mr.get_task_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result == "generated label"
    assert cache == {"s1": {"pt": "generated label"}}


def test_get_task_label_caches_no_label_marker_when_generation_runs_but_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Distinct from the no-source and cache-hit no-label tests above: here
    # generation actually RAN (budget consumed, subprocess called - twice,
    # for the retry) and compress_to_label came back None because both
    # attempts looked like refusals. The negative cache still has to be
    # written here so this permanently-unlabelable session is attempted
    # once, not on every future render - `cache.setdefault(session_id, {})
    # [lang] = label or ""` would satisfy every other test in this file
    # while writing a falsy empty string that reads as "not yet tried"
    # instead of the sentinel.
    path = tmp_path / "t.jsonl"
    write_transcript(
        path, [{"type": "system", "subtype": "away_summary", "content": "an away summary"}]
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: FakeCompletedProcess(0, "i cannot do that"),
    )
    cache: mr.LabelCache = {}
    record = {"session_id": "s1", "transcript_path": str(path)}
    result = mr.get_task_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result is None
    assert cache == {"s1": {"pt": "__no_label__"}}


# --- get_agent_label ------------------------------------------------------------


def test_get_agent_label_returns_none_without_agent_id() -> None:
    assert mr.get_agent_label({}, {}, mr.LabelBudget(None), lang="pt") is None


def test_get_agent_label_cache_hit_returns_cached_value_without_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache = {"a1": {"pt": "cached label"}}
    result = mr.get_agent_label({"agent_id": "a1"}, cache, mr.LabelBudget(None), lang="pt")
    assert result == "cached label"
    assert calls == []


def test_get_agent_label_cache_hit_no_label_marker_returns_none_without_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache = {"a1": {"pt": "__no_label__"}}
    result = mr.get_agent_label({"agent_id": "a1"}, cache, mr.LabelBudget(None), lang="pt")
    assert result is None
    assert calls == []


def test_get_agent_label_with_no_source_caches_no_label_marker_without_generating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache: mr.LabelCache = {}
    result = mr.get_agent_label({"agent_id": "a1"}, cache, mr.LabelBudget(None), lang="pt")
    assert result is None
    assert cache == {"a1": {"pt": "__no_label__"}}
    assert calls == []


def test_get_agent_label_budget_exhausted_leaves_agent_uncached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "user", "message": {"content": "some turn text"}}])
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", recording_fake_run(calls))
    cache: mr.LabelCache = {}
    record = {"agent_id": "a1", "agent_transcript_path": str(path)}
    result = mr.get_agent_label(record, cache, mr.LabelBudget(0), lang="pt")
    assert result is None
    assert cache == {}
    assert calls == []


def test_warm_summary_source_reads_the_last_turns_in_chronological_order(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "user", "message": {"content": "Review one task. Do NOT cd."}},
            {"type": "assistant", "message": {"content": "Caught. Now mutation 4."}},
            {"type": "assistant", "message": {"content": "Caught. Now mutation 5."}},
            {
                "type": "assistant",
                "message": {"content": "Spec Compliance: Compliant. All mutations caught."},
            },
        ],
    )
    result = mr.warm_summary_source(str(path), maximum_turns=2)
    assert result == "Caught. Now mutation 5. Spec Compliance: Compliant. All mutations caught."


def test_warm_summary_source_returns_none_for_a_missing_path() -> None:
    assert mr.warm_summary_source(None) is None
    assert mr.warm_summary_source("/does/not/exist.jsonl") is None


def test_warm_summary_source_returns_none_when_no_assistant_text_exists(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "user", "message": {"content": "Review one task."}}])
    assert mr.warm_summary_source(str(path)) is None


def test_warm_summary_source_skips_blank_lines_between_turns(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type": "assistant", "message": {"content": "turn0"}}\n'
        "\n"
        '{"type": "assistant", "message": {"content": "turn1"}}\n'
    )
    assert mr.warm_summary_source(str(path)) == "turn0 turn1"


def test_warm_summary_source_skips_a_malformed_json_line_between_turns(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type": "assistant", "message": {"content": "turn0"}}\n'
        "not json at all\n"
        '{"type": "assistant", "message": {"content": "turn1"}}\n'
    )
    assert mr.warm_summary_source(str(path)) == "turn0 turn1"


def test_warm_summary_source_ignores_an_entry_whose_content_is_neither_str_nor_list(
    tmp_path: Path,
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "assistant", "message": {"content": "turn0"}},
            {"type": "assistant", "message": {"content": 42}},
        ],
    )
    assert mr.warm_summary_source(str(path)) == "turn0"


def test_warm_summary_source_returns_none_for_an_unreadable_path(tmp_path: Path) -> None:
    a_directory = tmp_path / "adir"
    a_directory.mkdir()
    assert mr.warm_summary_source(str(a_directory)) is None


def test_warm_summary_source_extracts_text_blocks_from_list_content(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Done reviewing."}]},
            },
        ],
    )
    assert mr.warm_summary_source(str(path)) == "Done reviewing."


def test_get_agent_label_prefers_the_warm_source_over_the_cold_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(
        path,
        [
            {"type": "user", "message": {"content": "Review one task. Do NOT cd."}},
            {"type": "assistant", "message": {"content": "Spec Compliance: Compliant."}},
        ],
    )
    seen_sources: list[str] = []

    def fake_compress(source: str, *, lang: str) -> str:
        seen_sources.append(source)
        return "a label"

    monkeypatch.setattr(labels_module, "compress_to_label", fake_compress)
    record = {"agent_id": "a1", "agent_transcript_path": str(path)}
    label = mr.get_agent_label(record, {}, mr.LabelBudget(None), lang="pt")
    assert label == "a label"
    assert seen_sources == ["Spec Compliance: Compliant."]


def test_get_agent_label_falls_back_to_cold_source_when_no_assistant_text_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "user", "message": {"content": "Review one task."}}])
    seen_sources: list[str] = []

    def fake_compress(source: str, *, lang: str) -> str:
        seen_sources.append(source)
        return "a label"

    monkeypatch.setattr(labels_module, "compress_to_label", fake_compress)
    record = {"agent_id": "a1", "agent_transcript_path": str(path)}
    label = mr.get_agent_label(record, {}, mr.LabelBudget(None), lang="pt")
    assert label == "a label"
    assert seen_sources == ["Review one task."]


def test_get_agent_label_generates_and_caches_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "user", "message": {"content": "some turn text"}}])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: FakeCompletedProcess(0, "generated label"),
    )
    cache: mr.LabelCache = {}
    record = {"agent_id": "a1", "agent_transcript_path": str(path)}
    result = mr.get_agent_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result == "generated label"
    assert cache == {"a1": {"pt": "generated label"}}


def test_get_agent_label_caches_no_label_marker_when_generation_runs_but_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Same expensive-arm gap as get_task_label above: generation actually
    # ran and refused both attempts, so compress_to_label returned None -
    # the negative cache write on THIS arm (as opposed to the no-source
    # arm, already pinned above) is what stops the dashboard re-paying for
    # a subprocess call on every render for a permanently unlabelable
    # agent.
    path = tmp_path / "t.jsonl"
    write_transcript(path, [{"type": "user", "message": {"content": "some turn text"}}])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: FakeCompletedProcess(0, "i cannot do that"),
    )
    cache: mr.LabelCache = {}
    record = {"agent_id": "a1", "agent_transcript_path": str(path)}
    result = mr.get_agent_label(record, cache, mr.LabelBudget(None), lang="pt")
    assert result is None
    assert cache == {"a1": {"pt": "__no_label__"}}


# --- group_subagents_by_session ---------------------------------------------


def test_group_subagents_by_session_groups_by_parent_session_id() -> None:
    records = [
        {"session_id": "s1", "agent_id": "x"},
        {"session_id": "s1", "agent_id": "y"},
        {"session_id": "s2", "agent_id": "z"},
    ]
    result = mr.group_subagents_by_session(records)
    assert result == {
        "s1": [{"session_id": "s1", "agent_id": "x"}, {"session_id": "s1", "agent_id": "y"}],
        "s2": [{"session_id": "s2", "agent_id": "z"}],
    }


def test_group_subagents_by_session_skips_records_without_a_session_id() -> None:
    records = [{"agent_id": "orphan"}]
    assert mr.group_subagents_by_session(records) == {}
