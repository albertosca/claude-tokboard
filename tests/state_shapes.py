"""Every shape of backfill state that exists but cannot be read safely.

One list, imported by every test that needs it, so the library, the two
scripts and the dashboards are all checked against the same shapes and
cannot drift apart. A writer that took any of these for an empty state
would replace what the file holds with its own entry alone, so an apply
refuses them all; the dashboards read them leniently and still render.

The container shapes are readable JSON objects with a malformed interior:
a "fields" or "subagents" value, or one "fields" entry, that is not an
object. Those are the containers the writers merge into.
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


UNREADABLE_STATES: dict[str, bytes] = {
    "malformed JSON": b"{not json",
    "a list at the top level": b"[1, 2]",
    "a number at the top level": b"7",
    "not UTF-8": b'\xff\xfe{"cutoff": "c"}',
    "empty": b"",
    UNREADABLE_SHAPE: b'{"cutoff": "2026-08-21", "applied_at": "a"}',
    **_container_shapes(),
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
