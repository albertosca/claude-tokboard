"""Both hooks append per-call rows after their own record, and never fail the session."""

import importlib.util
import io
import json
import types
from pathlib import Path
from typing import NamedTuple

import pytest
from calls_fixtures import assistant_entry, write_transcript
from metrics_core import calls_store

HOOKS = Path(__file__).resolve().parent.parent / "hooks"


class HookSetup(NamedTuple):
    module: types.ModuleType
    metrics: Path
    store: str


def _load(name: str, file_name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, HOOKS / file_name)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["session", "subagent"])
def hook(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> HookSetup:
    if request.param == "session":
        module = _load("session_hook_calls", "metrics-log.py")
        path_key, store = "transcript_path", "sessions.jsonl"
    else:
        module = _load("subagent_hook_calls", "subagent-metrics-log.py")
        path_key, store = "agent_transcript_path", "subagents.jsonl"
    metrics = tmp_path / "metrics"
    monkeypatch.setattr(module, "METRICS_DIRECTORY", metrics)
    monkeypatch.setattr(module, "METRICS_FILE", metrics / store)
    transcript = tmp_path / "conversation-1.jsonl"
    write_transcript(transcript, [assistant_entry("m1"), assistant_entry("m2")])
    payload = json.dumps({"session_id": "session-1", path_key: str(transcript)})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    return HookSetup(module, metrics, store)


def test_the_hook_logs_the_transcripts_calls_next_to_its_own_record(hook: HookSetup) -> None:
    hook.module.main()

    assert (hook.metrics / hook.store).exists()
    rows = calls_store.read_calls(hook.metrics / "calls.jsonl")
    assert [row["message_id"] for row in rows] == ["m1", "m2"]


def test_a_failing_call_log_costs_the_session_neither_its_record_nor_its_exit(
    hook: HookSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_arguments: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(hook.module, "log_transcript", explode)

    hook.module.main()

    assert (hook.metrics / hook.store).exists()
    assert not (hook.metrics / "calls.jsonl").exists()
