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
- only the four token fields (and tokens_by_model) are rewritten:
  tool_call_count on historical rows stays as recorded, first-entry
  undercount included (the hook counts distinct tool_use blocks since
  2026-09-23).

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
    UNIT_ID_KEYS,
    apply_plan,
    carry_late_appends,
    daily_totals,
    file_kind_problem,
    find_nonmonotonic_sessions,
    index_subagent_transcripts,
    index_transcripts,
    plan_corrections_by_formula,
    sha256_bytes,
    sha256_of,
    subagent_transcripts,
    token_sum,
    verify_fields,
    write_backfill_state,
)
from metrics_core.transcript_ingest import TOKEN_FIELDS

METRICS_FILES = {
    kind: str(Path(f"~/.claude/metrics/{kind}.jsonl").expanduser()) for kind in UNIT_ID_KEYS
}
BACKUP_DIR = str(Path("~/.claude/metrics/backups").expanduser())
PROJECTS_DIR = str(Path("~/.claude/projects").expanduser())


def print_daily_comparison(
    before: list[dict[str, Any]], after: list[dict[str, Any]], days: int | None
) -> None:
    before_map = dict(daily_totals(before, days))
    after_map = dict(daily_totals(after, days))
    print(f"\nPer-day token totals via expand_records_to_daily_deltas (last {days} days)")
    print(f"  {'day':<12}{'before':>18}{'after':>18}{'factor':>10}")
    for day in sorted(set(before_map) | set(after_map)):
        b = before_map.get(day, 0)
        a = after_map.get(day, 0)
        factor = f"{b / a:.3f}x" if a else ("COLLAPSED" if b else "-")
        print(f"  {day:<12}{b:>18,}{a:>18,}{factor:>10}")
    collapsed = [d for d in before_map if before_map[d] > 0 and after_map.get(d, 0) == 0]
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
    for reason, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>6}  {reason}")
    print(f"\nLines: {total_lines} read, {changed_lines} would change")
    if factors:
        factors = sorted(factors)

        def pct(p: float) -> float:
            return factors[min(len(factors) - 1, int(len(factors) * p))]

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
        with os.fdopen(handle, "wb") as f:
            for line in new_lines:
                f.write(line)
                f.write(b"\n")
            f.flush()
            os.fsync(f.fileno())

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
                with temporary.open("ab") as f:
                    f.write(tail)
                    f.flush()
                    os.fsync(f.fileno())

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
    parser.add_argument("--projects-dir", default=PROJECTS_DIR)
    parser.add_argument("--backup-dir", default=BACKUP_DIR)
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
        "--apply", action="store_true", help="write the result; without it this is a dry run"
    )
    args = parser.parse_args()

    id_key = UNIT_ID_KEYS[args.kind]
    path = args.metrics_file or METRICS_FILES[args.kind]
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

    kind_problem = file_kind_problem(records, args.kind)
    if kind_problem:
        print(f"refusing: --kind {args.kind}, but {kind_problem}", file=sys.stderr)
        return 1

    if args.kind == "subagents":
        index, ambiguous = index_subagent_transcripts(args.projects_dir)
        print(f"indexed {len(index)} subagent transcripts under {args.projects_dir}")
        if ambiguous:
            print(
                f"  {len(ambiguous)} id(s) found under more than one project slug and "
                f"left unindexed: {ambiguous[:5]}"
            )
        transcripts = subagent_transcripts(records, index)
    else:
        transcripts = index_transcripts(args.projects_dir)
        print(f"indexed {len(transcripts)} session transcripts under {args.projects_dir}")

    result = plan_corrections_by_formula(records, transcripts, args.cutoff, id_key)
    plan, stats, factors = result.values, result.stats, result.factors
    corrected = apply_plan(records, plan, result.model_maps)

    before_total = sum(token_sum(r) for r in records)
    after_total = sum(token_sum(r) for r in corrected)
    print_summary(stats, factors, before_total, after_total, len(plan), len(raw_lines))
    print_field_deltas(records, corrected)
    print_daily_comparison(records, corrected, args.daily_days)

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

    if not args.apply:
        print("\nDRY RUN - nothing written. Re-run with --apply to write.")
        return 0

    if not plan:
        # No state write either: the state records the run that corrected
        # rows - its cutoff, its earliest corrected row, the retention note
        # the dashboards render - and a caught-up rerun has nothing to add
        # but zeros that would erase it.
        print("\nnothing to correct; leaving the file and the backfill state untouched")
        return 0
    backup_dir = Path(args.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = backup_dir / f"{metrics_path.name}.backfill-{stamp}.bak"
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
            args.cutoff,
            datetime.now().isoformat(),
            len(plan),
            uncorrectable_rows=stats.get("untouched: no transcript on disk", 0),
            earliest_corrected=earliest_corrected,
            path=args.state_file,
            kind=args.kind,
        )
        print(f"  backfill state recorded: {args.state_file}")

    mode = stat.S_IMODE(metrics_path.stat().st_mode)
    new_lines = [json.dumps(record).encode("utf-8") for record in corrected]
    appended = write_atomically(path, snapshot, new_lines, mode, on_replaced=record_state)

    written = metrics_path.read_bytes()
    written_count = written.count(b"\n")
    expected = len(raw_lines) + appended
    print(
        f"wrote {path}: {written_count} lines "
        f"({len(raw_lines)} rewritten + {appended} appended while running)"
    )
    if written_count != expected:
        print(f"LINE COUNT MISMATCH: expected {expected}, got {written_count}", file=sys.stderr)
        return 1
    print(f"  mode {oct(stat.S_IMODE(metrics_path.stat().st_mode))}")
    print(f"  sha256 {sha256_bytes(written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
