"""Recompute historical session token counts inflated by the pre-dedup
logging hook, from source transcripts - the pure logic shared by
scripts/backfill-token-dedup.py (which does the live file rewrite) and
both dashboards (which read the state this module's functions record,
to show the correction boundary honestly instead of a hardcoded date).
"""

from __future__ import annotations

import bisect
import collections
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from .i18n import t
from .records import expand_records_to_daily_deltas
from .transcript_ingest import SYNTHETIC_MODEL, TOKEN_FIELDS, USAGE_KEYS, model_key

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
BACKFILL_STATE_FILE = str(Path("~/.claude/metrics/backfill_state.json").expanduser())

# Which id names a unit - the owner of one growing snapshot series - in each
# metrics file. Subagent rows carry session_id TOO, but it is their parent's:
# grouping them on it merges every subagent one session dispatched into a
# single unit, the 2026-08-06 contamination (1012 of 1036 agent_ids
# mismatched). Session rows never carry agent_id at all.
UNIT_ID_KEYS: dict[str, str] = {"sessions": "session_id", "subagents": "agent_id"}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def index_transcripts(projects_dir: str) -> dict[str, str]:
    """session_id -> transcript path.

    Session transcripts live at <projects>/<slug>/<session_id>.jsonl. Deeper
    paths (<slug>/<session_id>/subagents/agent-*.jsonl) are subagent
    transcripts and belong to subagents.jsonl, not here. A handful of ids
    exist under two slugs (the same session logged from two cwds); the
    largest file is the complete one.
    """
    index: dict[str, str] = {}
    # Path.glob (unlike glob.glob) also matches dot-prefixed entries; safe here
    # because Claude Code's own naming scheme never produces a leading dot.
    for path in (str(p) for p in Path(projects_dir).glob("*/*.jsonl")):
        session_id = Path(path).name[: -len(".jsonl")]
        current = index.get(session_id)
        if current is None or Path(path).stat().st_size > Path(current).stat().st_size:
            index[session_id] = path
    return index


