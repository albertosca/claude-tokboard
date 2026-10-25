#!/usr/bin/env python3
"""Seed calls.jsonl and limit-hits.jsonl from the transcripts still on disk.

Background
----------
The two logging hooks now append one row per API response and one per
rate-limit rejection (see metrics_core.calls_store). Transcripts older than
the hooks, and every transcript Claude Code has already deleted, are not
covered, so this script rebuilds what the transcripts that remain can still
say. Run it once after installing the version that adds the hooks; history
is lost a little every day it waits.

Safety
------
- Dry run by default: it reads the transcripts and the existing store, prints
  what it would write, and writes nothing. --apply writes.
- It only APPENDS to calls.jsonl and limit-hits.jsonl (new files, so there is
  nothing to restore) and never opens sessions.jsonl, subagents.jsonl or
  memory-reads.jsonl. There is no --cutoff and no backup: a row is written
  only when its message id is absent from the store, so a rerun writes
  nothing, and there are no old-hook rows to spare.
- It stores each transcript's end offset in calls_cursor.json, so the hooks
  continue from where it stopped instead of reading everything again.
- A message replayed into a second transcript (a resumed session) is written
  once, at its per-field peak, the same rule the readers apply.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core import script_invocation
from metrics_core.calls_store import (
    CALLS_FILE_NAME,
    CURSOR_FILE_NAME,
    LIMIT_HITS_FILE_NAME,
    USAGE_FIELDS,
    append_rows,
    load_cursor,
    read_calls,
    read_limit_hits,
    read_region,
    save_cursor,
)

PROJECTS_DIRECTORY = str(Path("~/.claude/projects").expanduser())
METRICS_DIRECTORY = str(Path("~/.claude/metrics").expanduser())


class Plan:
    """What a run found and would write. A plain class, not a dataclass: this
    file is loaded by path in the tests, and dataclasses need the module in
    sys.modules."""

    def __init__(self) -> None:
        self.transcripts_scanned = 0
        self.rows_read = 0
        self.unique_calls = 0
        self.already_stored = 0
        self.fallback_ids = 0
        self.calls_to_write: list[dict[str, Any]] = []
        self.hits_to_write: list[dict[str, Any]] = []
        self.offsets: dict[str, int] = {}
        self.earliest: str | None = None
        self.latest: str | None = None

    @property
    def duplicates(self) -> int:
        return self.rows_read - self.unique_calls


def _hit_key(hit: dict[str, Any]) -> tuple[Any, ...]:
    return (hit.get("timestamp"), hit.get("rate_limit_type"), hit.get("resets_at"))


def make_plan(projects_directory: Path, metrics_directory: Path) -> Plan:
    plan = Plan()
    merged: dict[str, dict[str, Any]] = {}
    hits: dict[tuple[Any, ...], dict[str, Any]] = {}
    for transcript in sorted(projects_directory.rglob("*.jsonl")):
        try:
            rows, found_hits, offset = read_region(str(transcript), 0)
        except OSError:
            continue
        plan.transcripts_scanned += 1
        plan.offsets[str(transcript)] = offset
        plan.rows_read += len(rows)
        for row in rows:
            known = merged.get(row["message_id"])
            if known is None:
                merged[row["message_id"]] = row
                continue
            for usage_field in USAGE_FIELDS:
                known[usage_field] = max(known[usage_field], row[usage_field])
        for hit in found_hits:
            hits.setdefault(_hit_key(hit), hit)
    plan.unique_calls = len(merged)
    stored_ids = {row["message_id"] for row in read_calls(metrics_directory / CALLS_FILE_NAME)}
    stored_hits = {
        _hit_key(hit) for hit in read_limit_hits(metrics_directory / LIMIT_HITS_FILE_NAME)
    }
    for message_id, row in merged.items():
        timestamp = row.get("timestamp")
        if isinstance(timestamp, str):
            plan.earliest = timestamp if plan.earliest is None else min(plan.earliest, timestamp)
            plan.latest = timestamp if plan.latest is None else max(plan.latest, timestamp)
        if message_id in stored_ids:
            plan.already_stored += 1
            continue
        if message_id.startswith("@"):
            plan.fallback_ids += 1
        plan.calls_to_write.append(row)
    plan.hits_to_write = [hit for key, hit in hits.items() if key not in stored_hits]
    return plan


def apply_plan(plan: Plan, metrics_directory: Path) -> None:
    append_rows(metrics_directory / CALLS_FILE_NAME, plan.calls_to_write)
    append_rows(metrics_directory / LIMIT_HITS_FILE_NAME, plan.hits_to_write)
    cursor = load_cursor(metrics_directory)
    cursor.update(plan.offsets)
    save_cursor(metrics_directory, cursor)


def print_report(plan: Plan, metrics_directory: Path) -> None:
    print(f"transcripts scanned: {plan.transcripts_scanned}")
    print(f"call rows read: {plan.rows_read}")
    print(f"unique calls: {plan.unique_calls}")
    print(f"duplicates across transcripts: {plan.duplicates}")
    print(f"already in the store: {plan.already_stored}")
    print(f"calls to write: {len(plan.calls_to_write)}")
    print(f"calls keyed by a fallback id (no message id): {plan.fallback_ids}")
    print(f"limit hits to write: {len(plan.hits_to_write)}")
    earliest = plan.earliest[:10] if plan.earliest else "none"
    latest = plan.latest[:10] if plan.latest else "none"
    print(f"earliest day reachable: {earliest}")
    print(f"latest day reached: {latest}")
    cursor_file = metrics_directory / CURSOR_FILE_NAME
    if (
        cursor_file.exists()
        and cursor_file.stat().st_size > 0
        and not load_cursor(metrics_directory)
    ):
        print("note: the cursor file could not be read, so it will be rebuilt from this run")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--projects-dir", default=PROJECTS_DIRECTORY)
    parser.add_argument("--metrics-dir", default=METRICS_DIRECTORY)
    parser.add_argument("--apply", action="store_true", help="write; the default is a dry run")
    arguments = parser.parse_args()
    metrics_directory = Path(arguments.metrics_dir)
    plan = make_plan(Path(arguments.projects_dir), metrics_directory)
    print_report(plan, metrics_directory)
    if arguments.apply:
        apply_plan(plan, metrics_directory)
        print("applied")
        return 0
    command = shlex.join(
        [
            *script_invocation(__file__),
            "--projects-dir",
            arguments.projects_dir,
            "--metrics-dir",
            arguments.metrics_dir,
            "--apply",
        ]
    )
    print("dry run: nothing was written")
    print(f"to apply, run: {command}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
