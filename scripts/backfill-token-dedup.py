#!/usr/bin/env python3
"""Recompute the token fields of historical sessions.jsonl rows that were
written by the pre-dedup logging hook.

Background
----------
Until the dedup fix, hooks/metrics-log.py summed EVERY assistant entry in a
transcript. Resuming or compacting a session replays earlier turns back into
the same transcript file, so one message id can appear many times and every
token count logged that way is inflated (measured ~1.89x overall).

Why this rewrites every snapshot, not one row per session
---------------------------------------------------------
The Stop hook appends a fresh CUMULATIVE snapshot every turn, so a session
owns a GROWING SERIES of rows. expand_records_to_daily_deltas() rebuilds
per-day usage by differencing consecutive snapshots - that is what spreads a
long session's tokens over the days it really ran. Recomputing each row from
the FULL transcript would give every row of a session the same final total,
every delta but the first would collapse to zero, and the trend chart would
pile each session onto a single day. So each snapshot is recomputed AS OF ITS
OWN timestamp_end: only transcript entries at or before that moment count,
each message once at its per-field PEAK, exactly as the fixed
parse_transcript counts it (see transcript_ingest.usage_increment).

"At or before that moment" is the cut the hook really made: the file
prefix whose last timestamped line carries timestamp_end, since the hook
reads the file in order and Claude Code does not write it in timestamp
order (see metrics_core.backfill.scan_cut_points). A prefix is used when
its reconstruction reproduces the row's stored tokens; otherwise every
entry stamped at or before timestamp_end is, as before 2026-09-29.

Why the row selection is evidence-driven, not date-driven
---------------------------------------------------------
Two broken formulas are in the file. Before 2026-08-11 the hook summed
every entry (RAW, inflated by replays); from then until 2026-09-23 it kept
the FIRST entry of each message id, which handles replays but undercounts
every streamed message, whose later entries carry the larger output_tokens.
For every row three values are reconstructed from the transcript: the PEAK
as-of sum (what a correct hook would have written) and the two broken ones.
A row is only rewritten when its stored value reproduces RAW or FIRST - that
is positive proof both of which hook wrote it and that the reconstruction
models this row correctly - and the formula fixes the only direction it may
move: a raw sum down, a first sum up. A row already equal to the PEAK is
left alone, which is what makes a second run a no-op.

Why correction is all-or-nothing PER SESSION
--------------------------------------------
Correcting some snapshots of a session and leaving others re-inflates the
delta at the seam: the jump from the last corrected (halved) snapshot to the
first untouched (inflated) one is charged entirely to the day the seam falls
on. Measured on real data, a per-row cutoff INVENTED 1.9 billion tokens on
2026-08-13 - a fake spike is worse than the uniform overcount it replaced. So
a session is corrected only when every one of its rows can be handled; a
session with even one row on/after the cutoff is left entirely alone.

Safety
------
- backs up first and verifies the copy by sha256, refusing to proceed if it
  does not match;
- writes a new file beside the old one and renames into place only after
  validation, never edits in place;
- the metrics file is APPEND-ONLY and live (the Stop hook of any running
  session appends to it while this runs), so the snapshot prefix is verified
  unchanged at rename time and anything appended meanwhile is carried over
  verbatim - no line is ever lost;
- only the four token fields may change, each row only in its formula's
  direction; tokens_by_model on a corrected row is rebuilt by the same rule
  and must sum to the new totals; every other field is compared
  byte-for-byte before the rename.

Limits
------
- a token run rewrites only the four token fields (and tokens_by_model).
  Two other fields have their own passes, run only when named with --fields
  and never implied by a token run:

  --fields tool_calls        rebuilds tool_call_count by the current rule
                             (distinct tool_use block ids, as of each row's
                             timestamp_end). Every hook before 2026-09-23
                             undercounted it; a row is rewritten only when
                             it exactly matches the every-entry or the
                             first-entry count, and may only fall or rise
                             accordingly - or, at a file cut, the time-cut
                             count this pass itself wrote before
                             2026-09-29, which moves to the file cut's.
  --fields incomplete_usage  adds incomplete_usage_messages (see
                             transcript_ingest.count_incomplete) to rows
                             written before the hooks recorded it, only
                             where it is positive, and only for units whose
                             every row's tokens the transcript reproduces.

  Both use the same backup, atomic write, live-append carry-over and
  field-by-field verification as the token run - nothing but the named
  field may change - and record their own entry under "fields" in the
  state file, leaving the token entries the dashboards read alone.

subagents.jsonl
---------------
The subagent hook had the same bug, so subagents.jsonl needs the same
correction, run with --kind subagents. Everything above holds with one
change of UNIT: a subagent row carries its own agent_id AND its parent's
session_id, so its series is grouped by agent_id - grouping on session_id
merges every subagent one session dispatched, the 2026-08-06 contamination
(1012 of 1036 agent_ids mismatched). Its transcript is found the way
scripts/backfill-tokens-by-model.py finds it: the row's recorded
agent_transcript_path, relocated through the by-agent-id index when stale.
The run refuses when --kind contradicts the rows (agent_id present or
absent), and records its state under a "subagents" key of the same state
file, leaving the sessions entry the dashboards read untouched.
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
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core.backfill import (
    BACKFILL_STATE_FILE,
    DEFAULT_CUTOFF,
    FIRST,
    RAW,
    TIME_CUT,
    UNIT_ID_KEYS,
    FieldPlan,
    apply_field_plan,
    apply_plan,
    carry_late_appends,
    daily_totals,
    file_kind_problem,
    find_nonmonotonic_sessions,
    find_subagent_transcripts,
    index_subagent_transcripts,
    index_transcripts,
    plan_corrections_by_formula,
    plan_incomplete_usage,
    plan_tool_call_corrections,
    row_identities,
    sha256_bytes,
    sha256_of,
    subagent_transcripts,
    token_sum,
    verify_fields,
    write_backfill_state,
    write_field_state,
)
from metrics_core.transcript_ingest import TOKEN_FIELDS

METRICS_FILES = {
    kind: str(Path(f"~/.claude/metrics/{kind}.jsonl").expanduser()) for kind in UNIT_ID_KEYS
}
BACKUP_DIRECTORY = str(Path("~/.claude/metrics/backups").expanduser())
PROJECTS_DIRECTORY = str(Path("~/.claude/projects").expanduser())

# --fields value -> the record key its pass writes.
FIELD_KEYS = {"tool_calls": "tool_call_count", "incomplete_usage": "incomplete_usage_messages"}
FORMULA_NAMES = {RAW: "every-entry", FIRST: "first-entry", TIME_CUT: "earlier time-cut"}


def print_daily_comparison(
    before: list[dict[str, Any]], after: list[dict[str, Any]], days: int | None
) -> None:
    before_map = dict(daily_totals(before, days))
    after_map = dict(daily_totals(after, days))
    print(f"\nPer-day token totals via expand_records_to_daily_deltas (last {days} days)")
    print(f"  {'day':<12}{'before':>18}{'after':>18}{'factor':>10}")
    for day in sorted(set(before_map) | set(after_map)):
        before_count = before_map.get(day, 0)
        after_count = after_map.get(day, 0)
        factor = (
            f"{before_count / after_count:.3f}x"
            if after_count
            else ("COLLAPSED" if before_count else "-")
        )
        print(f"  {day:<12}{before_count:>18,}{after_count:>18,}{factor:>10}")
    collapsed = [day for day in before_map if before_map[day] > 0 and after_map.get(day, 0) == 0]
    print(f"  days that collapsed to zero: {len(collapsed)} {collapsed if collapsed else ''}")


def print_field_deltas(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    """Net change per token field, so a correction that raises output while
    lowering cache is visible as both, not netted into one grand total."""
    print("Net change per field:")
    for field in TOKEN_FIELDS:
        delta = sum(
            (new.get(field) or 0) - (old.get(field) or 0)
            for old, new in zip(before, after, strict=True)
        )
        print(f"  {field:<24}{delta:>+18,}")


def unreconciled_maps(records: list[dict[str, Any]], plan: dict[int, list[int]]) -> list[str]:
    """Problems for any corrected row whose tokens_by_model does not sum,
    field by field, to that row's own four totals."""
    problems = []
    for index in sorted(plan):
        model_map = records[index].get("tokens_by_model")
        if model_map is None:
            continue
        for field in TOKEN_FIELDS:
            mapped = sum(bucket.get(field, 0) for bucket in model_map.values())
            if mapped != records[index].get(field, 0):
                problems.append(
                    f"line {index + 1}: tokens_by_model {field} sums to {mapped}, "
                    f"the row says {records[index].get(field, 0)}"
                )
    return problems


