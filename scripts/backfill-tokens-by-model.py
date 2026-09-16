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
are an oracle the reconstruction is measured against, drawn from production
data and free to collect. check_against_native() runs FIRST, before a plan is
even computed, and a single disagreement aborts the run: a reconstructor that
is wrong about one snapshot where the answer is known has no claim on the
thousands where it is not.

"Disagreement" is bounded, and bounded in one direction only. A record stores
`timestamp_end`, never the line number the hook actually summed to, so in a
transcript whose timestamps run backwards against file order no
timestamp-derived cut can reproduce the hook exactly - and the error it makes
can only be a SHORTFALL, never an invention. Measured over every oracle
snapshot on 2026-09-16: 102 of 104 sessions exact, worst residue 0.792%
downward, and 85 of 85 subagents exact. So the gate accepts equality or a
shortfall of at most 1%, and refuses an over-count of any size at all. See
diff_against_native.

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
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core.backfill import (
    index_transcripts,
    model_totals_as_of,
    scan_transcript,
    token_sum,
)
from metrics_core.records import load_records
from metrics_core.transcript_ingest import TOKEN_FIELDS

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

# How far BELOW a natively written value a reconstruction may land and still
# be accepted, per model and per token field. Strictly one-directional: see
# diff_against_native for why an over-count is refused at any magnitude.
ORACLE_TOLERANCE = 0.01


class OracleReport(NamedTuple):
    """What the acceptance gate found: problems first, because that is what
    aborts the run; the two counts exist so a sudden jump in `tolerated` is
    visible instead of being averaged into a pass."""

    problems: list[str]
    exact: int
    tolerated: int


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
        if native:
            # Guarded so the disposition table never prints a reason with a
            # count of zero: an operator reading it before authorising a
            # write should see only categories that happened.
            stats["untouched: snapshot already has the field"] += native
    return plan, stats


def diff_against_native(native: ModelMap, rebuilt: ModelMap) -> tuple[list[str], bool]:
    """(complaints, tolerated) for one snapshot's reconstruction.

    The gate is deliberately ASYMMETRIC. A reconstruction may land up to
    ORACLE_TOLERANCE BELOW the natively written value and still pass; landing
    even fractionally above it fails at any magnitude.

    That is not a double standard, it is the lesson this repo already paid
    for: the dedup backfill's worst outcome was INVENTING 1.9 billion tokens
    on a single day, and a per-model bar chart shows a fabricated bar
    loudly while a sub-1% shortfall is invisible. The measured residue is
    also structurally one-directional - a record stores `timestamp_end`, not
    the line the hook actually summed to, so in a transcript whose timestamps
    run backwards against file order an as-of cut can only ever MISS entries
    the hook counted, never add ones it did not.

    A model bucket present on one side and absent on the other is never a
    tolerance case: that is a missing or invented series, not a rounding
    difference, and it is reported whatever the numbers say.
    """
    complaints: list[str] = []
    tolerated = False
    only_native = sorted(set(native) - set(rebuilt))
    only_rebuilt = sorted(set(rebuilt) - set(native))
    if only_native or only_rebuilt:
        complaints.append(
            f"model buckets differ: only in the native map {only_native}, "
            f"only in the reconstruction {only_rebuilt}"
        )
    for model in sorted(set(native) & set(rebuilt)):
        for field in TOKEN_FIELDS:
            recorded = native[model].get(field, 0) or 0
            found = rebuilt[model].get(field, 0) or 0
            if found == recorded:
                continue
            if found > recorded:
                complaints.append(
                    f"{model}.{field}: native {recorded:,}, rebuilt {found:,} - "
                    f"ABOVE native by {found - recorded:,}; an over-count is never tolerated"
                )
                continue
            # found < recorded, so recorded is at least 1 and the share is
            # well defined without a zero guard.
            shortfall = recorded - found
            share = shortfall / recorded
            if share <= ORACLE_TOLERANCE:
                tolerated = True
            else:
                complaints.append(
                    f"{model}.{field}: native {recorded:,}, rebuilt {found:,} - "
                    f"below native by {shortfall:,} ({share:.3%}), past the "
                    f"{ORACLE_TOLERANCE:.0%} floor"
                )
    return complaints, tolerated


