"""Recompute historical session token counts inflated by the pre-dedup
logging hook, from source transcripts - the pure logic shared by
scripts/backfill-token-dedup.py (which does the live file rewrite) and
both dashboards (which read the state this module's functions record,
to show the correction boundary honestly instead of a hardcoded date).
"""
import bisect
import collections
import glob
import hashlib
import json
import os

from .records import expand_records_to_daily_deltas

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
# transcript usage key for each of our field names
USAGE_KEYS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_creation_tokens": "cache_creation_input_tokens",
}
# Rows written on or after this date are left alone: the correction is only
# claimed for the window it was verified on. Override with --cutoff once the
# fixed hook is actually deployed (see the report - as of 2026-08-18 the
# running hook is the plugin-cache copy, which still has no dedup).
DEFAULT_CUTOFF = "2026-08-13"
# A row that reproduces neither formula is still treated as broken-hook output
# when it sits this close to the raw sum. Cause: entries timestamped before a
# snapshot were appended to the transcript after it, so the file no longer
# reproduces that moment exactly. Measured on real data, 58 of 59 such rows
# land within 0.3% of the raw sum while being ~2x the deduped one, and eight
# of them belong to the largest session in the file - refusing them outright
# would leave more than half of all inflation uncorrected.
NEITHER_TOLERANCE = 0.02
BACKFILL_STATE_FILE = os.path.expanduser("~/.claude/metrics/backfill_state.json")


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def index_transcripts(projects_dir):
    """session_id -> transcript path.

    Session transcripts live at <projects>/<slug>/<session_id>.jsonl. Deeper
    paths (<slug>/<session_id>/subagents/agent-*.jsonl) are subagent
    transcripts and belong to subagents.jsonl, not here. A handful of ids
    exist under two slugs (the same session logged from two cwds); the
    largest file is the complete one.
    """
    index = {}
    for path in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
        session_id = os.path.basename(path)[:-len(".jsonl")]
        current = index.get(session_id)
        if current is None or os.path.getsize(path) > os.path.getsize(current):
            index[session_id] = path
    return index


def scan_transcript(path):
    """Timeline of assistant entries, flagged as duplicates in FILE order.

    Duplicate detection has to follow file order because that is what the
    hook does; the flags are then reused for any as-of cutoff, which is
    sound because a repeated message id was verified to always carry
    identical usage numbers.
    """
    timeline = []
    seen_ids = set()
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message") or {}
            usage = message.get("usage") or {}
            values = tuple(usage.get(USAGE_KEYS[field], 0) or 0 for field in TOKEN_FIELDS)
            message_id = message.get("id")
            is_duplicate = message_id is not None and message_id in seen_ids
            if message_id is not None:
                seen_ids.add(message_id)
            timeline.append((entry.get("timestamp") or "", is_duplicate, values))
    return timeline


def build_prefix_sums(timeline):
    """Sorted timestamps plus running deduped/raw totals, for as-of lookups.

    Timestamps are NOT monotonic in file order (a replayed turn keeps its
    original timestamp), so they are sorted here. Summing a set of entries is
    order-independent, and the duplicate flags were already fixed in file
    order, so this changes nothing but the lookup cost.
    """
    ordered = sorted(timeline, key=lambda item: item[0])
    stamps = [item[0] for item in ordered]
    deduped = [[0] * 4]
    raw = [[0] * 4]
    for _, is_duplicate, values in ordered:
        previous_d = deduped[-1]
        previous_r = raw[-1]
        raw.append([previous_r[i] + values[i] for i in range(4)])
        if is_duplicate:
            deduped.append(list(previous_d))
        else:
            deduped.append([previous_d[i] + values[i] for i in range(4)])
    return stamps, deduped, raw


def as_of(stamps, deduped, raw, end_timestamp):
    """Cumulative deduped and raw totals for entries at or before a moment."""
    if end_timestamp is None:
        count = len(stamps)
    else:
        count = bisect.bisect_right(stamps, end_timestamp)
    return deduped[count], raw[count]


def token_sum(record):
    return sum(record.get(field, 0) or 0 for field in TOKEN_FIELDS)


