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
can only be a SHORTFALL, never an invention. As this gate itself reported on
2026-09-16: 126 of 128 sessions exact, worst residue 0.792% downward, and
93 of 93 subagents exact. So the gate accepts equality or a
shortfall of at most 1%, and refuses an over-count of any size at all. See
diff_against_native.

Why the oracle is not the only gate
----------------------------------
The oracle can only sample rows that already carry a native map, and every
one of those was written on deploy day - long after the transcript-entry
dedup went live, so their stored totals are clean. The rows this run would
WRITE are historical, and many of them store PRE-DEDUP, duplicate-inflated
totals while the reconstruction dedupes. The oracle is therefore disjoint in
AGE from the defect and stays green while the run writes a map that
contradicts the four totals beside it - the one outcome this script exists
to prevent. So a second gate, reconcile_against_totals, runs over EVERY
planned row: each of the four fields, the map's sum against that same row's
own total, under the oracle's one-directional bound. It needs no transcript
and no index; the row carries both halves. A unit any of whose planned rows
fails is dropped from the plan and counted - not aborted on, because those
rows are inflated in the stored data, not wrong in the reconstruction. The
same refusal covers a unit whose reconstructed per-model series would go
DOWN between snapshots, which the day-by-day differencing would floor away
and re-emit as a phantom day.

Why --apply demands a number the operator read for themselves
-------------------------------------------------------------
A bare run is a dry run. Writing needs --apply AND --expect-checked N, and
the run refuses if the oracle verified fewer than N snapshots - or zero of
them, whatever N says. The floor exists because this gate has twice reported
green while quietly measuring less than anyone believed: once over an empty
index, and once reaching 49 of 93 subagent rows while printing a verdict
that read exactly as green as 93 of 93. Both runs would have exited 0. An
oracle that stops reaching its rows looks identical to one that passed, so
the count of rows it actually verified is made an argument the operator must
copy off the dry run they just read, rather than a number the run gets to
shrink on its own. Vacuous checks - empty on both sides - are excluded from
that count on purpose: they agree and prove nothing.