def checkable_native_rows(
    records: list[dict[str, Any]], transcript_key: str, transcripts: dict[str, str] | None = None
) -> list[tuple[int, str]]:
    """(row index, transcript path) for every row the oracle can check.

    A row qualifies when it carries a natively written map AND still has the
    transcript that map was computed from AND records when it was taken. Rows
    that carry a map but lost their transcript are not failures - there is
    simply nothing left to check them against.

    `transcripts` is the by-id index, and passing it is not optional in
    practice: transcripts migrate between project slugs while their sessions
    are alive (measured 2026-09-16 - the two snapshots that first exposed the
    tolerance question moved out of a worktree slug within the hour). Without
    it those rows drop out of the oracle silently, and a gate that quietly
    stops checking the hard cases reports a pass it has not earned.
    """
    id_key = ID_KEY_FOR_TRANSCRIPT_KEY[transcript_key]
    checkable: list[tuple[int, str]] = []
    for index, record in enumerate(records):
        if FIELD not in record or not record.get("timestamp_end"):
            continue
        path = resolve_transcript(record, transcripts or {}, transcript_key, record.get(id_key))
        if path is not None:
            checkable.append((index, path))
    return checkable


def check_against_native(
    records: list[dict[str, Any]], transcript_key: str, transcripts: dict[str, str] | None = None
) -> OracleReport:
    """The acceptance gate: every snapshot that already carries a natively
    written tokens_by_model, reconstructed and compared against it.

    The map the hook wrote at the time, from the live transcript, is the
    known-good answer for that snapshot. `problems` carries one message per
    snapshot that fails diff_against_native - an over-count of any size, a
    shortfall past ORACLE_TOLERANCE, or a model bucket that exists on only
    one side - and a non-empty list aborts the whole run. `exact` and
    `tolerated` split the passes, so the tolerance can be watched rather
    than merely trusted.
    """
    id_key = ID_KEY_FOR_TRANSCRIPT_KEY[transcript_key]
    timelines: dict[str, Timeline] = {}
    problems: list[str] = []
    exact = 0
    tolerated = 0
    for index, path in checkable_native_rows(records, transcript_key, transcripts):
        record = records[index]
        timeline = timelines.get(path)
        if timeline is None:
            timeline = timelines[path] = scan_transcript(path)
        rebuilt = model_totals_as_of(timeline, record["timestamp_end"])
        complaints, within_tolerance = diff_against_native(record[FIELD], rebuilt)
        if complaints:
            header = (
                f"line {index + 1}: {id_key}={record.get(id_key)!r} as of {record['timestamp_end']}"
            )
            problems.append("\n".join([header, *(f"    {line}" for line in complaints)]))
        elif within_tolerance:
            tolerated += 1
        else:
            exact += 1
    return OracleReport(problems, exact, tolerated)


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

    # Indexed before the gate runs, not after: the oracle needs it to follow
    # a transcript that has moved between project slugs, and a row it cannot
    # reach is a row it cannot check.
    transcripts = index_transcripts(args.projects_dir)
    print(f"indexed {len(transcripts)} session transcripts under {args.projects_dir}")

    # The gate runs before the plan is computed: if the reconstruction cannot
    # reproduce the snapshots whose answer is already known, nothing it has
    # to say about the rest is worth printing.
    checkable = checkable_native_rows(records, transcript_key, transcripts)
    native = sum(1 for record in records if FIELD in record)
    print(
        f"oracle: {len(checkable)} of {native} natively written snapshots "
        f"still have their transcript"
    )
    report = check_against_native(records, transcript_key, transcripts)
    print(
        f"oracle: {report.exact} exact, {report.tolerated} within the "
        f"{ORACLE_TOLERANCE:.0%} downward tolerance, "
        f"{len(report.problems)} disagreements"
    )
    if report.problems:
        print(f"\nORACLE FAILED ({len(report.problems)} disagreements):", file=sys.stderr)
        for problem in report.problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "\nnothing was written and nothing will be: a reconstruction that "
            "cannot reproduce a snapshot whose answer is known has no claim "
            "on the ones where it is not",
            file=sys.stderr,
        )
        return 1

    plan, stats = plan_backfill(records, transcripts, transcript_key)
    print_plan_summary(records, plan, stats, id_key)
    print("\nREAD-ONLY - this script has no write path; --apply is a later change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