def print_summary(
    stats: collections.Counter[str],
    factors: list[float],
    before_total: int,
    after_total: int,
    changed_lines: int,
    total_lines: int,
) -> None:
    print("\nRow disposition")
    for reason, count in sorted(stats.items(), key=lambda item: -item[1]):
        print(f"  {count:>6}  {reason}")
    print(f"\nLines: {total_lines} read, {changed_lines} would change")
    if factors:
        factors = sorted(factors)

        def pct(percentile: float) -> float:
            return factors[min(len(factors) - 1, int(len(factors) * percentile))]

        print("Correction factor (stored / recomputed) over corrected rows:")
        print(
            f"  min {factors[0]:.3f}x  p25 {pct(0.25):.3f}x  median {pct(0.5):.3f}x  "
            f"p75 {pct(0.75):.3f}x  max {factors[-1]:.3f}x"
        )
        buckets: collections.Counter[str] = collections.Counter()
        for factor in factors:
            if factor < 0.999:
                buckets["<1.00x (undercount raised)"] += 1
            elif factor < 1.001:
                buckets["1.00x (no duplicates)"] += 1
            elif factor < 1.5:
                buckets["1.00-1.50x"] += 1
            elif factor < 1.95:
                buckets["1.50-1.95x"] += 1
            elif factor < 2.05:
                buckets["~2.00x"] += 1
            else:
                buckets[">2.05x"] += 1
        for bucket, count in buckets.most_common():
            print(f"    {count:>6}  {bucket}")
    print(f"\nGrand total tokens: before {before_total:,}  after {after_total:,}")
    if after_total:
        print(
            f"  overall inflation removed: {before_total / after_total:.3f}x "
            f"({before_total - after_total:,} tokens)"
        )