def plan_corrections(records, transcripts, cutoff):
    """Decide which sessions can be recomputed, and to what.

    Returns (plan, stats, factors) where plan maps a row index to its new
    token values. Decisions are taken per SESSION and applied to all of its
    rows or none of them; every skip reason is counted so the dry run shows
    exactly what was and was not touched.
    """
    stats = collections.Counter()
    factors = []
    plan = {}

    rows_by_session = collections.defaultdict(list)
    for index, record in enumerate(records):
        rows_by_session[record.get("session_id")].append(index)

    for session_id, indexes in rows_by_session.items():
        transcript = transcripts.get(session_id)
        if transcript is None:
            stats["untouched: no transcript on disk"] += len(indexes)
            continue
        if any(
            not records[index].get("timestamp_end")
            or records[index]["timestamp_end"] >= cutoff
            for index in indexes
        ):
            # One row past the cutoff disqualifies the whole session, so the
            # snapshot series is never left half corrected.
            stats["untouched: session spans the cutoff"] += len(indexes)
            continue

        stamps, deduped, raw = build_prefix_sums(scan_transcript(transcript))
        proposals = {}
        already_correct = 0
        refused = None
        for index in indexes:
            record = records[index]
            stored = [record.get(field, 0) or 0 for field in TOKEN_FIELDS]
            deduped_now, raw_now = as_of(stamps, deduped, raw, record["timestamp_end"])
            if stored == deduped_now:
                already_correct += 1
                continue
            if stored != raw_now:
                total_raw = sum(raw_now)
                drift = abs(sum(stored) / total_raw - 1.0) if total_raw else 1.0
                if drift > NEITHER_TOLERANCE:
                    refused = "untouched: session has a row matching neither formula"
                    break
            if any(deduped_now[i] > stored[i] for i in range(4)):
                refused = "untouched: session where a correction would increase a field"
                break
            proposals[index] = deduped_now

        if refused:
            stats[refused] += len(indexes)
            continue
        if not proposals:
            stats["untouched: already correct"] += len(indexes)
            continue

        stats["corrected"] += len(proposals)
        stats["untouched: already correct"] += already_correct
        for index, values in proposals.items():
            plan[index] = values
            before = sum(records[index].get(field, 0) or 0 for field in TOKEN_FIELDS)
            after = sum(values)
            if after:
                factors.append(before / after)
    return plan, stats, factors


def find_nonmonotonic_sessions(records):
    """Sessions whose snapshot series is not non-decreasing.

    expand_records_to_daily_deltas orders a session's snapshots by
    duration_seconds and differences them, flooring at zero - so a series
    that goes DOWN silently drops usage. Any such session introduced by this
    rewrite would be a bug; pre-existing ones are reported for comparison.
    """
    by_session = collections.defaultdict(list)
    for record in records:
        by_session[record.get("session_id")].append(record)
    offenders = set()
    for session_id, snapshots in by_session.items():
        snapshots = sorted(snapshots, key=lambda r: r.get("duration_seconds") or 0)
        previous = [0] * 4
        for snapshot in snapshots:
            current = [snapshot.get(field, 0) or 0 for field in TOKEN_FIELDS]
            if any(current[i] < previous[i] for i in range(4)):
                offenders.add(session_id)
                break
            previous = current
    return offenders


def apply_plan(records, plan):
    corrected = []
    for index, record in enumerate(records):
        if index in plan:
            updated = dict(record)
            for position, field in enumerate(TOKEN_FIELDS):
                updated[field] = plan[index][position]
            corrected.append(updated)
        else:
            corrected.append(record)
    return corrected


def daily_totals(records, days):
    """Per-day token totals through the same code path the trend chart uses."""
    totals = collections.Counter()
    for delta in expand_records_to_daily_deltas([dict(r) for r in records]):
        moment = delta.get("timestamp_start")
        if not moment:
            continue
        totals[moment[:10]] += token_sum(delta)
    ordered = sorted(totals)
    if days:
        ordered = ordered[-days:]
    return [(day, totals[day]) for day in ordered]


def verify_fields(before, after):
    """Every non-token field must survive untouched, tokens may only drop."""
    if len(before) != len(after):
        return [f"record count changed: {len(before)} -> {len(after)}"]
    problems = []
    for index, (old, new) in enumerate(zip(before, after)):
        if set(old.keys()) != set(new.keys()):
            problems.append(f"line {index + 1}: key set changed")
            continue
        if list(old.keys()) != list(new.keys()):
            problems.append(f"line {index + 1}: key order changed")
            continue
        for key in old:
            if key in TOKEN_FIELDS:
                if (new[key] or 0) > (old[key] or 0):
                    problems.append(f"line {index + 1}: {key} increased")
            elif old[key] != new[key]:
                problems.append(f"line {index + 1}: {key} changed {old[key]!r} -> {new[key]!r}")
    return problems


def load_backfill_state(path=None):
    """Metadata about the most recent successful --apply run, or None.

    Read by both dashboards to render the correction boundary honestly;
    written only by scripts/backfill-token-dedup.py after a verified
    write to the live metrics file.
    """
    path = path or BACKFILL_STATE_FILE
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def write_backfill_state(cutoff, applied_at, corrected_rows, path=None):
    path = path or BACKFILL_STATE_FILE
    state = {"cutoff": cutoff, "applied_at": applied_at, "corrected_rows": corrected_rows}
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
    return state


def describe_backfill_state(state):
    """Plain-language boundary note, rendered verbatim by both dashboards."""
    if state is None:
        return (
            f"duplicação de tokens ainda não corrigida no histórico "
            f"(dados anteriores a {DEFAULT_CUTOFF} podem estar inflados)"
        )
    return (
        f"duplicação corrigida para sessões encerradas até {state['cutoff']} "
        f"(aplicado em {state['applied_at'][:10]}); sessões sem transcript "
        f"salvo continuam com contagem inflada"
    )
