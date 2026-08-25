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
deduped by message.id exactly as the fixed parse_transcript does.

Why the row selection is evidence-driven, not date-driven
---------------------------------------------------------
For every row two values are reconstructed from the transcript: the DEDUPED
as-of sum (what a correct hook would have written) and the RAW as-of sum
(what the broken hook did write). A row is only rewritten when its stored
value reproduces the RAW sum exactly - that is positive proof both that the
row came from the broken hook and that the reconstruction models this row
correctly. A row already equal to the DEDUPED sum is left alone, which is
what makes a second run a no-op instead of a second correction.

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
- only the four token fields may change, and only downwards; every other
  field is compared byte-for-byte before the rename.
"""

import argparse
import collections
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

from metrics_core.backfill import (
    BACKFILL_STATE_FILE,
    DEFAULT_CUTOFF,
    apply_plan,
    daily_totals,
    find_nonmonotonic_sessions,
    index_transcripts,
    plan_corrections,
    sha256_bytes,
    sha256_of,
    token_sum,
    verify_fields,
    write_backfill_state,
)

METRICS_FILE = os.path.expanduser("~/.claude/metrics/sessions.jsonl")
BACKUP_DIR = os.path.expanduser("~/.claude/metrics/backups")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def print_daily_comparison(before, after, days):
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


def print_summary(stats, factors, before_total, after_total, changed_lines, total_lines):
    print("\nRow disposition")
    for reason, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>6}  {reason}")
    print(f"\nLines: {total_lines} read, {changed_lines} would change")
    if factors:
        factors = sorted(factors)

        def pct(p):
            return factors[min(len(factors) - 1, int(len(factors) * p))]

        print("Correction factor (stored / recomputed) over corrected rows:")
        print(
            f"  min {factors[0]:.3f}x  p25 {pct(0.25):.3f}x  median {pct(0.5):.3f}x  "
            f"p75 {pct(0.75):.3f}x  max {factors[-1]:.3f}x"
        )
        buckets = collections.Counter()
        for factor in factors:
            if factor < 1.001:
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


def _valid_cutoff(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"--cutoff must be YYYY-MM-DD, got {value!r}")
    # strptime alone is not enough: it accepts non-zero-padded values like
    # "2026-8-21" as valid dates, which is exactly the malformed shape that
    # would sort wrong lexicographically against timestamp_end strings -
    # the failure mode this validator exists to catch. Round-tripping back
    # through the canonical format rejects anything that isn't already
    # zero-padded YYYY-MM-DD, even though it parsed.
    if parsed.strftime("%Y-%m-%d") != value:
        raise argparse.ArgumentTypeError(f"--cutoff must be YYYY-MM-DD, got {value!r}")
    return value


def write_atomically(path, snapshot_prefix, new_lines, mode):
    """Rename a fully written file into place, carrying over live appends.

    The metrics file is append-only and written by every running session, so
    between reading it and replacing it new rows may have arrived. The body
    below is written first since it can be slow for a large correction; the
    live file's tail is read again right before the rename - as late as
    possible - checked against the snapshot prefix, and appended verbatim.
    This keeps the window in which a concurrent append could be missed to
    that final read-verify-append-rename sequence, not the whole write.
    """
    directory = os.path.dirname(path)
    handle, temporary = tempfile.mkstemp(dir=directory, prefix=".sessions.jsonl.", suffix=".new")
    try:
        with os.fdopen(handle, "wb") as f:
            for line in new_lines:
                f.write(line)
                f.write(b"\n")
            f.flush()
            os.fsync(f.fileno())

        with open(path, "rb") as f:
            current = f.read()
        if current[: len(snapshot_prefix)] != snapshot_prefix:
            raise RuntimeError(
                "refusing to write: the file changed under us in a way that is not "
                "a plain append (its first bytes no longer match the snapshot)"
            )
        tail = current[len(snapshot_prefix) :]
        if tail:
            with open(temporary, "ab") as f:
                f.write(tail)
                f.flush()
                os.fsync(f.fileno())

        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return len(tail.split(b"\n")) - 1 if tail else 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--metrics-file", default=METRICS_FILE)
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

    path = args.metrics_file
    if not os.path.exists(path):
        print(f"no metrics file at {path}", file=sys.stderr)
        return 1

    with open(path, "rb") as f:
        data = f.read()
    # Ignore a partially written trailing line: a concurrent hook may be
    # mid-append. Everything from there on is treated as live tail.
    usable = data.rfind(b"\n") + 1
    snapshot = data[:usable]
    raw_lines = snapshot.split(b"\n")[:-1]
    print(f"read {len(raw_lines)} lines from {path}")
    print(f"  sha256 {sha256_bytes(data)}")

    records = []
    for number, raw in enumerate(raw_lines, 1):
        try:
            records.append(json.loads(raw.decode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            print(f"line {number} is not valid JSON, aborting: {error}", file=sys.stderr)
            return 1

    transcripts = index_transcripts(args.projects_dir)
    print(f"indexed {len(transcripts)} session transcripts under {args.projects_dir}")

    plan, stats, factors = plan_corrections(records, transcripts, args.cutoff)
    corrected = apply_plan(records, plan)

    before_total = sum(token_sum(r) for r in records)
    after_total = sum(token_sum(r) for r in corrected)
    print_summary(stats, factors, before_total, after_total, len(plan), len(raw_lines))
    print_daily_comparison(records, corrected, args.daily_days)

    problems = verify_fields(records, corrected)
    if problems:
        print(f"\nFIELD VERIFICATION FAILED ({len(problems)} problems):", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("\nfield-by-field verification: only token fields changed, none increased")

    before_offenders = find_nonmonotonic_sessions(records)
    after_offenders = find_nonmonotonic_sessions(corrected)
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
        print("\nnothing to correct; leaving the file untouched")
    else:
        os.makedirs(args.backup_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        backup = os.path.join(args.backup_dir, f"sessions.jsonl.backfill-{stamp}.bak")
        shutil.copy2(path, backup)
        source_sha = sha256_of(path)
        backup_sha = sha256_of(backup)
        if source_sha != backup_sha:
            # Can legitimately differ if a hook appended between the copy and the
            # re-read; either way an unverified backup is not a backup.
            print(
                f"BACKUP VERIFICATION FAILED\n  source {source_sha}\n  backup {backup_sha}",
                file=sys.stderr,
            )
            return 1
        print(f"\nbackup verified: {backup}\n  sha256 {backup_sha}")

        mode = stat.S_IMODE(os.stat(path).st_mode)
        new_lines = [json.dumps(record).encode("utf-8") for record in corrected]
        appended = write_atomically(path, snapshot, new_lines, mode)

        with open(path, "rb") as f:
            written = f.read()
        written_count = written.count(b"\n")
        expected = len(raw_lines) + appended
        print(
            f"wrote {path}: {written_count} lines "
            f"({len(raw_lines)} rewritten + {appended} appended while running)"
        )
        if written_count != expected:
            print(f"LINE COUNT MISMATCH: expected {expected}, got {written_count}", file=sys.stderr)
            return 1
        print(f"  mode {oct(stat.S_IMODE(os.stat(path).st_mode))}")
        print(f"  sha256 {sha256_bytes(written)}")

    write_backfill_state(args.cutoff, datetime.now().isoformat(), len(plan), path=args.state_file)
    print(f"  backfill state recorded: {args.state_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