def _valid_cutoff(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"--cutoff must be YYYY-MM-DD, got {value!r}") from error
    # strptime alone is not enough: it accepts non-zero-padded values like
    # "2026-8-21" as valid dates, which is exactly the malformed shape that
    # would sort wrong lexicographically against timestamp_end strings -
    # the failure mode this validator exists to catch. Round-tripping back
    # through the canonical format rejects anything that isn't already
    # zero-padded YYYY-MM-DD, even though it parsed.
    if parsed.strftime("%Y-%m-%d") != value:
        raise argparse.ArgumentTypeError(f"--cutoff must be YYYY-MM-DD, got {value!r}")
    return value


def write_atomically(
    path: str,
    snapshot_prefix: bytes,
    new_lines: list[bytes],
    mode: int,
    on_replaced: Callable[[], None] | None = None,
) -> int:
    """Rename a fully written file into place, carrying over live appends.

    The metrics file is append-only and written by every running session, so
    between reading it and replacing it new rows may have arrived. The body
    below is written first since it can be slow for a large correction; the
    live file's tail is read again right before the rename - as late as
    possible - checked against the snapshot prefix, and appended verbatim.
    This keeps the window in which a concurrent append could be missed to
    that final read-verify-append-rename sequence, not the whole write.
    """
    target = Path(path)
    directory = target.parent
    handle, temporary_name = tempfile.mkstemp(
        dir=directory, prefix=f".{target.name}.", suffix=".new"
    )
    temporary = Path(temporary_name)
    tail = b""
    try:
        with os.fdopen(handle, "wb") as temporary_handle:
            for line in new_lines:
                temporary_handle.write(line)
                temporary_handle.write(b"\n")
            temporary_handle.flush()
            os.fsync(temporary_handle.fileno())

        # Read through a handle that is kept open across the rename, so what
        # arrives on the old inode after this read can still be reached.
        with target.open("rb") as old:
            current = old.read()
            if current[: len(snapshot_prefix)] != snapshot_prefix:
                raise RuntimeError(
                    "refusing to write: the file changed under us in a way that is not "
                    "a plain append (its first bytes no longer match the snapshot)"
                )
            tail = current[len(snapshot_prefix) :]
            if tail:
                with temporary.open("ab") as tail_handle:
                    tail_handle.write(tail)
                    tail_handle.flush()
                    os.fsync(tail_handle.fileno())

            temporary.chmod(mode)
            temporary.replace(target)
            if on_replaced is not None:
                # The file is rewritten from here on; see record_state.
                on_replaced()
            stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            late = carry_late_appends(old, target, stamp)
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
    return (len(tail.split(b"\n")) - 1 if tail else 0) + late


