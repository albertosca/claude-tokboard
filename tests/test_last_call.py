import importlib.util
import json
import runpy
import types
from pathlib import Path
from typing import Any

import metrics_core.last_call as last_call
import pytest
from metrics_core.transcript_ingest import TOKEN_FIELDS, parse_transcript

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill-subagent-last-call.py"
MODEL = "model-x"


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("backfill_subagent_last_call", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assistant(
    message_id: str,
    output: int,
    *,
    input_tokens: int = 10,
    read: int = 50,
    creation: int = 5,
    model: str = MODEL,
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": "2026-10-20T10:00:00Z",
        "message": {
            "id": message_id,
            "model": model,
            "stop_reason": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": creation,
            },
        },
    }


def _write_lines(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))


def _parent_result(
    agent_id: str,
    *,
    output: int = 500,
    input_tokens: int = 20,
    read: int = 60,
    creation: int = 7,
    iterations: int = 1,
) -> dict[str, Any]:
    total = input_tokens + output + read + creation
    return {
        "toolUseResult": {
            "agentId": agent_id,
            "totalTokens": total,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": creation,
                "iterations": [{"type": "message"}] * iterations,
            },
        }
    }


def _build(
    tmp_path: Path,
    *,
    parent_entries: list[dict[str, Any]] | None = None,
    last_output: int = 3,
    with_map: bool = True,
) -> tuple[dict[str, Any], Path, Path]:
    """A subagent whose last message is stuck at `last_output`, its parent
    session, and the row the hook stored for it."""
    session_directory = tmp_path / "projects" / "slug" / "session-1"
    transcript = session_directory / "subagents" / "agent-a1.jsonl"
    _write_lines(
        transcript,
        [
            _assistant("m1", 100),
            _assistant("m2", last_output, input_tokens=20, read=60, creation=7),
        ],
    )
    parent = session_directory.parent / "session-1.jsonl"
    _write_lines(parent, [_parent_result("a1")] if parent_entries is None else parent_entries)
    parsed = parse_transcript(str(transcript))
    record = {
        "agent_id": "a1",
        "timestamp_end": "2026-10-20T10:00:00Z",
        "agent_transcript_path": str(transcript),
        **{field: parsed[field] for field in TOKEN_FIELDS},
    }
    if with_map:
        record["tokens_by_model"] = {MODEL: {field: parsed[field] for field in TOKEN_FIELDS}}
    return record, transcript, parent


def test_the_parent_session_sits_beside_the_agents_session_directory(tmp_path):
    transcript = tmp_path / "slug" / "session-1" / "subagents" / "agent-a1.jsonl"

    assert last_call.parent_session_path(str(transcript)) == tmp_path / "slug" / "session-1.jsonl"


def test_a_parent_result_with_one_call_gives_its_four_fields(tmp_path):
    parent = tmp_path / "parent.jsonl"
    _write_lines(parent, [_parent_result("a1")])

    assert last_call.parent_last_calls(parent) == {"a1": (20, 500, 60, 7)}


def test_a_parent_result_that_is_not_provably_one_call_is_marked_so(tmp_path):
    parent = tmp_path / "parent.jsonl"
    cumulative = _parent_result("a2")
    cumulative["toolUseResult"]["totalTokens"] += 1
    _write_lines(parent, [_parent_result("a1", iterations=2), cumulative])

    assert last_call.parent_last_calls(parent) == {"a1": None, "a2": None}


def test_lines_that_are_not_agent_results_are_ignored(tmp_path):
    parent = tmp_path / "parent.jsonl"
    parent.write_text(
        '{"agentId": "a1", "totalTokens": 1} not json\n'
        + json.dumps({"toolUseResult": "text", "agentId": 1, "totalTokens": 1})
        + "\n"
        + json.dumps({"toolUseResult": {"agentId": "a3", "totalTokens": 1, "usage": 5}})
        + "\n"
        + json.dumps({"toolUseResult": {"agentId": 7, "totalTokens": 1, "usage": {}}})
        + "\n"
        + "an unrelated line\n"
    )

    assert last_call.parent_last_calls(parent) == {}


def test_a_missing_parent_session_holds_no_results(tmp_path):
    assert last_call.parent_last_calls(tmp_path / "gone.jsonl") == {}


def test_the_last_message_is_the_one_first_seen_last_at_its_per_field_peak(tmp_path):
    transcript = tmp_path / "agent.jsonl"
    _write_lines(
        transcript,
        [
            _assistant("m1", 100),
            _assistant("m2", 2, model="other"),
            _assistant("m2", 4, read=1, model="other"),
            {"type": "user"},
            {"type": "assistant", "message": {"usage": {}}},
        ],
    )
    transcript.write_text(transcript.read_text() + "broken\n")

    assert last_call.last_message(str(transcript)) == ("other", (10, 4, 50, 5))


