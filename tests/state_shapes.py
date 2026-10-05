"""Every shape of backfill state that exists but cannot be read safely.

One list, imported by every test that needs it, so the library, the two
scripts and the dashboards are all checked against the same shapes and
cannot drift apart. A writer that took any of these for an empty state
would replace what the file holds with its own entry alone, so an apply
refuses them all; the dashboards read them leniently and still render.

The container shapes are readable JSON objects with a malformed interior:
a "fields" or "subagents" value, or one "fields" entry, that is not an
object. Those are the containers the writers merge into.

The leaf shapes are readable containers with one malformed value inside
an entry - the flat sessions entry at the top level, the "subagents"
entry, or a "fields" entry: a count that is not a non-negative integer,
a date or time that is not a string, a row-name list that is not a list
of non-empty strings. A writer would carry such a value on, or fold it
into what it writes, so it is refused like a malformed container.
"""

from __future__ import annotations

import json
from pathlib import Path

UNREADABLE_SHAPE = "unreadable"


def _dumped(state: object) -> bytes:
    return json.dumps(state).encode("utf-8")


def _container_shapes() -> dict[str, bytes]:
    shapes: dict[str, bytes] = {}
    for label, value in (("a list", [1]), ("null", None), ("a string", "junk")):
        shapes[f"fields is {label}"] = _dumped({"cutoff": "c", "applied_at": "a", "fields": value})
        shapes[f"subagents is {label}"] = _dumped(
            {"cutoff": "c", "applied_at": "a", "subagents": value}
        )
        shapes[f"a fields entry is {label}"] = _dumped(
            {"cutoff": "c", "applied_at": "a", "fields": {"sessions.tool_calls": value}}
        )
    return shapes


def _in_sessions(key: str, value: object) -> bytes:
    return _dumped({"cutoff": "c", "applied_at": "a", key: value})


def _in_subagents(key: str, value: object) -> bytes:
    return _dumped({"subagents": {"cutoff": "c", "applied_at": "a", key: value}})


def _in_fields(key: str, value: object) -> bytes:
    return _dumped(
        {"fields": {"sessions.tool_calls": {"cutoff": "c", "applied_at": "a", key: value}}}
    )


def _leaf_shapes() -> dict[str, bytes]:
    return {
        "the sessions cutoff is a number": _dumped({"cutoff": 7, "applied_at": "a"}),
        "the sessions applied_at is null": _dumped({"cutoff": "c", "applied_at": None}),
        "the sessions earliest_corrected is a number": _in_sessions("earliest_corrected", 7),
        "the sessions corrected_rows is negative": _in_sessions("corrected_rows", -1),
        "the sessions corrected_row_ids holds an empty name": _in_sessions(
            "corrected_row_ids", [""]
        ),
        "the subagents applied_at is a number": _in_subagents("applied_at", 7),
        "the subagents corrected_rows is a boolean": _in_subagents("corrected_rows", True),
        "the subagents uncorrectable_rows is null": _in_subagents("uncorrectable_rows", None),
        "the subagents unidentified_earlier_rows is negative": _in_subagents(
            "unidentified_earlier_rows", -3
        ),
        "a fields entry's cutoff is a list": _in_fields("cutoff", ["c"]),
        "a fields entry's corrected_rows is a string": _in_fields("corrected_rows", "2000"),
        "a fields entry's corrected_rows is a decimal": _in_fields("corrected_rows", 3.5),
        "a fields entry's rows_changed_this_run is null": _in_fields("rows_changed_this_run", None),
        "a fields entry's unidentified_earlier_rows is a string": _in_fields(
            "unidentified_earlier_rows", "x"
        ),
        "a fields entry's corrected_row_ids is a string": _in_fields("corrected_row_ids", "abc"),
        "a fields entry's corrected_row_ids holds a number": _in_fields(
            "corrected_row_ids", [1, "abc"]
        ),
    }


UNREADABLE_STATES: dict[str, bytes] = {
    "malformed JSON": b"{not json",
    "a list at the top level": b"[1, 2]",
    "a number at the top level": b"7",
    "not UTF-8": b'\xff\xfe{"cutoff": "c"}',
    "empty": b"",
    UNREADABLE_SHAPE: b'{"cutoff": "2026-08-21", "applied_at": "a"}',
    **_container_shapes(),
    **_leaf_shapes(),
}

SHAPE_NAMES = sorted(UNREADABLE_STATES)


def write_unreadable_state(path: Path, shape: str) -> None:
    """Write `shape` to `path`; the unreadable shape also drops every
    permission. Call restore_permissions before reading it back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(UNREADABLE_STATES[shape])
    if shape == UNREADABLE_SHAPE:
        path.chmod(0o000)


def restore_permissions(path: Path) -> None:
    path.chmod(0o644)