def field_problems(
    before: list[dict[str, Any]], after: list[dict[str, Any]], field: str, plan: FieldPlan
) -> list[str]:
    """Everything a field pass changed besides its own field's value, and any
    tool_call_count that moved against the formula it was recognised as.

    verify_fields lets token fields fall (the token run's direction), so the
    four are compared for equality here: a field pass may not move them."""
    key = FIELD_KEYS[field]
    if field == "tool_calls":
        problems = verify_fields(before, after, allow_changed_keys=frozenset({key}))
    else:
        problems = verify_fields(before, after, allow_new_keys=frozenset({key}))
    for index, (old, new) in enumerate(zip(before, after, strict=True)):
        for token_field in TOKEN_FIELDS:
            if old.get(token_field) != new.get(token_field):
                problems.append(
                    f"line {index + 1}: {token_field} changed "
                    f"{old.get(token_field)!r} -> {new.get(token_field)!r}"
                )
    for index, formula in plan.formulas.items():
        old_count = before[index].get(key) or 0
        new_count = after[index].get(key) or 0
        name = FORMULA_NAMES[formula]
        if formula == FIRST and new_count < old_count:
            problems.append(
                f"line {index + 1}: {key} fell {old_count} -> {new_count} but was "
                f"recognised as the {name} count"
            )
        if formula == RAW and new_count > old_count:
            problems.append(
                f"line {index + 1}: {key} rose {old_count} -> {new_count} but was "
                f"recognised as the {name} count"
            )
    return problems


def back_up_and_write(
    path: str,
    snapshot: bytes,
    line_count: int,
    corrected: list[dict[str, Any]],
    backup_directory: str,
    record_state: Callable[[], None],
) -> int:
    """Back up the live file (verified by sha256), write `corrected` over it
    atomically with live appends carried over, and check the line count.
    `record_state` runs the moment the new file is in place (see main's
    record_state for why then). Returns the exit code."""
    metrics_path = Path(path)
    backups_path = Path(backup_directory)
    backups_path.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = backups_path / f"{metrics_path.name}.backfill-{stamp}.bak"
    shutil.copy2(path, backup)
    source_sha = sha256_of(path)
    backup_sha = sha256_of(str(backup))
    if source_sha != backup_sha:
        # Can legitimately differ if a hook appended between the copy and the
        # re-read; either way an unverified backup is not a backup.
        print(
            f"BACKUP VERIFICATION FAILED\n  source {source_sha}\n  backup {backup_sha}",
            file=sys.stderr,
        )
        return 1
    print(f"\nbackup verified: {backup}\n  sha256 {backup_sha}")

    mode = stat.S_IMODE(metrics_path.stat().st_mode)
    new_lines = [json.dumps(record).encode("utf-8") for record in corrected]
    appended = write_atomically(path, snapshot, new_lines, mode, on_replaced=record_state)

    written = metrics_path.read_bytes()
    written_count = written.count(b"\n")
    expected = line_count + appended
    print(
        f"wrote {path}: {written_count} lines "
        f"({line_count} rewritten + {appended} appended while running)"
    )
    if written_count != expected:
        print(f"LINE COUNT MISMATCH: expected {expected}, got {written_count}", file=sys.stderr)
        return 1
    print(f"  mode {oct(stat.S_IMODE(metrics_path.stat().st_mode))}")
    print(f"  sha256 {sha256_bytes(written)}")
    return 0