The write itself reuses the machinery scripts/backfill-token-dedup.py
already proved: back up and verify the copy by sha256, re-verify at rename
time that the rows the plan was built from are still the file's prefix,
carry over verbatim anything appended while the run was in flight, pass the
whole result through verify_fields with tokens_by_model declared as the only
permitted addition, and rename into place rather than editing in place.
verify_fields also refuses a row whose key ORDER changed ("key order
changed"), not just an added or missing key - a RECOMPUTE hits this if it
reinserts tokens_by_model at a different position than the run that first
wrote it, since a plain dict merge only preserves position for a key that
was already there.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, NoReturn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core.backfill import (
    TimelineEntry,
    index_subagent_transcripts,
    index_transcripts,
    model_totals_as_of,
    resolve_transcript,
    scan_transcript,
    sha256_bytes,
    sha256_of,
    token_sum,
    verify_fields,
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
Timeline = list[TimelineEntry]

# How far BELOW a natively written value a reconstruction may land and still
# be accepted, per model and per token field. Strictly one-directional: see
# diff_against_native for why an over-count is refused at any magnitude.
ORACLE_TOLERANCE = 0.01

# The two dispositions the second gate adds to the dry run's table. Named
# once so the table, the tests and the refusal all spell them identically.
RECONCILE_REASON = "untouched: a snapshot's map does not reconcile with its own totals"
MONOTONIC_REASON = "untouched: the per-model series would go down between snapshots"


class Refusals(NamedTuple):
    """The units the second gate took back off the plan, by reason.

    Two sets rather than one count, because the two say different things
    about the data: `unreconciled` means the totals stored beside the map are
    inflated (pre-dedup rows the dedup backfill never reached), while
    `nonmonotonic` means the stored series itself resets mid-way. They are
    disjoint - a unit that fails both is reported under the first.
    """

    unreconciled: set[Any]
    nonmonotonic: set[Any]


class OracleReport(NamedTuple):
    """What the acceptance gate found: problems first, because that is what
    aborts the run; the counts exist so the evidence behind a pass can be
    read rather than assumed - a jump in `tolerated`, or a total propped up
    by `vacuous` checks or hollowed out by `unverified` rows, is visible
    instead of being averaged into a green number."""

    problems: list[str]
    exact: int
    tolerated: int
    vacuous: int
    unverified: int


class NativeRows(NamedTuple):
    """Rows carrying a native map, split by what the gate can do with them.

    `checkable` are the ones it will actually reconstruct and compare; the
    other two are the reasons it will not, kept apart because they have
    different causes and different fixes.
    """

    checkable: list[tuple[int, str]]
    undated: list[int]
    unreachable: list[int]


def index_for_file(file_name: str, projects_dir: str) -> tuple[dict[str, str], list[str]]:
    """The by-id transcript index for one metrics file, and its ambiguities.

    The two files key on different ids and store their transcripts at
    different depths, so they need different indexers. This is the one place
    that knows which - everything downstream just takes a dict.
    """
    if file_name == "subagents":
        return index_subagent_transcripts(projects_dir)
    return index_transcripts(projects_dir), []


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


def reconcile_against_totals(record: dict[str, Any], rebuilt: ModelMap) -> list[str]:
    """Complaints for one planned row: its map against the four totals that
    are already sitting beside it in that same row.

    This is the second gate, and it exists because the oracle structurally
    cannot be the only one. The oracle can only sample rows that already
    carry a native map, and those were all written on deploy day - after the
    dedup of duplicated transcript entries was live, so their totals are
    clean. The rows this run would WRITE are historical, and a quarter of
    the subagent ones store PRE-DEDUP, duplicate-inflated totals while the
    reconstruction dedupes. So the oracle stays green over healthy rows while
    the run writes a map that contradicts the four numbers next to it, and
    writing a correct map beside totals it disagrees with is the single
    outcome this whole script exists to prevent. The oracle's sample is
    disjoint in AGE from the defect, which no amount of sampling fixes.

    The bound is the oracle's, for the oracle's reason: equal, or short by at
    most ORACLE_TOLERANCE, and never above by any amount. An as-of cut can
    miss entries the totals counted; it can never invent ones they did not.

    Needs no transcript and no index - the row carries both halves.
    """
    complaints: list[str] = []
    for field in TOKEN_FIELDS:
        recorded = record.get(field, 0) or 0
        found = sum(bucket.get(field, 0) or 0 for bucket in rebuilt.values())
        if found == recorded:
            continue
        if found > recorded:
            complaints.append(
                f"{field}: row totals {recorded:,}, map sums to {found:,} - "
                f"ABOVE the row's own total by {found - recorded:,}"
            )
            continue
        # found < recorded, so recorded is at least 1 and the share is well
        # defined without a zero guard.
        shortfall = recorded - found
        share = shortfall / recorded
        if share > ORACLE_TOLERANCE:
            complaints.append(
                f"{field}: row totals {recorded:,}, map sums to {found:,} - "
                f"below by {shortfall:,} ({share:.3%}), past the "
                f"{ORACLE_TOLERANCE:.0%} floor"
            )
    return complaints


def series_goes_down(
    records: list[dict[str, Any]], indexes: list[int], maps: dict[int, ModelMap]
) -> bool:
    """Whether a unit's planned per-model series ever decreases.

    expand_records_to_daily_deltas orders a unit's snapshots by
    duration_seconds and differences the per-model map exactly as it
    differences the four totals, flooring each field at zero - so a series
    that goes DOWN silently drops usage, and the drop reappears as a phantom
    day the next time the series climbs back. scripts/backfill-token-dedup.py
    refuses on precisely that for the four totals; this is the same refusal
    for the field this script adds.

    find_nonmonotonic_sessions is NOT reused: it groups on a hardcoded
    `session_id`, so it cannot see subagent units at all, and it compares the
    four token totals rather than the per-model buckets - the series this
    script writes and the only one it can regress.

    A model absent from a later snapshot is not a decrease: the expander
    keeps the last value it saw for that model rather than differencing
    against zero, so an omission produces no delta at all. Only a value that
    comes back LOWER than the last one seen for the same model can be
    floored away.
    """
    seen: dict[str, dict[str, int]] = {}
    for index in sorted(indexes, key=lambda i: records[i].get("duration_seconds") or 0):
        for model, bucket in maps[index].items():
            previous = seen.setdefault(model, dict.fromkeys(TOKEN_FIELDS, 0))
            for field in TOKEN_FIELDS:
                current = bucket.get(field, 0) or 0
                if current < previous[field]:
                    return True
                previous[field] = current
    return False


def refuse_unwritable_units(
    records: list[dict[str, Any]],
    plan: dict[int, ModelMap],
    stats: collections.Counter[str],
    id_key: str,
) -> tuple[dict[int, ModelMap], Refusals]:
    """The plan with every unit that cannot be written honestly taken back
    off it, and the reasons, counted.

    A refused unit is SKIPPED, never aborted on: unlike an oracle
    disagreement, these rows are not evidence that the reconstruction is
    wrong. They are evidence that the totals STORED beside them are inflated
    - pre-dedup sums the dedup backfill never reached - and the honest
    outcome is to decline those units and write the rest, not to abandon
    thousands of good rows over history nothing here can repair.

    All-or-nothing per unit, the same rule plan_backfill already applies: a
    unit written half way leaves a seam that the day-by-day differencing
    reads as a burst of usage.

    `stats` is updated in place so the dry run's disposition table keeps
    summing to the row count - a refused unit's planned rows move from
    "planned" to their reason, while its native rows stay counted where they
    already were, because they are untouched either way.
    """
    grouped = rows_by_unit(records, id_key)
    unreconciled: set[Any] = set()
    nonmonotonic: set[Any] = set()
    for unit_id in {records[index].get(id_key) for index in plan}:
        indexes = grouped[unit_id]
        maps = {index: plan.get(index) or records[index].get(FIELD) or {} for index in indexes}
        if any(
            reconcile_against_totals(records[index], maps[index])
            for index in indexes
            if index in plan
        ):
            # First reason wins, so the two sets stay disjoint and the counts
            # printed from them add up to the units actually refused.
            unreconciled.add(unit_id)
        elif series_goes_down(records, indexes, maps):
            nonmonotonic.add(unit_id)

    dropped: collections.Counter[str] = collections.Counter()
    kept: dict[int, ModelMap] = {}
    for index, model_map in plan.items():
        unit_id = records[index].get(id_key)
        if unit_id in unreconciled:
            dropped[RECONCILE_REASON] += 1
        elif unit_id in nonmonotonic:
            dropped[MONOTONIC_REASON] += 1
        else:
            kept[index] = model_map
    for reason, count in dropped.items():
        stats[reason] += count
        stats["planned"] -= count
    if dropped and not stats["planned"]:
        # Same rule as plan_backfill's: a disposition table should list only
        # categories that happened, never a reason with a count of zero.
        del stats["planned"]
    return kept, Refusals(unreconciled, nonmonotonic)


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
        # The field loop below reads TOKEN_FIELDS and nothing else, so a
        # bucket carrying a fifth key would compare clean while differing.
        # The old whole-dict comparison caught that for free; this restores
        # it explicitly rather than trusting the two shapes to stay equal.
        if set(native[model]) != set(rebuilt[model]):
            complaints.append(
                f"{model}: bucket fields differ - native {sorted(native[model])}, "
                f"rebuilt {sorted(rebuilt[model])}"
            )
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


def classify_native_rows(
    records: list[dict[str, Any]], transcript_key: str, transcripts: dict[str, str] | None = None
) -> NativeRows:
    """Split the rows that already carry a native map by what can be done
    with them, keeping the reasons apart.

    A row is checkable when it records when it was taken AND its transcript
    can still be found - through the recorded path, or through the by-id
    index when that path has gone stale. The other two outcomes are reported
    as themselves, never merged: a row with no `timestamp_end` has no as-of
    to reconstruct at, and a row whose transcript cannot be found anywhere
    is unverified. Both are rows the gate did NOT check, and a gate that
    folds them into its green count is claiming evidence it does not have.

    `transcripts` is the by-id index, and passing it is not optional in
    practice: transcripts migrate between project slugs while their sessions
    are alive (measured 2026-09-16 - the snapshots that first exposed the
    tolerance question moved out of a worktree slug within the hour, and 44
    subagent transcripts did the same). Without the index those rows drop out
    of the oracle silently, and a gate that quietly stops checking the hard
    cases reports a pass it has not earned.
    """
    id_key = ID_KEY_FOR_TRANSCRIPT_KEY[transcript_key]
    checkable: list[tuple[int, str]] = []
    undated: list[int] = []
    unreachable: list[int] = []
    for index, record in enumerate(records):
        if FIELD not in record:
            continue
        if not record.get("timestamp_end"):
            undated.append(index)
            continue
        path = resolve_transcript(record, transcripts or {}, transcript_key, record.get(id_key))
        if path is None:
            unreachable.append(index)
        else:
            checkable.append((index, path))
    return NativeRows(checkable, undated, unreachable)


def checkable_native_rows(
    records: list[dict[str, Any]], transcript_key: str, transcripts: dict[str, str] | None = None
) -> list[tuple[int, str]]:
    """(row index, transcript path) for every row the oracle can check."""
    return classify_native_rows(records, transcript_key, transcripts).checkable


def check_against_native(
    records: list[dict[str, Any]], transcript_key: str, transcripts: dict[str, str] | None = None
) -> OracleReport:
    """The acceptance gate: every snapshot that already carries a natively
    written tokens_by_model, reconstructed and compared against it.

    The map the hook wrote at the time, from the live transcript, is the
    known-good answer for that snapshot. `problems` carries one message per
    snapshot that fails diff_against_native - an over-count of any size, a
    shortfall past ORACLE_TOLERANCE, or a model bucket that exists on only
    one side - and a non-empty list aborts the whole run.

    The remaining counts exist so the evidence can be read rather than
    assumed. `exact` and `tolerated` split the passes, so the tolerance is
    watched rather than merely trusted. `vacuous` holds the snapshots that
    are empty on BOTH sides: they agree, but they prove nothing, and
    counting them as evidence inflates the total with checks that could not
    have failed. `unverified` holds the native rows the gate never reached
    at all - unreconstructable or unfindable - which belong in the report,
    not in silence.
    """
    id_key = ID_KEY_FOR_TRANSCRIPT_KEY[transcript_key]
    timelines: dict[str, Timeline] = {}
    problems: list[str] = []
    exact = 0
    tolerated = 0
    vacuous = 0
    native_rows = classify_native_rows(records, transcript_key, transcripts)
    for index, path in native_rows.checkable:
        record = records[index]
        timeline = timelines.get(path)
        if timeline is None:
            timeline = timelines[path] = scan_transcript(path)
        rebuilt = model_totals_as_of(timeline, record["timestamp_end"])
        if not record[FIELD] and not rebuilt:
            vacuous += 1
            continue
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
    return OracleReport(
        problems,
        exact,
        tolerated,
        vacuous,
        len(native_rows.undated) + len(native_rows.unreachable),
    )


def refuse(message: str) -> NoReturn:
    """Abort the write with a reason on stderr and a non-zero exit.

    Every guard below refuses the same way, so no caller can mistake a
    refusal for a return value it forgot to check.
    """
    print(message, file=sys.stderr)
    raise SystemExit(1)


def record_of(line: bytes) -> dict[str, Any] | None:
    """One file line as the record it holds, or None if it is not one.

    None is a comparison value, not an error path: an unparseable or blank
    line inside the prefix means the file is not what the plan was built
    from, and the prefix check below refuses on that the same way it refuses
    a line whose contents changed. load_records SKIPS such lines, so a blank
    line in the prefix would shift every row index the plan holds by one -
    exactly the misalignment that must never reach a write.
    """
    try:
        record: dict[str, Any] = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return record


def tail_after_prefix(target: Path, records: list[dict[str, Any]]) -> bytes:
    """Everything the live file holds beyond the rows the plan was built
    from - after refusing if those rows are no longer its prefix.

    Read as LATE as possible, for the same reason the dedup backfill reads
    it late: the metrics file is append-only and live, every running
    session's Stop hook appends to it, and it grew by more than 200 lines
    during the session that specified this. Anything past the prefix is
    returned as raw bytes and copied through untouched, including a
    half-written final line a hook is in the middle of appending.

    The check is by RECORD, not by bytes: the records came from
    load_records, which is free to have parsed whitespace differently than
    this reads it back. What must hold is that row index i of the plan still
    names the same record it named when the plan was computed.
    """
    current = target.read_bytes()
    usable = current.rfind(b"\n") + 1
    lines = current[:usable].split(b"\n")[:-1]
    prefix = lines[: len(records)]
    if [record_of(line) for line in prefix] != records:
        refuse(
            f"refusing to write: the file changed under us - its first {len(records)} "
            "line(s) are no longer the records this plan was built from, so every "
            "row index in the plan may now name a different record"
        )
    return current[sum(len(line) + 1 for line in prefix) :]


def apply_backfill(
    path: str,
    plan: dict[int, ModelMap],
    records: list[dict[str, Any]],
    backup_dir: str | None = None,
) -> None:
    """Write the planned maps into the metrics file, or write nothing at all.

    The one irreversible step in this backfill, so it mirrors the order
    scripts/backfill-token-dedup.py proved on this same file:

    1. back up, and verify the copy by sha256 - an unverified backup is not
       a backup;
    2. add the planned key and nothing else, then put the whole result
       through verify_fields with tokens_by_model declared as the only
       permitted addition, which still rejects an undeclared key, a
       reordering, and any change to a value that was already there;
    3. write the body to a temporary file beside the target;
    4. re-read the live file as late as possible, refuse unless the planned
       rows are still its prefix, and append whatever arrived after them
       verbatim;
    5. rename into place, never edit in place.

    Any refusal leaves the target byte-for-byte as it was, and removes the
    temporary file on the way out.
    """
    target = Path(path)
    out_of_range = sorted(index for index in plan if not 0 <= index < len(records))
    if out_of_range:
        refuse(
            f"refusing to write: the plan names row(s) {out_of_range[:5]}, which are "
            f"not rows of the {len(records)} records it was built from"
        )

    backups = Path(backup_dir) if backup_dir else target.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = backups / f"{target.name}.tokens-by-model-{stamp}.bak"
    shutil.copy2(str(target), str(backup))
    source_sha = sha256_of(str(target))
    backup_sha = sha256_of(str(backup))
    if source_sha != backup_sha:
        # Can legitimately differ if a hook appended between the copy and the
        # re-read; either way an unverified backup is not a backup.
        refuse(f"BACKUP VERIFICATION FAILED\n  source {source_sha}\n  backup {backup_sha}")
    print(f"\nbackup verified: {backup}\n  sha256 {backup_sha}")

    corrected = [
        {**record, FIELD: plan[index]} if index in plan else record
        for index, record in enumerate(records)
    ]
    problems = verify_fields(records, corrected, allow_new_keys=frozenset({FIELD}))
    if problems:
        detail = "\n".join(f"  {problem}" for problem in problems[:20])
        refuse(f"FIELD VERIFICATION FAILED ({len(problems)} problems):\n{detail}")
    print(f"field-by-field verification: {FIELD} added to {len(plan)} row(s), nothing else changed")

    mode = stat.S_IMODE(target.stat().st_mode)
    body = b"".join(json.dumps(record).encode("utf-8") + b"\n" for record in corrected)
    handle, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".new"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(body)
            tail = tail_after_prefix(target, records)
            f.write(tail)
            f.flush()
            os.fsync(f.fileno())
        temporary.chmod(mode)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    written = target.read_bytes()
    # The bytes on disk, against the bytes intended - not a count of them.
    # Canary 2 measured why: with the tail carry-over neutered, the run
    # printed "1 line(s) (1 rewritten + 1 appended while running)" and exited
    # 0, a contradiction nobody was comparing. startswith rather than
    # equality because a live Stop hook may legitimately append in the
    # microseconds after the rename; nothing else can make this false.
    if not written.startswith(body + tail):
        refuse(
            "POST-WRITE VERIFICATION FAILED: the file on disk is not what was "
            f"written. The backup taken at the start of this run is {backup}, "
            "verified by sha256 before anything was touched"
        )
    appended = tail.count(b"\n")
    total_lines = written.count(b"\n")
    print(
        f"wrote {target}: {total_lines} line(s) "
        f"({len(records)} rewritten + {appended} appended while running)"
    )
    print(f"  mode {oct(stat.S_IMODE(target.stat().st_mode))}")
    print(f"  sha256 {sha256_bytes(written)}")


def print_plan_summary(
    records: list[dict[str, Any]],
    plan: dict[int, ModelMap],
    stats: collections.Counter[str],
    id_key: str,
    refusals: Refusals,
) -> None:
    """The dry run's report: what would be written, and what would not.

    The volume figures compare each unit's LARGEST cumulative snapshot, which
    is that unit's whole consumption - so "covered" is the share of all
    tokens ever logged in this file that the backfill could attribute to a
    model, which is the number worth knowing, not the share of rows.

    "Covered" is read off the plan AFTER refuse_unwritable_units has narrowed
    it, so it counts only what would actually be written. Counting the raw
    plan overstated it by 981,659,264 tokens on the subagent file and
    4,134,787,999 on the session file (measured 2026-09-17, the two dry runs
    either side of this gate) - volume attributed to units whose map does not
    reach their own totals and which are therefore declined.
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

    refused = refusals.unreconciled | refusals.nonmonotonic
    if refused:
        # Printed apart from the disposition table, and apart from each
        # other: these are units the reconstruction got RIGHT and the stored
        # data cannot host, which is a different fact about the file than a
        # missing transcript, and the volume is how much of the file it
        # costs to decline them.
        declined = sum(total for unit_id, total in totals.items() if unit_id in refused)
        print(
            f"\nUnits declined by the reconciliation gate: {len(refused)} "
            f"({len(refusals.unreconciled)} whose map does not reconcile with their "
            f"own totals, {len(refusals.nonmonotonic)} whose per-model series goes "
            f"down), holding {declined:,} token(s)"
        )

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
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="where the pre-write backup goes (default: a backups/ beside the metrics file)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the reconstructed maps; without it this is a dry run",
    )
    parser.add_argument(
        "--expect-checked",
        type=int,
        default=None,
        help=(
            "how many natively written snapshots the oracle is expected to verify, "
            "read off the dry run; --apply refuses below it. Required with --apply"
        ),
    )
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
    transcripts, ambiguous = index_for_file(args.file, args.projects_dir)
    print(f"indexed {len(transcripts)} {args.file[:-1]} transcripts under {args.projects_dir}")
    if ambiguous:
        print(
            f"  {len(ambiguous)} id(s) found under more than one project slug and "
            f"left unindexed: {ambiguous[:5]}"
        )

    # The gate runs before the plan is computed: if the reconstruction cannot
    # reproduce the snapshots whose answer is already known, nothing it has
    # to say about the rest is worth printing.
    #
    # The header states what was MEASURED, not a cause it did not check. An
    # earlier wording ("still have their transcript") asserted the reason 44
    # subagent rows were skipped, and was false for every one of them - the
    # files were on disk and the index was the thing that could not reach
    # them.
    native_rows = classify_native_rows(records, transcript_key, transcripts)
    native = len(native_rows.checkable) + len(native_rows.undated) + len(native_rows.unreachable)
    print(
        f"oracle: {native} natively written snapshots - "
        f"{len(native_rows.checkable)} checked, "
        f"{len(native_rows.unreachable)} with no transcript found anywhere, "
        f"{len(native_rows.undated)} with no timestamp_end"
    )
    report = check_against_native(records, transcript_key, transcripts)
    print(
        f"oracle: {report.exact} exact, {report.tolerated} within the "
        f"{ORACLE_TOLERANCE:.0%} downward tolerance, "
        f"{report.vacuous} vacuous (empty on both sides), "
        f"{len(report.problems)} disagreements, "
        f"{report.unverified} unverified"
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
    # The second gate, over every row the plan would write rather than over
    # the oracle's sample. It narrows the plan instead of aborting: see
    # refuse_unwritable_units.
    plan, refusals = refuse_unwritable_units(records, plan, stats, id_key)
    print_plan_summary(records, plan, stats, id_key, refusals)

    # The evidence this run actually collected: snapshots reconstructed and
    # compared against a native map that had something in it. Vacuous checks
    # are left out on purpose - they agree and prove nothing, and a total
    # propped up by them is the shape of a gate that has stopped looking.
    checked = report.exact + report.tolerated
    if not args.apply:
        # --file is part of the printed command, not left to the default.
        # Without it, a subagents dry run hands the operator a line that runs
        # against sessions.jsonl - and the hazard is asymmetric, so it
        # survives casual reading: the subagent file's smaller count clears
        # the session file's floor and writes, while the reverse refuses.
        print(
            f"\nDRY RUN - nothing written. To write: --file {args.file} --apply "
            f"--expect-checked {checked} (and read the oracle line above before you do)."
        )
        return 0

    if args.expect_checked is None:
        print(
            "--apply requires --expect-checked N: the number of natively written "
            "snapshots the oracle verified in the dry run you just read (exact plus "
            "within-tolerance; vacuous checks do not count). It exists so a human "
            "looks at how much evidence this run has, rather than letting a "
            "quietly-shrinking oracle wave a write through.",
            file=sys.stderr,
        )
        return 1
    if checked == 0:
        print(
            "refusing to write: the oracle verified 0 snapshots, so this run has no "
            "evidence at all that the reconstruction is right. A gate that has "
            "stopped reaching its rows prints the same green as one that passed.",
            file=sys.stderr,
        )
        return 1
    if checked < args.expect_checked:
        print(
            f"refusing to write: the oracle verified {checked} snapshot(s), fewer than "
            f"the {args.expect_checked} this run was told to expect. Either it has "
            "stopped reaching rows it used to reach, or --expect-checked is stale - "
            "both are worth stopping for.",
            file=sys.stderr,
        )
        return 1

    if not plan:
        print("\nnothing to write; leaving the file untouched")
        return 0
    apply_backfill(str(path), plan, records, args.backup_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
