#!/usr/bin/env python3
"""Recover the last call of subagents whose transcript ended on a placeholder.

Since Claude Code 2.1.278 the last message of many subagent transcripts never
gets its final usage entry, so the stored `output_tokens` is a floor. The
parent session records each finished agent's result with a `usage` block that
IS that last call (checked: `totalTokens` is the sum of one call's four
fields, `iterations` has one entry). This script raises the stored output by
the difference, for the rows where that is provable, and says why for every
row it leaves alone.

It repairs the LAST call only. Every earlier call that never got its usage
stays a floor: no other source holds it, and `incomplete_usage_messages`
keeps counting them. Run it, then re-run `backfill-token-dedup.py --kind
subagents`: a repaired row no longer matches the transcript's own total, so
that pass leaves the unit untouched and says so.

A dry run is the default; `--apply` backs the file up (verified by sha256),
rewrites it atomically and carries over what live hooks appended meanwhile.
Only `output_tokens` and the matching `tokens_by_model` bucket change.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core.backfill import script_invocation, sha256_bytes
from metrics_core.last_call import plan_last_call

METRICS_FILE = str(Path("~/.claude/metrics/subagents.jsonl").expanduser())


def load_writer() -> Any:
    """The sibling script that already owns the verified backup and the
    atomic, append-preserving rewrite - one writer for the irreplaceable file."""
    path = Path(__file__).with_name("backfill-token-dedup.py")
    spec = importlib.util.spec_from_file_location("backfill_token_dedup", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def problems(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> list[str]:
    """Anything besides output_tokens and the model map that moved, and any
    model map that no longer sums to its row's totals."""
    found = []
    for index, (old, new) in enumerate(zip(before, after, strict=True)):
        for key in (old.keys() | new.keys()) - {"output_tokens", "tokens_by_model"}:
            if old.get(key) != new.get(key):
                found.append(f"line {index + 1}: {key} changed")
        if new.get("output_tokens", 0) < old.get("output_tokens", 0):
            found.append(f"line {index + 1}: output_tokens fell")
        by_model = new.get("tokens_by_model")
        if isinstance(by_model, dict):
            total = sum(bucket.get("output_tokens", 0) for bucket in by_model.values())
            if total != new.get("output_tokens", 0):
                found.append(f"line {index + 1}: the model map no longer sums to the row")
    return found


def plan(records: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[str, int], int]:
    """(row index -> repaired row, skip reason -> rows, tokens gained)."""
    parents: dict[str, dict[str, tuple[int, ...] | None]] = {}
    repaired: dict[int, dict[str, Any]] = {}
    skipped: dict[str, int] = collections.Counter()
    gained = 0
    # Only the newest row of an agent can be its final snapshot.
    newest: dict[str, int] = {}
    for index, record in enumerate(records):
        agent_id = record.get("agent_id")
        if not isinstance(agent_id, str):
            continue
        current = newest.get(agent_id)
        if current is None or record.get("timestamp_end", "") >= records[current].get(
            "timestamp_end", ""
        ):
            newest[agent_id] = index
    for index in sorted(newest.values()):
        updated, reason = plan_last_call(records[index], parents)
        if updated is None:
            skipped[str(reason)] += 1
        else:
            repaired[index] = updated
            gained += updated["output_tokens"] - records[index]["output_tokens"]
    return repaired, dict(skipped), gained


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", default=METRICS_FILE, help="the subagents metrics file")
    parser.add_argument("--backup-dir", default=None, help="default: backups/ beside the file")
    parser.add_argument("--apply", action="store_true", help="write; without it this is a dry run")
    arguments = parser.parse_args()

    path = Path(arguments.file)
    if not path.exists():
        print(f"no metrics file at {path}", file=sys.stderr)
        return 1
    data = path.read_bytes()
    snapshot = data[: data.rfind(b"\n") + 1]
    lines = snapshot.split(b"\n")[:-1]
    records = [json.loads(line) for line in lines]
    print(f"read {len(lines)} lines from {path}\n  sha256 {sha256_bytes(data)}")

    repaired, skipped, gained = plan(records)
    print(f"\nRows whose last call would be recovered: {len(repaired)} (+{gained:,} output tokens)")
    for reason, count in sorted(skipped.items(), key=lambda item: -item[1]):
        print(f"  left alone, {reason}: {count}")
    corrected = [repaired.get(index, record) for index, record in enumerate(records)]
    found = problems(records, corrected)
    if found:
        print("\nVERIFICATION FAILED, nothing written:\n  " + "\n  ".join(found), file=sys.stderr)
        return 1
    print("\nverification: only output_tokens and its model bucket moved; every map sums")
    if not arguments.apply:
        _, command = script_invocation(__file__)
        print(f"\nDRY RUN - nothing written. Re-run with --apply to write: {command} --apply")
        return 0
    if not repaired:
        print("nothing to write")
        return 0
    writer = load_writer()
    backup_directory = arguments.backup_dir or str(path.parent / "backups")
    return int(
        writer.back_up_and_write(
            str(path), snapshot, len(lines), corrected, backup_directory, lambda: None
        )
    )


if __name__ == "__main__":
    sys.exit(main())