def test_a_transcript_without_messages_has_no_last_message(tmp_path):
    transcript = tmp_path / "agent.jsonl"
    _write_lines(transcript, [{"type": "user"}])

    assert last_call.last_message(str(transcript)) is None


def test_a_stuck_last_call_is_raised_to_the_parents_figure_and_its_model_bucket(tmp_path):
    record, _, _ = _build(tmp_path)

    updated, reason = last_call.plan_last_call(record, {})

    assert reason is None
    assert updated is not None
    assert updated["output_tokens"] == record["output_tokens"] + 497
    assert updated["tokens_by_model"][MODEL]["output_tokens"] == updated["output_tokens"]
    assert record["tokens_by_model"][MODEL]["output_tokens"] == record["output_tokens"]


def test_a_row_without_a_model_map_is_repaired_without_gaining_one(tmp_path):
    record, _, _ = _build(tmp_path, with_map=False)

    updated, _ = last_call.plan_last_call(record, {})

    assert updated is not None
    assert updated["output_tokens"] == record["output_tokens"] + 497
    assert "tokens_by_model" not in updated


def test_a_repaired_row_is_recognised_as_already_recovered(tmp_path):
    record, _, _ = _build(tmp_path)
    updated, _ = last_call.plan_last_call(record, {})
    assert updated is not None

    assert last_call.plan_last_call(updated, {}) == (None, last_call.ALREADY_RECOVERED)


def test_a_row_that_is_not_the_final_snapshot_is_left_alone(tmp_path):
    record, _, _ = _build(tmp_path)
    record["output_tokens"] -= 1

    assert last_call.plan_last_call(record, {}) == (None, last_call.NOT_FINAL_ROW)


def test_a_model_map_without_the_last_calls_model_is_left_alone(tmp_path):
    record, _, _ = _build(tmp_path)
    record["tokens_by_model"] = {"someone-else": record["tokens_by_model"][MODEL]}

    assert last_call.plan_last_call(record, {}) == (None, last_call.MAP_MISMATCH)


def test_a_last_call_that_is_already_complete_has_nothing_to_gain(tmp_path):
    record, _, _ = _build(tmp_path, last_output=500)

    assert last_call.plan_last_call(record, {}) == (None, last_call.NOTHING_TO_GAIN)


@pytest.mark.parametrize(
    "result",
    [
        _parent_result("a1", input_tokens=21),
        _parent_result("a1", read=1),
        _parent_result("a1", creation=8),
        _parent_result("a1", output=2),
    ],
)
def test_a_parent_result_that_is_not_the_transcripts_last_call_is_left_alone(tmp_path, result):
    record, _, _ = _build(tmp_path, parent_entries=[result])

    assert last_call.plan_last_call(record, {}) == (None, last_call.DIFFERENT_CALL)


def test_a_cumulative_parent_result_is_left_alone(tmp_path):
    record, _, _ = _build(tmp_path, parent_entries=[_parent_result("a1", iterations=2)])

    assert last_call.plan_last_call(record, {}) == (None, last_call.NOT_ONE_CALL)


def test_an_agent_the_parent_never_recorded_is_left_alone(tmp_path):
    record, _, _ = _build(tmp_path, parent_entries=[_parent_result("someone-else")])

    assert last_call.plan_last_call(record, {}) == (None, last_call.NO_PARENT_RESULT)


def test_a_missing_or_unrecorded_transcript_is_left_alone(tmp_path):
    record, transcript, _ = _build(tmp_path)

    assert last_call.plan_last_call({**record, "agent_transcript_path": None}, {}) == (
        None,
        last_call.NO_TRANSCRIPT,
    )
    transcript.write_text('{"type": "user"}\n')
    assert last_call.plan_last_call(record, {}) == (None, last_call.NO_TRANSCRIPT)
    transcript.unlink()
    assert last_call.plan_last_call(record, {}) == (None, last_call.NO_TRANSCRIPT)


def test_a_dangling_transcript_link_is_a_missing_transcript(tmp_path):
    record, transcript, _ = _build(tmp_path)
    transcript.unlink()
    transcript.symlink_to(tmp_path / "moved-away.jsonl")

    assert last_call.plan_last_call(record, {}) == (None, last_call.NO_TRANSCRIPT)


def test_each_parent_session_is_read_once_for_all_its_agents(tmp_path, monkeypatch):
    record, _, _ = _build(tmp_path)
    reads: list[Path] = []
    real = last_call.parent_last_calls

    def counting(path: Path) -> dict[str, tuple[int, ...] | None]:
        reads.append(path)
        return real(path)

    monkeypatch.setattr(last_call, "parent_last_calls", counting)
    parents: dict[str, Any] = {}

    last_call.plan_last_call(record, parents)
    last_call.plan_last_call(record, parents)

    assert len(reads) == 1


