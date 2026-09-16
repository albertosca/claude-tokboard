#!/usr/bin/env python3
"""Reconstruct the per-model token map of historical metrics rows from the
session transcripts those rows were parsed from.

What is reconstructed
---------------------
Since the capture change, hooks/metrics-log.py and
hooks/subagent-metrics-log.py write a `tokens_by_model` map alongside the
four token totals: model id -> {input_tokens, output_tokens,
cache_read_tokens, cache_creation_tokens}. Every row written before that
deploy has the four totals and no map. Where the transcript a row was
parsed from still exists on disk, the map can be rebuilt from it, by the
same attribution rules the hooks use - scan_transcript() and
model_totals_as_of() in lib/metrics_core/backfill.py are shared with the
capture path on purpose, so the message.id dedup, the `<synthetic>` rule
and the "unknown" fallback exist once rather than twice.

Why each snapshot is computed AS OF ITS OWN moment
--------------------------------------------------
The Stop hook appends a fresh CUMULATIVE snapshot every turn, so a session
owns a GROWING SERIES of rows, and expand_records_to_daily_deltas() rebuilds
per-day usage by differencing consecutive snapshots - per model as well as
in total. Giving every row of a session the map of that session's FINAL
state would charge the whole session to the day of its first snapshot and
leave every later delta empty. So each row is reconstructed from the
transcript entries at or before that row's own `timestamp_end`, and only
those.

Why the decision is per unit, not per row
-----------------------------------------
Filling the map in some snapshots of a session and not others makes the seam
between a filled snapshot and an empty one read as a burst of usage on the
day the seam falls on. The dedup backfill measured exactly that on real
data: a per-row cutoff INVENTED 1.9 billion tokens on a single day. So a
unit (a session, or a subagent) is planned only when every one of its rows
is resolved - reconstructed from its transcript, or already carrying a
natively written map - and is otherwise left completely alone. A unit that
cannot be reconstructed keeps no key at all: absence is how this schema
already spells "unknown", and thousands of explicit markers would rewrite
most of an irreplaceable file to say what the absence says.

Why the native records are the acceptance gate
----------------------------------------------
Capture went live mid-session, so a number of units own snapshots from both
sides of the deploy: early ones without the map, later ones with a map the
hook computed itself, at the time, from the live transcript. Those snapshots
are an oracle the reconstruction must reproduce exactly, drawn from
production data and free to collect. check_against_native() runs FIRST,
before a plan is even computed, and a single disagreement aborts the run: a
reconstructor that is wrong about one snapshot where the answer is known has
no claim on the thousands where it is not.

This half of the backfill writes nothing at all - there is no write path in
this file, by design. A --apply flag, with the backup/verify/rename
machinery scripts/backfill-token-dedup.py already carries, is a separate
change.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core.backfill import (
    index_transcripts,
    model_totals_as_of,
    scan_transcript,
    token_sum,
)
from metrics_core.records import load_records

METRICS_DIR = str(Path("~/.claude/metrics").expanduser())
PROJECTS_DIR = str(Path("~/.claude/projects").expanduser())

FIELD = "tokens_by_model"

# Which id names a unit, and which field names the transcript it was parsed
# from, in each of the two metrics files. Both files feed the same
# expand_records_to_daily_deltas() call, so backfilling only one would leave
# the per-model view disagreeing with its own totals on every day that had
# subagents.
UNIT_KEYS: dict[str, tuple[str, str]] = {
    "sessions": ("session_id", "transcript_path"),
    "subagents": ("agent_id", "agent_transcript_path"),
}

# The inverse, so the two public functions can take just the transcript key -
# the thing that actually differs between the two files - and still know
# which id groups a unit.
ID_KEY_FOR_TRANSCRIPT_KEY: dict[str, str] = {
    transcript_key: id_key for id_key, transcript_key in UNIT_KEYS.values()
}

ModelMap = dict[str, dict[str, int]]
Timeline = list[tuple[str, bool, tuple[int, ...], str]]


def resolve_transcript(
    record: dict[str, Any], transcripts: dict[str, str], transcript_key: str, unit_id: Any
) -> str | None:
    """The transcript this row was parsed from, or None if it is gone.

    A row that RECORDED a path whose file is no longer there gets a second
    chance through the by-id index: a session logged from two working
    directories exists under two project slugs, so a stored path can be stale
    while the transcript itself is still on disk under the same id.

    A row that recorded NO path never had one - there is nothing on disk to
    look for, and no recorded path to confirm an index hit against - so it
    resolves to None and (see plan_backfill) disqualifies its whole unit. The
    by-id index is a way to relocate a known transcript, not a way to guess
    one.
    """
    recorded = record.get(transcript_key)
    if not recorded:
        return None
    if Path(recorded).exists():
        return str(recorded)
    return transcripts.get(unit_id)


def rows_by_unit(records: list[dict[str, Any]], id_key: str) -> dict[Any, list[int]]:
    """Row indexes grouped by the unit whose growing series they are."""
    grouped: dict[Any, list[int]] = collections.defaultdict(list)
    for index, record in enumerate(records):
        grouped[record.get(id_key)].append(index)
    return grouped


def resolve_unit_transcripts(
    records: list[dict[str, Any]],
    indexes: list[int],
    transcripts: dict[str, str],
    transcript_key: str,
    unit_id: Any,
) -> dict[int, str] | None:
    """A transcript for every row of the unit, or None if any row lacks one.

    All-or-nothing is the point: one unresolvable row means the unit can only
    ever be written half way, which is the seam this backfill exists to avoid
    creating.
    """
    resolved: dict[int, str] = {}
    for index in indexes:
        path = resolve_transcript(records[index], transcripts, transcript_key, unit_id)
        if path is None:
            return None
        resolved[index] = path
    return resolved


def plan_backfill(
    records: list[dict[str, Any]], transcripts: dict[str, str], transcript_key: str
) -> tuple[dict[int, ModelMap], collections.Counter[str]]:
    """Row index -> reconstructed per-model map, decided per unit.

    A unit (session or subagent) is planned only when every one of its rows
    can be resolved: either the row already carries tokens_by_model, or its
    transcript exists and the map can be reconstructed as of that row's own
    timestamp_end. Rows already carrying the field are left out of the plan -
    they need no write - but they do not disqualify their unit.

    Returns the plan and a count of every row by disposition, so the dry run
    explains not only what it would do but what it refused to do and why.
    """
    id_key = ID_KEY_FOR_TRANSCRIPT_KEY[transcript_key]
    stats: collections.Counter[str] = collections.Counter()
    plan: dict[int, ModelMap] = {}
    timelines: dict[str, Timeline] = {}

    for unit_id, indexes in rows_by_unit(records, id_key).items():
        pending = [index for index in indexes if FIELD not in records[index]]
        native = len(indexes) - len(pending)
        if not pending:
            stats["untouched: every snapshot already has the field"] += native
            continue

        resolved = resolve_unit_transcripts(records, indexes, transcripts, transcript_key, unit_id)
        if resolved is None:
            stats["untouched: a snapshot has no transcript on disk"] += len(indexes)
            continue
        if any(not records[index].get("timestamp_end") for index in indexes):
            # Without the moment a snapshot was taken there is no as-of to
            # reconstruct at, and the final total is the wrong answer.
            stats["untouched: a snapshot has no timestamp_end"] += len(indexes)
            continue

        for index in pending:
            path = resolved[index]
            timeline = timelines.get(path)
            if timeline is None:
                timeline = timelines[path] = scan_transcript(path)
            plan[index] = model_totals_as_of(timeline, records[index]["timestamp_end"])
        stats["planned"] += len(pending)
        stats["untouched: snapshot already has the field"] += native
    return plan, stats


def checkable_native_rows(
    records: list[dict[str, Any]], transcript_key: str
) -> list[tuple[int, str]]:
    """(row index, transcript path) for every row the oracle can check.

    A row qualifies when it carries a natively written map AND still has the
    transcript that map was computed from AND records when it was taken. Rows
    that carry a map but lost their transcript are not failures - there is
    simply nothing left to check them against.
    """
    id_key = ID_KEY_FOR_TRANSCRIPT_KEY[transcript_key]
    checkable: list[tuple[int, str]] = []
    for index, record in enumerate(records):
        if FIELD not in record or not record.get("timestamp_end"):
            continue
        path = resolve_transcript(record, {}, transcript_key, record.get(id_key))
        if path is not None:
            checkable.append((index, path))
    return checkable


def check_against_native(records: list[dict[str, Any]], transcript_key: str) -> list[str]:
    """One message per snapshot whose reconstruction disagrees with its own
    natively written tokens_by_model; empty when every one agrees.

    This is the acceptance gate. The map the hook wrote at the time, from the
    live transcript, is the known-good answer for that snapshot, so a
    difference here says the reconstruction is wrong about a case where the
    truth is available - and therefore untrustworthy on the far larger set
    where it is not.
    """
    id_key = ID_KEY_FOR_TRANSCRIPT_KEY[transcript_key]
    timelines: dict[str, Timeline] = {}
    problems: list[str] = []
    for index, path in checkable_native_rows(records, transcript_key):
        record = records[index]
        timeline = timelines.get(path)
        if timeline is None:
            timeline = timelines[path] = scan_transcript(path)
        rebuilt = model_totals_as_of(timeline, record["timestamp_end"])
        if rebuilt != record[FIELD]:
            problems.append(
                f"line {index + 1}: {id_key}={record.get(id_key)!r} "
                f"as of {record['timestamp_end']}\n"
                f"      native {record[FIELD]}\n"
                f"    rebuilt {rebuilt}"
            )
    return problems


def print_plan_summary(
    records: list[dict[str, Any]],
    plan: dict[int, ModelMap],
    stats: collections.Counter[str],
    id_key: str,
) -> None:
    """The dry run's report: what would be written, and what would not.

    The volume figures compare each unit's LARGEST cumulative snapshot, which
    is that unit's whole consumption - so "covered" is the share of all
    tokens ever logged in this file that the backfill could attribute to a
    model, which is the number worth knowing, not the share of rows.
    """
    print("\nRow disposition")
    for reason, count in sorted(stats.items(), key=lambda item: -item[1]):
        print(f"  {count:>6}  {reason}")

    planned_units = {records[index].get(id_key) for index in plan}
    totals: dict[Any, int] = {
        unit_id: max((token_sum(records[index]) for index in indexes), default=0)
        for unit_id, indexes in rows_by_unit(records, id_key).items()
    }
    covered = sum(total for unit_id, total in totals.items() if unit_id in planned_units)
    overall = sum(totals.values())
    share = f"{covered / overall:.1%}" if overall else "-"

    print(f"\nUnits planned: {len(planned_units)} of {len(totals)}")
    print(f"Rows to write: {len(plan)} of {len(records)}")
    print(f"Token volume covered: {covered:,} of {overall:,} ({share})")
    earliest = min((records[index].get("timestamp_end") or "" for index in plan), default="")
    if earliest:
        print(f"Earliest snapshot that would be written: {earliest}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--file",
        choices=sorted(UNIT_KEYS),
        default="sessions",
        help="which metrics file to plan against",
    )
    parser.add_argument("--metrics-dir", default=METRICS_DIR)
    parser.add_argument("--projects-dir", default=PROJECTS_DIR)
    args = parser.parse_args()

    id_key, transcript_key = UNIT_KEYS[args.file]
    path = Path(args.metrics_dir) / f"{args.file}.jsonl"
    if not path.exists():
        print(f"no metrics file at {path}", file=sys.stderr)
        return 1

    records = load_records(str(path))
    print(f"read {len(records)} rows from {path}")

    # The gate runs before anything else is computed: if the reconstruction
    # cannot reproduce the snapshots whose answer is already known, nothing
    # it has to say about the rest is worth printing.
    checkable = checkable_native_rows(records, transcript_key)
    native = sum(1 for record in records if FIELD in record)
    print(
        f"oracle: {len(checkable)} of {native} natively written snapshots "
        f"still have their transcript"
    )
    problems = check_against_native(records, transcript_key)
    if problems:
        print(f"\nORACLE FAILED ({len(problems)} disagreements):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nnothing was written and nothing will be: a reconstruction that "
            "cannot reproduce a snapshot whose answer is known has no claim "
            "on the ones where it is not",
            file=sys.stderr,
        )
        return 1
    print(f"oracle: all {len(checkable)} reconstructions agree with the native value")

    transcripts = index_transcripts(args.projects_dir)
    print(f"indexed {len(transcripts)} session transcripts under {args.projects_dir}")

    plan, stats = plan_backfill(records, transcripts, transcript_key)
    print_plan_summary(records, plan, stats, id_key)
    print("\nREAD-ONLY - this script has no write path; --apply is a later change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