def run_field_pass(
    arguments: argparse.Namespace,
    path: str,
    snapshot: bytes,
    records: list[dict[str, Any]],
    transcripts: dict[str, str],
    id_key: str,
) -> int:
    """One --fields pass: plan, report, verify, and (with --apply) write."""
    field = arguments.fields
    key = FIELD_KEYS[field]
    planner = plan_tool_call_corrections if field == "tool_calls" else plan_incomplete_usage
    plan = planner(records, transcripts, arguments.cutoff, id_key)
    corrected = apply_field_plan(records, key, plan.values)

    print("\nRow disposition")
    for reason, count in sorted(plan.stats.items(), key=lambda item: -item[1]):
        print(f"  {count:>6}  {reason}")
    print(f"\nLines: {len(records)} read, {len(plan.values)} would change")
    before_total = sum(record.get(key) or 0 for record in records)
    after_total = sum(record.get(key) or 0 for record in corrected)
    print(f"{key} total: before {before_total:,}  after {after_total:,}")

    problems = field_problems(records, corrected, field, plan)
    if problems:
        print(f"\nFIELD VERIFICATION FAILED ({len(problems)} problems):", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        return 1
    if field == "tool_calls":
        by_formula = collections.Counter(plan.formulas.values())
        print(
            f"\nfield-by-field verification: only {key} changed; "
            f"{by_formula[RAW]} every-entry row(s) only fell, "
            f"{by_formula[FIRST]} first-entry row(s) only rose, "
            f"{by_formula[TIME_CUT]} earlier time-cut row(s) moved to their file cut"
        )
    else:
        print(f"\nfield-by-field verification: only {key} was added, nothing else changed")

    if not arguments.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to write.")
        return 0
    if not plan.values:
        print("\nnothing to correct; leaving the file and the backfill state untouched")
        return 0

    def record_state() -> None:
        write_field_state(
            arguments.kind,
            field,
            arguments.cutoff,
            datetime.now().isoformat(),
            row_identities(records, plan.values, id_key),
            path=arguments.state_file,
        )
        print(f"  backfill state recorded: {arguments.state_file}")

    return back_up_and_write(
        path, snapshot, len(records), corrected, arguments.backup_dir, record_state
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--kind",
        choices=sorted(UNIT_ID_KEYS),
        default="sessions",
        help=(
            "which metrics file this is; decides the unit a snapshot series is grouped "
            "by (session_id or agent_id). The run refuses if the rows contradict it"
        ),
    )
    parser.add_argument(
        "--metrics-file",
        default=None,
        help="default: ~/.claude/metrics/<kind>.jsonl",
    )
    parser.add_argument("--projects-dir", default=PROJECTS_DIRECTORY)
    parser.add_argument("--backup-dir", default=BACKUP_DIRECTORY)
    parser.add_argument(
        "--state-file",
        default=BACKFILL_STATE_FILE,
        help="where to record the cutoff and timestamp of a successful apply",
    )
    parser.add_argument(
        "--cutoff",
        default=DEFAULT_CUTOFF,
        type=_valid_cutoff,
        help="rows whose timestamp_end sorts at or after this are left alone",
    )
    parser.add_argument(
        "--daily-days",
        type=int,
        default=15,
        help="how many days of the per-day comparison table to print",
    )
    parser.add_argument(
        "--fields",
        choices=["tokens", *FIELD_KEYS],
        default="tokens",
        help=(
            "what to recompute: the four token fields (default), tool_call_count "
            "(tool_calls), or incomplete_usage_messages (incomplete_usage). Each is its "
            "own run; a token run never touches the other two"
        ),
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the result; without it this is a dry run"
    )
    arguments = parser.parse_args()

    id_key = UNIT_ID_KEYS[arguments.kind]
    path = arguments.metrics_file or METRICS_FILES[arguments.kind]
    metrics_path = Path(path)
    if not metrics_path.exists():
        print(f"no metrics file at {path}", file=sys.stderr)
        return 1

    data = metrics_path.read_bytes()
    # Ignore a partially written trailing line: a concurrent hook may be
    # mid-append. Everything from there on is treated as live tail.
    usable = data.rfind(b"\n") + 1
    snapshot = data[:usable]
    raw_lines = snapshot.split(b"\n")[:-1]
    print(f"read {len(raw_lines)} lines from {path}")
    print(f"  sha256 {sha256_bytes(data)}")

    records: list[dict[str, Any]] = []
    for number, raw in enumerate(raw_lines, 1):
        try:
            records.append(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print(f"line {number} is not valid JSON, aborting: {error}", file=sys.stderr)
            return 1

    kind_problem = file_kind_problem(records, arguments.kind)
    if kind_problem:
        print(f"refusing: --kind {arguments.kind}, but {kind_problem}", file=sys.stderr)
        return 1

    if arguments.kind == "subagents":
        found = find_subagent_transcripts(arguments.projects_dir)
        index, ambiguous = index_subagent_transcripts(arguments.projects_dir, found)
        print(f"indexed {len(index)} subagent transcripts under {arguments.projects_dir}")
        if ambiguous:
            print(
                f"  {len(ambiguous)} id(s) found under more than one project slug with "
                f"differing copies, left unindexed (a row may still pick its copy by "
                f"its parent session): {ambiguous[:5]}"
            )
        candidates = {agent_id: found[agent_id] for agent_id in ambiguous}
        transcripts = subagent_transcripts(records, index, candidates)
    else:
        transcripts = index_transcripts(arguments.projects_dir)
        print(f"indexed {len(transcripts)} session transcripts under {arguments.projects_dir}")

    if arguments.fields != "tokens":
        return run_field_pass(arguments, path, snapshot, records, transcripts, id_key)

    result = plan_corrections_by_formula(records, transcripts, arguments.cutoff, id_key)
    plan, stats, factors = result.values, result.stats, result.factors
    corrected = apply_plan(records, plan, result.model_maps)

    before_total = sum(token_sum(record) for record in records)
    after_total = sum(token_sum(record) for record in corrected)
    print_summary(stats, factors, before_total, after_total, len(plan), len(raw_lines))
    print_field_deltas(records, corrected)
    print_daily_comparison(records, corrected, arguments.daily_days)

    # Each corrected row may move in ONE direction, fixed by the formula it
    # was recognised as: a raw sum only down, a first-occurrence sum only
    # up. tokens_by_model may change value on a corrected row (apply_plan
    # rebuilt it by the same rule as the totals) but not appear, vanish or
    # move - and the check below proves the rebuilt map sums to the new
    # totals rather than trusting it.
    rising = frozenset(index for index, formula in result.formulas.items() if formula == FIRST)
    problems = verify_fields(
        records,
        corrected,
        may_increase=rising,
        allow_changed_keys=frozenset({"tokens_by_model"}),
    )
    problems += unreconciled_maps(corrected, plan)
    if problems:
        print(f"\nFIELD VERIFICATION FAILED ({len(problems)} problems):", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(
        "\nfield-by-field verification: only token fields and tokens_by_model changed; "
        f"{len(plan) - len(rising)} raw-sum row(s) only fell, {len(rising)} "
        "first-occurrence row(s) only rose; every rewritten map sums to its row's totals"
    )

    before_offenders = find_nonmonotonic_sessions(records, id_key)
    after_offenders = find_nonmonotonic_sessions(corrected, id_key)
    introduced = after_offenders - before_offenders
    print(
        f"non-monotonic snapshot series: {len(before_offenders)} before, "
        f"{len(after_offenders)} after, {len(introduced)} introduced"
    )
    if introduced:
        print(f"MONOTONICITY REGRESSION in {sorted(introduced)[:5]}", file=sys.stderr)
        return 1

    if not arguments.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to write.")
        return 0

    if not plan:
        # No state write either: the state records the run that corrected
        # rows - its cutoff, its earliest corrected row, the retention note
        # the dashboards render - and a caught-up rerun has nothing to add
        # but zeros that would erase it.
        print("\nnothing to correct; leaving the file and the backfill state untouched")
        return 0

    def record_state() -> None:
        # The earliest timestamp_end actually corrected, and the count of
        # rows that could never be (no transcript on disk) - both feed the
        # boundary note's other half: WHERE correction stops for lack of
        # transcript retention, not just the cutoff date. Called by
        # write_atomically the moment the corrected file is in place, BEFORE
        # the post-write checks: if one of them fails the file is already
        # rewritten, and an unrecorded rewrite is never recorded - a re-run
        # finds every row corrected and has nothing to write.
        earliest_corrected = min((records[index]["timestamp_end"] for index in plan), default=None)
        write_backfill_state(
            arguments.cutoff,
            datetime.now().isoformat(),
            len(plan),
            uncorrectable_rows=stats.get("untouched: no transcript on disk", 0),
            earliest_corrected=earliest_corrected,
            path=arguments.state_file,
            kind=arguments.kind,
        )
        print(f"  backfill state recorded: {arguments.state_file}")

    return back_up_and_write(
        path, snapshot, len(raw_lines), corrected, arguments.backup_dir, record_state
    )


if __name__ == "__main__":
    sys.exit(main())
