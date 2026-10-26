"""The colour slot each model was given, kept between renders.

`assign_model_slots` derives slots from the history in first-appearance order,
which is stable for an append-only history but moves a colour when a repair
script back-dates a model, or when the history shrinks (backlog 2026-09-28 #1).
Pinning what was already shown removes both: a model keeps the slot it was
first drawn with. The file holds only display state, never metrics, and a
missing, unreadable or malformed file just means nothing is pinned.

hooks import metrics_core and may run under a stock macOS Python 3.9.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .format import ModelColorSlot, assign_model_slots

MODEL_SLOTS_FILE_NAME = "model_slots.json"


def load_pinned_slots(path: Path, slots: int) -> dict[str, int]:
    """model id -> palette index for the given palette size.

    Entries that are out of range, repeat a slot already pinned, or are not
    strings and integers are dropped, so a hand-edited file cannot break the
    one-model-per-slot rule.
    """
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = stored.get(str(slots)) if isinstance(stored, dict) else None
    if not isinstance(entries, dict):
        return {}
    pinned: dict[str, int] = {}
    for model, index in entries.items():
        if (
            isinstance(index, int)
            and not isinstance(index, bool)
            and 0 <= index < slots
            and index not in pinned.values()
        ):
            pinned[model] = index
    return pinned


def save_pinned_slots(
    path: Path, slots: int, assignment: dict[str, ModelColorSlot], pinned: dict[str, int]
) -> None:
    """Add the models that got a slot of their own to the file.

    Overflow models are not pinned: they share a colour by definition. The
    write is skipped when nothing is new, is atomic, and a failure (read-only
    directory) is swallowed - the colours are still right for this render.
    """
    fresh = {
        model: slot.palette_index
        for model, slot in assignment.items()
        if not slot.is_overflow and model not in pinned
    }
    if not fresh:
        return
    try:
        stored: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            stored = {}
    except (OSError, ValueError):
        stored = {}
    stored[str(slots)] = {**pinned, **fresh}
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def assign_persistent_model_slots(
    models: list[str], slots: int, metrics_directory: Path
) -> dict[str, ModelColorSlot]:
    """`assign_model_slots` over what the directory's file already pinned, then
    pin whatever is new. What both dashboards call for the whole-history slots."""
    path = metrics_directory / MODEL_SLOTS_FILE_NAME
    pinned = load_pinned_slots(path, slots)
    assignment = assign_model_slots(models, slots, pinned)
    save_pinned_slots(path, slots, assignment, pinned)
    return assignment
