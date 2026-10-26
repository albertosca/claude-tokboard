import json
from pathlib import Path

import metrics_core as mr
import pytest
from metrics_core import model_slots_store as store


def _file(directory: Path) -> Path:
    return directory / store.MODEL_SLOTS_FILE_NAME


def test_a_model_keeps_its_slot_when_a_back_dated_model_would_have_taken_it(
    tmp_path: Path,
) -> None:
    first = mr.assign_persistent_model_slots(["local-a", "local-b"], 6, tmp_path)
    # A repair script gives a new model the earliest first appearance.
    second = mr.assign_persistent_model_slots(["local-new", "local-a", "local-b"], 6, tmp_path)

    assert second["local-a"] == first["local-a"]
    assert second["local-b"] == first["local-b"]
    assert second["local-new"].palette_index not in {
        first["local-a"].palette_index,
        first["local-b"].palette_index,
    }
    unpinned = mr.assign_model_slots(["local-new", "local-a", "local-b"], 6)
    assert unpinned["local-a"] != first["local-a"]


def test_a_model_missing_from_a_shrunken_history_still_holds_its_slot(tmp_path: Path) -> None:
    first = mr.assign_persistent_model_slots(["local-a", "local-b"], 6, tmp_path)

    shrunk = mr.assign_persistent_model_slots(["local-b"], 6, tmp_path)
    regrown = mr.assign_persistent_model_slots(["local-b", "local-a", "local-c"], 6, tmp_path)

    assert shrunk["local-b"] == first["local-b"]
    assert regrown["local-a"] == first["local-a"]
    assert regrown["local-c"].palette_index not in {
        first["local-a"].palette_index,
        first["local-b"].palette_index,
    }


def test_the_two_palette_sizes_are_pinned_separately(tmp_path: Path) -> None:
    mr.assign_persistent_model_slots(["local-a"], 6, tmp_path)
    mr.assign_persistent_model_slots(["local-b", "local-a"], 8, tmp_path)

    stored = json.loads(_file(tmp_path).read_text())
    assert set(stored) == {"6", "8"}
    assert set(stored["8"]) == {"local-a", "local-b"}


def test_overflow_models_are_not_pinned(tmp_path: Path) -> None:
    assignment = mr.assign_persistent_model_slots(["local-a", "local-b", "local-c"], 2, tmp_path)

    assert assignment["local-c"].is_overflow
    assert set(json.loads(_file(tmp_path).read_text())["2"]) == {"local-a", "local-b"}


def test_nothing_is_written_when_no_model_is_new(tmp_path: Path) -> None:
    mr.assign_persistent_model_slots(["local-a"], 6, tmp_path)
    before = _file(tmp_path).stat().st_mtime_ns

    mr.assign_persistent_model_slots(["local-a"], 6, tmp_path)

    assert _file(tmp_path).stat().st_mtime_ns == before
    assert not _file(tmp_path).with_name("model_slots.json.tmp").exists()


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        '{"6": []}',
        '{"6": {"local-a": "x", "local-b": true, "local-c": 99, "local-d": -1}}',
    ],
)
def test_a_malformed_file_pins_nothing_and_is_replaced(tmp_path: Path, content: str) -> None:
    _file(tmp_path).write_text(content)

    assignment = mr.assign_persistent_model_slots(["local-a"], 6, tmp_path)

    assert not assignment["local-a"].is_overflow
    assert json.loads(_file(tmp_path).read_text())["6"] == {
        "local-a": assignment["local-a"].palette_index
    }


def test_two_models_pinned_to_one_slot_keep_only_the_first(tmp_path: Path) -> None:
    _file(tmp_path).write_text('{"6": {"local-a": 4, "local-b": 4}}')

    assert store.load_pinned_slots(_file(tmp_path), 6) == {"local-a": 4}
    assignment = mr.assign_persistent_model_slots(["local-a", "local-b"], 6, tmp_path)
    assert assignment["local-a"].palette_index != assignment["local-b"].palette_index


def test_an_unwritable_directory_does_not_break_the_render(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    assignment = mr.assign_persistent_model_slots(["local-a"], 6, missing)

    assert "local-a" in assignment
    assert not missing.exists()
