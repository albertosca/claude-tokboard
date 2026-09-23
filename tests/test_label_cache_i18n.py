"""The label cache follows the interface language.

Both caches (task_labels.json, agent_labels.json) moved from {id: label} to
{id: {lang: label}}. These tests cover the migration of an old-format file
(built BY HAND below, never by today's code - a fixture produced by the code
under test could not tell an old file from a new one), the one-time
`.pre-i18n` backup, the atomic write, and the two-language prompt.

Two isolation mechanisms, mirroring tests/test_labels.py:

1. TASK_LABELS_FILE/AGENT_LABELS_FILE - resolved from HOME at import time -
   are monkeypatched directly to a throwaway tmp_path location.
2. subprocess.run is monkeypatched to a hard-failing stub by default, so any
   code path that reaches the real CLI without a test-local override
   (the `spy` fixture patches _run_label_prompt instead, one layer up)
   fails loudly instead of shelling out.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import metrics_core.labels as labels
import pytest

# An old-format cache holding all three kinds of value at once: a session
# named by Alberto, the negative-cache marker, and a label the model wrote -
# migration must lose none of them.
OLD = {"s-named": "fix auth redirect", "s-empty": "__no_label__", "s-model": "corrigindo o login"}
AGENT_OLD = {
    "a-named": "revisao de pr",
    "a-empty": "__no_label__",
    "a-model": "corrigindo o teste",
}


@dataclass
class LabelFiles:
    task: Path
    agent: Path


@dataclass
class Spy:
    prompts: list[str] = field(default_factory=list)

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "a label"


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LabelFiles:
    task_file = tmp_path / "task_labels.json"
    agent_file = tmp_path / "agent_labels.json"
    monkeypatch.setattr(labels, "TASK_LABELS_FILE", str(task_file))
    monkeypatch.setattr(labels, "AGENT_LABELS_FILE", str(agent_file))
    return LabelFiles(task=task_file, agent=agent_file)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Spy:
    fake = Spy()
    monkeypatch.setattr(labels, "_run_label_prompt", fake)
    return fake


@pytest.fixture(autouse=True)
def forbid_real_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("real subprocess.run must never be called in tests")

    monkeypatch.setattr(subprocess, "run", fail)


# --- migration: task labels ---------------------------------------------


def test_an_old_cache_migrates_as_portuguese_with_nothing_lost(files: LabelFiles) -> None:
    files.task.write_text(json.dumps(OLD))
    assert labels.load_label_cache() == {k: {"pt": v} for k, v in OLD.items()}


def test_the_first_save_keeps_a_one_time_backup_of_the_old_file(files: LabelFiles) -> None:
    files.task.write_text(json.dumps(OLD))
    cache = labels.load_label_cache()
    labels.save_label_cache(cache)
    backup = files.task.with_name(files.task.name + ".pre-i18n")
    assert json.loads(backup.read_text()) == OLD
    cache["s-new"] = {"en": "x"}
    labels.save_label_cache(cache)
    assert json.loads(backup.read_text()) == OLD, "the backup must never be overwritten"


def test_a_failed_write_leaves_the_old_file_whole(
    files: LabelFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    files.task.write_text(json.dumps(OLD))

    def raise_disk_full(*args: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(labels.os, "replace", raise_disk_full)
    with pytest.raises(OSError, match="disk full"):
        labels.save_label_cache({"s": {"pt": "y"}})
    assert json.loads(files.task.read_text()) == OLD


def test_a_failed_write_does_not_leave_a_stray_tmp_file(
    files: LabelFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    files.task.write_text(json.dumps(OLD))

    def raise_disk_full(*args: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(labels.os, "replace", raise_disk_full)
    with pytest.raises(OSError):
        labels.save_label_cache({"s": {"pt": "y"}})
    tmp_file = files.task.with_name(files.task.name + ".tmp")
    assert not tmp_file.exists()


def test_a_corrupt_cache_reads_as_empty(files: LabelFiles) -> None:
    files.task.write_text("{not json")
    assert labels.load_label_cache() == {}


def test_saving_over_a_corrupt_file_makes_no_backup_and_still_succeeds(
    files: LabelFiles,
) -> None:
    # _holds_old_shape can't tell an unparsable file apart from any other
    # shape it doesn't recognise - it treats it as not-old-shape, same as
    # load_label_cache treats a corrupt read as an empty cache, so no backup
    # is made and the corrupt file is simply overwritten.
    files.task.write_text("{not json")
    labels.save_label_cache({"s": {"pt": "y"}})
    backup = files.task.with_name(files.task.name + ".pre-i18n")
    assert not backup.exists()
    assert json.loads(files.task.read_text()) == {"s": {"pt": "y"}}


def test_a_non_dict_json_cache_reads_as_empty(files: LabelFiles) -> None:
    files.task.write_text(json.dumps(["not", "a", "dict"]))
    assert labels.load_label_cache() == {}


def test_migration_drops_a_value_that_is_neither_string_nor_dict(files: LabelFiles) -> None:
    files.task.write_text(json.dumps({"s-good": "a label", "s-bad": 42}))
    assert labels.load_label_cache() == {"s-good": {"pt": "a label"}}


def test_migration_keeps_an_already_migrated_dict_value_as_is(files: LabelFiles) -> None:
    files.task.write_text(json.dumps({"s1": {"pt": "rotulo", "en": "label"}}))
    assert labels.load_label_cache() == {"s1": {"pt": "rotulo", "en": "label"}}


def test_migration_drops_a_non_string_label_inside_an_already_migrated_entry(
    files: LabelFiles,
) -> None:
    files.task.write_text(json.dumps({"s1": {"pt": "rotulo", "en": 42}}))
    assert labels.load_label_cache() == {"s1": {"pt": "rotulo"}}


def test_missing_file_still_returns_an_empty_cache(files: LabelFiles) -> None:
    assert labels.load_label_cache() == {}


# --- migration: agent labels (mirrors the task-label tests above) --------


def test_an_old_agent_cache_migrates_as_portuguese_with_nothing_lost(files: LabelFiles) -> None:
    files.agent.write_text(json.dumps(AGENT_OLD))
    assert labels.load_agent_label_cache() == {k: {"pt": v} for k, v in AGENT_OLD.items()}


def test_the_first_agent_save_keeps_a_one_time_backup_of_the_old_file(files: LabelFiles) -> None:
    files.agent.write_text(json.dumps(AGENT_OLD))
    cache = labels.load_agent_label_cache()
    labels.save_agent_label_cache(cache)
    backup = files.agent.with_name(files.agent.name + ".pre-i18n")
    assert json.loads(backup.read_text()) == AGENT_OLD
    cache["a-new"] = {"en": "x"}
    labels.save_agent_label_cache(cache)
    assert json.loads(backup.read_text()) == AGENT_OLD, "the backup must never be overwritten"


def test_an_agent_failed_write_leaves_the_old_file_whole(
    files: LabelFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    files.agent.write_text(json.dumps(AGENT_OLD))

    def raise_disk_full(*args: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(labels.os, "replace", raise_disk_full)
    with pytest.raises(OSError, match="disk full"):
        labels.save_agent_label_cache({"a": {"pt": "y"}})
    assert json.loads(files.agent.read_text()) == AGENT_OLD


def test_an_agent_corrupt_cache_reads_as_empty(files: LabelFiles) -> None:
    files.agent.write_text("{not json")
    assert labels.load_agent_label_cache() == {}


# --- reuse without spending budget ----------------------------------------


def test_a_session_name_is_reused_in_english_without_spending_budget(spy: Spy) -> None:
    record = {"session_id": "s-named", "internal_name": "fix auth redirect"}
    cache = {"s-named": {"pt": "fix auth redirect"}}
    budget = labels.LabelBudget(5)
    assert labels.get_task_label(record, cache, budget, lang="en") == "fix auth redirect"
    assert budget.remaining == 5
    assert spy.prompts == []


def test_an_id_cached_in_one_language_generates_fresh_for_the_other(
    spy: Spy, tmp_path: Path
) -> None:
    # An id already cached in "pt" is not itself a cache hit for "en" - the
    # cache-hit check is per (id, lang), so a model-written label must be
    # regenerated (spending budget, calling the model) for a language that
    # has never been asked for, rather than being reused verbatim the way an
    # internal_name is. Both entries end up on the same id afterwards.
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps({"type": "assistant", "message": {"content": "some text"}}) + "\n")
    record = {"agent_id": "a-model", "agent_transcript_path": str(path)}
    cache: labels.LabelCache = {"a-model": {"pt": "corrigindo o teste"}}
    budget = labels.LabelBudget(1)
    result = labels.get_agent_label(record, cache, budget, lang="en")
    assert result == "a label"
    assert budget.remaining == 0
    assert "in English" in spy.prompts[0]
    assert cache["a-model"] == {"pt": "corrigindo o teste", "en": "a label"}


# --- the English prompt asks for English -----------------------------------


def test_an_english_label_is_asked_for_in_english(spy: Spy, tmp_path: Path) -> None:
    # The shape cold_summary_source reads: one JSON object per line, user
    # and assistant turns with a string `content` (see tests/test_labels.py).
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "please fix the login redirect"}})
        + "\n"
        + json.dumps({"type": "assistant", "message": {"content": "fixed the redirect loop"}})
        + "\n"
    )
    record = {"session_id": "s", "transcript_path": str(transcript)}
    assert labels.get_task_label(record, {}, labels.LabelBudget(1), lang="en") == "a label"
    assert "in English" in spy.prompts[0]
    assert "please fix the login redirect" in spy.prompts[0]


def test_a_portuguese_label_is_asked_for_in_portuguese(spy: Spy, tmp_path: Path) -> None:
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"content": "conserta o login"}}) + "\n"
    )
    record = {"session_id": "s", "transcript_path": str(transcript)}
    assert labels.get_task_label(record, {}, labels.LabelBudget(1), lang="pt") == "a label"
    assert "portugues" in spy.prompts[0]
    assert "conserta o login" in spy.prompts[0]


# --- the two prompts per language are exactly two lines of text -----------


def test_label_prompts_has_exactly_the_two_documented_languages() -> None:
    assert set(labels.LABEL_PROMPTS) == {"pt", "en"}
    for prefix, retry_prefix in labels.LABEL_PROMPTS.values():
        assert isinstance(prefix, str)
        assert isinstance(retry_prefix, str)
        assert prefix
        assert retry_prefix