def index_subagent_transcripts(projects_dir: str) -> tuple[dict[str, str], list[str]]:
    """agent_id -> transcript path, for the files index_transcripts excludes.

    Subagent transcripts sit one level deeper than session ones, at
    <projects>/<slug>/<session_id>/subagents/<agent_id>.jsonl, and are keyed
    by AGENT id. index_transcripts deliberately walks only <slug>/*.jsonl and
    keys by SESSION id - its docstring says the deeper paths belong to
    subagents.jsonl, not there - so handing it to subagent code hands over an
    index that cannot hit on any key. Measured 2026-09-16: 44 native subagent
    snapshots were being skipped as "no transcript" while all 44 files were
    on disk, migrated into the parent project slug exactly as the session
    transcripts were.

    The filename carries the agent id with an `agent-` PREFIX that the record
    itself does not have: the file is `agent-a04a2851b378c2216.jsonl` and the
    record's agent_id is `a04a2851b378c2216`. Measured 2026-09-16 over the
    whole tree: 1,005 of 1,005 transcript basenames carry the prefix, 2,124
    of 2,124 agent_id values do not, and all 1,953 rows with a recorded path
    have `basename == "agent-" + agent_id`. Keying by the raw basename
    therefore builds an index that looks populated and never matches - the
    same class of failure as handing over the session index, one level down.

    An agent id found under more than one slug is AMBIGUITY, not a match:
    nothing here can tell which copy the record was parsed from, and picking
    the bigger or the first would be a guess dressed as a resolution. Those
    ids are left out of the index and returned so the run can name them.

    Shared by scripts/backfill-tokens-by-model.py and
    scripts/backfill-token-dedup.py, so both find a subagent's transcript
    the same way.
    """
    prefix = "agent-"
    found: dict[str, list[str]] = collections.defaultdict(list)
    for path in Path(projects_dir).glob("*/*/subagents/*.jsonl"):
        stem = path.name[: -len(".jsonl")]
        found[stem.removeprefix(prefix)].append(str(path))
    index = {agent_id: paths[0] for agent_id, paths in found.items() if len(paths) == 1}
    ambiguous = sorted(agent_id for agent_id, paths in found.items() if len(paths) > 1)
    return index, ambiguous


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
    resolves to None and disqualifies its whole unit. The by-id index is a
    way to relocate a known transcript, not a way to guess one.
    """
    recorded = record.get(transcript_key)
    if not recorded:
        return None
    if Path(recorded).exists():
        return str(recorded)
    return transcripts.get(unit_id)


def subagent_transcripts(records: list[dict[str, Any]], index: dict[str, str]) -> dict[str, str]:
    """agent_id -> the one transcript every row of that subagent resolves to.

    The subagent counterpart of index_transcripts, in the shape
    plan_corrections takes: one transcript per unit. Each row goes through
    resolve_transcript (its recorded agent_transcript_path, relocated through
    the by-id index when stale), and a unit is left out - so plan_corrections
    reports it as having no transcript - when any row fails to resolve, or
    when its rows resolve to different files: nothing here can say which of
    two files a single as-of series was parsed from.
    """
    resolved: dict[str, set[str | None]] = collections.defaultdict(set)
    for record in records:
        agent_id = record.get("agent_id")
        if not agent_id:
            continue
        resolved[agent_id].add(resolve_transcript(record, index, "agent_transcript_path", agent_id))
    units: dict[str, str] = {}
    for agent_id, paths in resolved.items():
        (only, *others) = paths
        if only is not None and not others:
            units[agent_id] = only
    return units


def file_kind_problem(records: list[dict[str, Any]], kind: str) -> str | None:
    """Why these rows cannot be the declared kind of metrics file, or None.

    The two files differ by one key: subagent rows carry agent_id, session
    rows never do. A subagents run over rows without one would group them
    under None; a sessions run over subagent rows would group them on their
    PARENT's session_id. Both are the contamination this check exists to
    refuse up front, before any plan is computed.
    """
    total = len(records)
    if kind == "subagents":
        missing = sum(1 for record in records if not record.get("agent_id"))
        if missing:
            return (
                f"{missing} of {total} rows have no agent_id - this does not look like "
                "subagents.jsonl"
            )
        return None
    carrying = sum(1 for record in records if "agent_id" in record)
    if carrying:
        return (
            f"{carrying} of {total} rows carry an agent_id - this looks like "
            "subagents.jsonl; pass --kind subagents"
        )
    return None


def scan_transcript(path: str) -> list[tuple[str, bool, tuple[int, ...], str]]:
    """Timeline of assistant entries, flagged as duplicates in FILE order.

    Duplicate detection has to follow file order because that is what the
    hook does; the flags are then reused for any as-of cutoff, which is
    sound because a repeated message id was verified to always carry
    identical usage numbers. Each entry also carries the model that
    produced it (via the shared model_key rule), so callers can build
    per-model as-of totals without re-reading the transcript.
    """
    timeline: list[tuple[str, bool, tuple[int, ...], str]] = []
    seen_ids: set[Any] = set()
    with Path(path).open() as f:
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
            timeline.append(
                (entry.get("timestamp") or "", is_duplicate, values, model_key(message))
            )
    return timeline


def build_prefix_sums(
    timeline: list[tuple[str, bool, tuple[int, ...], str]],
) -> tuple[list[str], list[list[int]], list[list[int]]]:
    """Sorted timestamps plus running deduped/raw totals, for as-of lookups.

    Timestamps are NOT monotonic in file order (a replayed turn keeps its
    original timestamp), so they are sorted here. Summing a set of entries is
    order-independent, and the duplicate flags were already fixed in file
    order, so this changes nothing but the lookup cost.
    """
    ordered = sorted(timeline, key=lambda item: item[0])
    stamps = [item[0] for item in ordered]
    deduped: list[list[int]] = [[0] * 4]
    raw: list[list[int]] = [[0] * 4]
    for _stamp, is_duplicate, values, _model in ordered:
        previous_d = deduped[-1]
        previous_r = raw[-1]
        raw.append([previous_r[i] + values[i] for i in range(4)])
        if is_duplicate:
            deduped.append(list(previous_d))
        else:
            deduped.append([previous_d[i] + values[i] for i in range(4)])
    return stamps, deduped, raw


def as_of(
    stamps: list[str], deduped: list[list[int]], raw: list[list[int]], end_timestamp: str | None
) -> tuple[list[int], list[int]]:
    """Cumulative deduped and raw totals for entries at or before a moment."""
    count = len(stamps) if end_timestamp is None else bisect.bisect_right(stamps, end_timestamp)
    return deduped[count], raw[count]


def model_totals_as_of(
    timeline: list[tuple[str, bool, tuple[int, ...], str]], end_timestamp: str | None
) -> dict[str, dict[str, int]]:
    """Per-model cumulative totals for entries at or before a moment.

    The per-model counterpart of as_of, and deliberately a linear scan rather
    than a prefix-sum table: a session owns a few dozen snapshots against a
    transcript of a few thousand entries, so the table would cost more to
    build (one dict copy per entry per model) than the scans it saves.

    Replayed entries are skipped via the duplicate flag scan_transcript
    already computed, so a resumed session cannot inflate the model whose
    turns were replayed.
    """
    totals: dict[str, dict[str, int]] = {}
    for stamp, is_duplicate, values, model in timeline:
        if is_duplicate:
            continue
        if end_timestamp is not None and stamp > end_timestamp:
            continue
        if model == SYNTHETIC_MODEL and not any(values):
            continue
        bucket = totals.get(model)
        if bucket is None:
            bucket = totals[model] = dict.fromkeys(TOKEN_FIELDS, 0)
        for index, field in enumerate(TOKEN_FIELDS):
            bucket[field] += values[index]
    return totals


def token_sum(record: dict[str, Any]) -> int:
    return sum(record.get(field, 0) or 0 for field in TOKEN_FIELDS)


def plan_corrections(
    records: list[dict[str, Any]],
    transcripts: dict[str, str],
    cutoff: str,
    id_key: str = "session_id",
) -> tuple[dict[int, list[int]], collections.Counter[str], list[float]]:
    """Decide which sessions can be recomputed, and to what.

    Returns (plan, stats, factors) where plan maps a row index to its new
    token values. Decisions are taken per UNIT - the rows sharing `id_key` -
    and applied to all of its rows or none of them; every skip reason is
    counted so the dry run shows exactly what was and was not touched.

    `transcripts` is keyed by that same id. For subagents.jsonl pass
    id_key="agent_id" (see UNIT_ID_KEYS): the session_id on a subagent row
    is its parent's.
    """
    stats: collections.Counter[str] = collections.Counter()
    factors: list[float] = []
    plan: dict[int, list[int]] = {}

    rows_by_session: dict[Any, list[int]] = collections.defaultdict(list)
    for index, record in enumerate(records):
        rows_by_session[record.get(id_key)].append(index)

    for session_id, indexes in rows_by_session.items():
        transcript = transcripts.get(session_id)
        if transcript is None:
            stats["untouched: no transcript on disk"] += len(indexes)
            continue
        if any(
            not records[index].get("timestamp_end") or records[index]["timestamp_end"] >= cutoff
            for index in indexes
        ):
            # One row past the cutoff disqualifies the whole session, so the
            # snapshot series is never left half corrected.
            stats["untouched: session spans the cutoff"] += len(indexes)
            continue

        stamps, deduped, raw = build_prefix_sums(scan_transcript(transcript))
        proposals: dict[int, list[int]] = {}
        already_correct = 0
        refused: str | None = None
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


def find_nonmonotonic_sessions(
    records: list[dict[str, Any]], id_key: str = "session_id"
) -> set[Any]:
    """Units (rows sharing `id_key`) whose snapshot series is not
    non-decreasing.

    expand_records_to_daily_deltas orders a unit's snapshots by
    duration_seconds and differences them, flooring at zero - so a series
    that goes DOWN silently drops usage. Any such unit introduced by this
    rewrite would be a bug; pre-existing ones are reported for comparison.
    Subagents must pass id_key="agent_id", the key the expander groups them
    by: merged under their parent's session_id, two healthy agents read as
    one series that drops.
    """
    by_session: dict[Any, list[dict[str, Any]]] = collections.defaultdict(list)
    for record in records:
        by_session[record.get(id_key)].append(record)
    offenders: set[Any] = set()
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


def apply_plan(records: list[dict[str, Any]], plan: dict[int, list[int]]) -> list[dict[str, Any]]:
    corrected = []
    for index, record in enumerate(records):
        if index in plan:
            updated = dict(record)
            for position, field in enumerate(TOKEN_FIELDS):
                updated[field] = plan[index][position]
            # The per-model map was computed against the totals just replaced, so
            # it is now wrong and nothing here can recompute it (that needs the
            # transcript). Absent is how this schema spells "unknown".
            updated.pop("tokens_by_model", None)
            corrected.append(updated)
        else:
            corrected.append(record)
    return corrected


def daily_totals(records: list[dict[str, Any]], days: int | None) -> list[tuple[str, int]]:
    """Per-day token totals through the same code path the trend chart uses."""
    totals: collections.Counter[str] = collections.Counter()
    for delta in expand_records_to_daily_deltas([dict(r) for r in records]):
        moment = delta.get("timestamp_start")
        if not moment:
            continue
        totals[moment[:10]] += token_sum(delta)
    ordered = sorted(totals)
    if days:
        ordered = ordered[-days:]
    return [(day, totals[day]) for day in ordered]


def verify_fields(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    allow_new_keys: frozenset[str] = frozenset(),
    allow_removed_keys: frozenset[str] = frozenset(),
) -> list[str]:
    """Every non-token field must survive untouched, tokens may only drop.

    A key in allow_new_keys may appear where it was absent; a key in
    allow_removed_keys may disappear where it was present. Each direction is
    declared separately and grants only that direction - allowing a key's
    addition does not also allow its removal, nor the reverse.
    """
    if len(before) != len(after):
        return [f"record count changed: {len(before)} -> {len(after)}"]
    problems = []
    for index, (old, new) in enumerate(zip(before, after, strict=True)):
        # Declared additive and removable keys are invisible to every check
        # below, so the gate keeps rejecting an undeclared key, a reordering,
        # and any change to a pre-existing value - which is what makes this a
        # pair of parameters rather than a loosening.
        added = {key for key in new if key in allow_new_keys and key not in old}
        new = {key: value for key, value in new.items() if key not in added}
        removed = {key for key in old if key in allow_removed_keys and key not in new}
        old = {key: value for key, value in old.items() if key not in removed}
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


def load_backfill_state(path: str | None = None) -> dict[str, Any] | None:
    """Metadata about the most recent successful --apply run, or None.

    Read by both dashboards to render the correction boundary honestly;
    written only by scripts/backfill-token-dedup.py after a verified
    write to the live metrics file.
    """
    path = path or BACKFILL_STATE_FILE
    if not Path(path).exists():
        return None
    try:
        with Path(path).open() as f:
            return cast("dict[str, Any]", json.load(f))
    except (json.JSONDecodeError, OSError):
        return None


def write_backfill_state(
    cutoff: str,
    applied_at: str,
    corrected_rows: int,
    uncorrectable_rows: int = 0,
    earliest_corrected: str | None = None,
    path: str | None = None,
    kind: str = "sessions",
) -> dict[str, Any]:
    """Record a successful apply, merged into whatever the file holds.

    One state file serves both metrics files. The sessions entry keeps the
    flat top-level shape both dashboards read through describe_backfill_state
    (and that every state written before subagents were deduped has); a
    subagents run nests its entry under a "subagents" key. Each kind replaces
    only its own entry, so neither run erases the other's record. A file that
    does not hold a dict is replaced rather than merged into.
    """
    path = path or BACKFILL_STATE_FILE
    existing = load_backfill_state(path)
    state: dict[str, Any] = existing if isinstance(existing, dict) else {}
    entry = {
        "cutoff": cutoff,
        "applied_at": applied_at,
        "corrected_rows": corrected_rows,
        # Rows that plan_corrections() could never touch for lack of a
        # transcript on disk, and the earliest timestamp_end actually
        # corrected - together they mark WHERE the correction boundary
        # falls inside the transcript-retention window, not just when the
        # cutoff itself was set. See describe_backfill_state().
        "uncorrectable_rows": uncorrectable_rows,
        "earliest_corrected": earliest_corrected,
    }
    if kind == "subagents":
        state["subagents"] = entry
    else:
        state.update(entry)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as f:
        json.dump(state, f, indent=2)
    return state


def describe_backfill_state(state: dict[str, Any] | None, *, lang: str) -> str:
    """Plain-language boundary note, rendered verbatim by both dashboards.

    Degrades to the "never applied" note on any malformed state - missing
    keys, or valid JSON that isn't a dict at all - instead of raising
    KeyError/TypeError and crashing both dashboards over a state file that
    lives outside the repo and is hand-editable. load_backfill_state()
    already guards against corrupt JSON; this guards against JSON that
    parses fine but isn't the shape expected here.
    """
    never_applied = t("backfill.never_applied", lang, cutoff=DEFAULT_CUTOFF)
    if not isinstance(state, dict):
        return never_applied
    cutoff = state.get("cutoff")
    applied_at = state.get("applied_at")
    if not isinstance(cutoff, str) or not isinstance(applied_at, str):
        return never_applied
    note = t("backfill.applied", lang, cutoff=cutoff, applied=applied_at[:10])
    # The OTHER boundary (2026-08-24 backlog entry): correction is also
    # bounded by transcript retention, not just the cutoff date above. A
    # reader can't otherwise tell "that week was genuinely busier" from
    # "that week is still uncorrected" - measured on the 2026-08-21
    # production run, pre-2026-07-22 days averaged roughly the same
    # 1.6-2x factor the correction removes elsewhere.
    earliest_corrected = state.get("earliest_corrected")
    if isinstance(earliest_corrected, str) and earliest_corrected:
        note += t("backfill.retention", lang, date=earliest_corrected[:10])
    return note