def _store(path: Path, records: list[dict[str, Any]]) -> None:
    _write_lines(path, records)


def test_the_plan_repairs_only_the_newest_row_of_each_agent(tmp_path, script):
    record, _, _ = _build(tmp_path)
    older = {**record, "timestamp_end": "2026-10-19T10:00:00Z", "output_tokens": 1}
    unnamed = {"agent_id": None, "timestamp_end": "x"}

    repaired, skipped, gained = script.plan([older, record, unnamed])

    assert sorted(repaired) == [1]
    assert gained == 497
    assert skipped == {}


def test_a_later_row_with_an_equal_stamp_stands_for_the_agent(tmp_path, script):
    record, _, _ = _build(tmp_path)

    repaired, _, _ = script.plan([{**record, "output_tokens": 1}, record])

    assert sorted(repaired) == [1]


def test_the_plan_counts_why_each_row_was_left_alone(tmp_path, script):
    record, _, _ = _build(tmp_path, parent_entries=[])

    repaired, skipped, gained = script.plan([record])

    assert (repaired, gained) == ({}, 0)
    assert skipped == {last_call.NO_PARENT_RESULT: 1}


def test_the_check_names_anything_but_the_output_that_moved(script):
    before = [{"agent_id": "a1", "output_tokens": 5, "input_tokens": 1}]

    assert script.problems(before, [{**before[0], "input_tokens": 2}]) == [
        "line 1: input_tokens changed"
    ]
    assert script.problems(before, [{**before[0], "output_tokens": 4}]) == [
        "line 1: output_tokens fell"
    ]
    broken = {**before[0], "output_tokens": 6, "tokens_by_model": {MODEL: {"output_tokens": 5}}}
    assert script.problems(before, [broken]) == ["line 1: the model map no longer sums to the row"]
    assert script.problems(before, [{**before[0], "output_tokens": 6}]) == []


def _run(
    script: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    path: Path,
    *extra: str,
) -> tuple[int, Any]:
    monkeypatch.setattr("sys.argv", ["script", "--file", str(path), *extra])
    code = script.main()
    return code, capsys.readouterr()


def test_a_dry_run_reports_and_writes_nothing(tmp_path, script, monkeypatch, capsys):
    record, _, _ = _build(tmp_path)
    store = tmp_path / "subagents.jsonl"
    _store(store, [record])
    before = store.read_bytes()

    code, output = _run(script, monkeypatch, capsys, store)

    assert code == 0
    assert "would be recovered: 1 (+497 output tokens)" in output.out
    assert "DRY RUN - nothing written" in output.out
    assert store.read_bytes() == before


def test_an_apply_backs_up_then_writes_only_the_repaired_row(tmp_path, script, monkeypatch, capsys):
    record, _, _ = _build(tmp_path)
    store = tmp_path / "subagents.jsonl"
    _store(store, [record])
    before = store.read_bytes()

    code, output = _run(script, monkeypatch, capsys, store, "--apply")

    assert code == 0
    assert "backup verified" in output.out
    [written] = [json.loads(line) for line in store.read_text().splitlines()]
    assert written["output_tokens"] == record["output_tokens"] + 497
    [backup] = list((tmp_path / "backups").iterdir())
    assert backup.read_bytes() == before
    again_code, again = _run(script, monkeypatch, capsys, store, "--apply")
    assert again_code == 0
    assert "nothing to write" in again.out


def test_a_missing_metrics_file_is_an_error(tmp_path, script, monkeypatch, capsys):
    code, output = _run(script, monkeypatch, capsys, tmp_path / "gone.jsonl")

    assert code == 1
    assert "no metrics file" in output.err


def test_a_failed_check_writes_nothing(tmp_path, script, monkeypatch, capsys):
    record, _, _ = _build(tmp_path)
    store = tmp_path / "subagents.jsonl"
    _store(store, [record])
    monkeypatch.setattr(
        script, "plan", lambda _records: ({0: {**record, "input_tokens": 1}}, {}, 1)
    )
    before = store.read_bytes()

    code, output = _run(script, monkeypatch, capsys, store, "--apply")

    assert code == 1
    assert "VERIFICATION FAILED" in output.err
    assert store.read_bytes() == before


def test_an_older_row_listed_after_the_newest_does_not_take_its_place(tmp_path, script):
    record, _, _ = _build(tmp_path)
    older = {**record, "timestamp_end": "2026-10-19T10:00:00Z", "output_tokens": 1}

    repaired, _, _ = script.plan([record, older])

    assert sorted(repaired) == [0]


def test_run_as_a_program_it_exits_with_the_codes_status(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["script", "--file", str(tmp_path / "gone.jsonl")])

    with pytest.raises(SystemExit) as raised:
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert raised.value.code == 1
