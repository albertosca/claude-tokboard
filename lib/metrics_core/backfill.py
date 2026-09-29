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
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, BinaryIO, NamedTuple, cast

from .i18n import t
from .records import expand_records_to_daily_deltas
from .transcript_ingest import (
    SYNTHETIC_MODEL,
    TOKEN_FIELDS,
    count_incomplete,
    model_key,
    usage_increment,
    usage_values,
)

# Rows written on or after this date are left alone: the correction is only
# claimed for the window it was verified on. Override with --cutoff once the
# fixed hook is actually deployed (see the report - as of 2026-08-18 the
# running hook is the plugin-cache copy, which still has no dedup).
DEFAULT_CUTOFF = "2026-08-13"
BACKFILL_STATE_FILE = str(Path("~/.claude/metrics/backfill_state.json").expanduser())

# Which id names a unit - the owner of one growing snapshot series - in each
# metrics file. Subagent rows carry session_id TOO, but it is their parent's:
# grouping them on it merges every subagent one session dispatched into a
# single unit, the 2026-08-06 contamination (1012 of 1036 agent_ids
# mismatched). Session rows never carry agent_id at all.
UNIT_ID_KEYS: dict[str, str] = {"sessions": "session_id", "subagents": "agent_id"}


def sha256_of(path: str) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def carry_late_appends(old: BinaryIO, target: Path, stamp: str) -> int:
    """After a rewritten file was renamed over `target`, move into it
    whatever a live hook appended to the REPLACED file since it was read.

    `old` must be a handle opened on the target BEFORE its final read and
    read to the end: it stays on the old inode across the rename, so reading
    it again returns exactly the bytes that landed there too late to be
    copied - which the 2026-09-23 review reproduced being lost. Complete
    lines are appended to the new file; a partial line cannot be (a hook's
    next line would glue onto it), so it is saved beside the target and the
    run fails loudly with its location. Returns the number of lines carried.

    This narrows the loss window to a hook that opened the old file before
    the rename and writes after this read - microseconds, not the whole
    final sequence - but cannot close it: no lock is shared with the hooks.
    """
    late = old.read()
    if not late:
        return 0
    if not late.endswith(b"\n"):
        rescue = target.with_name(f"{target.name}.late-append-{stamp}.partial")
        rescue.write_bytes(late)
        raise RuntimeError(
            f"a live append landed during the rename and ends mid-line; its "
            f"{len(late)} byte(s) were saved to {rescue} and NOT added to {target}"
        )
    with target.open("ab") as target_file:
        target_file.write(late)
        target_file.flush()
        os.fsync(target_file.fileno())
    return late.count(b"\n")


def index_transcripts(projects_directory: str) -> dict[str, str]:
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
    for path in (str(found_path) for found_path in Path(projects_directory).glob("*/*.jsonl")):
        session_id = Path(path).name[: -len(".jsonl")]
        current = index.get(session_id)
        if current is None or Path(path).stat().st_size > Path(current).stat().st_size:
            index[session_id] = path
    return index


def index_subagent_transcripts(
    projects_directory: str, found: dict[str, list[str]] | None = None
) -> tuple[dict[str, str], list[str]]:
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

    An agent id found under more than one slug whose copies are
    BYTE-IDENTICAL is one transcript stored twice - measured 2026-09-28, all
    93 such ids on disk were (a renamed project directory, a migrated
    worktree slug) - so the first path in sorted order stands for all of
    them: which copy is read cannot change a number. Copies that DIFFER are
    ambiguity, not a match: nothing here can tell which one the record was
    parsed from, and picking the bigger or the first would be a guess dressed
    as a resolution. Those ids are left out of the index and returned so the
    run can name them - and so subagent_transcripts can try the row's own
    parent session, the one piece of the row's data that tells copies apart.

    `found` is find_subagent_transcripts' result, for a caller that also
    needs the copies; without it the tree is walked here.

    Shared by scripts/backfill-tokens-by-model.py and
    scripts/backfill-token-dedup.py, so both find a subagent's transcript
    the same way.
    """
    if found is None:
        found = find_subagent_transcripts(projects_directory)
    index: dict[str, str] = {}
    ambiguous: list[str] = []
    for agent_id, paths in sorted(found.items()):
        if len({sha256_of(path) for path in paths}) == 1:
            index[agent_id] = paths[0]
        else:
            ambiguous.append(agent_id)
    return index, ambiguous


def find_subagent_transcripts(projects_directory: str) -> dict[str, list[str]]:
    """agent_id -> every copy of its transcript on disk, in sorted order.

    See index_subagent_transcripts for the path shape and the `agent-`
    filename prefix the record's own agent_id does not carry.
    """
    prefix = "agent-"
    found: dict[str, list[str]] = collections.defaultdict(list)
    for path in Path(projects_directory).glob("*/*/subagents/*.jsonl"):
        stem = path.name[: -len(".jsonl")]
        found[stem.removeprefix(prefix)].append(str(path))
    return {agent_id: sorted(paths) for agent_id, paths in found.items()}


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


def resolve_by_parent(record: dict[str, Any], copies: list[str]) -> str | None:
    """The one copy of an ambiguous subagent transcript that sits under this
    row's own parent session - <slug>/<session_id>/subagents/ - or None when
    none or several do. A row that recorded no path resolves to None here as
    everywhere else: the parent picks between known copies of a transcript
    the row did record, it does not invent one."""
    if not record.get("agent_transcript_path"):
        return None
    parent = record.get("session_id")
    matches = [path for path in copies if Path(path).parent.parent.name == parent]
    return matches[0] if len(matches) == 1 else None


def subagent_transcripts(
    records: list[dict[str, Any]],
    index: dict[str, str],
    candidates: dict[str, list[str]] | None = None,
) -> dict[str, str]:
    """agent_id -> the one transcript every row of that subagent resolves to.

    The subagent counterpart of index_transcripts, in the shape
    plan_corrections takes: one transcript per unit. Each row goes through
    resolve_transcript (its recorded agent_transcript_path, relocated through
    the by-id index when stale), and a unit is left out - so plan_corrections
    reports it as having no transcript - when any row fails to resolve, or
    when its rows resolve to different files: nothing here can say which of
    two files a single as-of series was parsed from.

    `candidates` maps an ambiguous agent id (differing copies, see
    index_subagent_transcripts) to its copies; a row that resolves nowhere
    else is then tried against them through resolve_by_parent.
    """
    candidates = candidates or {}
    resolved: dict[str, set[str | None]] = collections.defaultdict(set)
    for record in records:
        agent_id = record.get("agent_id")
        if not agent_id:
            continue
        path = resolve_transcript(record, index, "agent_transcript_path", agent_id)
        if path is None and agent_id in candidates:
            path = resolve_by_parent(record, candidates[agent_id])
        resolved[agent_id].add(path)
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


# One assistant entry: (timestamp, is_first_occurrence_duplicate, the four
# usage values, model, message id).
TimelineEntry = tuple[str, bool, tuple[int, ...], str, Any]
ModelMap = dict[str, dict[str, int]]


class PrefixSums(NamedTuple):
    """Timestamp-sorted entries and three running totals over them.

    `peak` is the correct total: each message id at its per-field peak (see
    transcript_ingest.usage_increment). `first` and `raw` are the two
    formulas earlier hooks wrote, kept so a stored row can be recognised as
    the output of one of them: `first` counts only the first entry of each
    id in file order (the hook from 2026-08-11 to 2026-09-23), `raw` sums
    every entry (the hook before that).
    """

    stamps: list[str]
    peak: list[list[int]]
    first: list[list[int]]
    raw: list[list[int]]


class AsOf(NamedTuple):
    peak: list[int]
    first: list[int]
    raw: list[int]


def scan_transcript(path: str) -> list[TimelineEntry]:
    """Timeline of assistant entries, in file order.

    Each entry keeps its message id, so the correct (peak) total can be
    rebuilt for any subset of entries, and a first-occurrence flag fixed in
    FILE order, which is what the 2026-08-11 hook did - kept only to
    recognise rows that hook wrote. The flag is NOT a statement that later
    entries of an id carry nothing new: an earlier version of this docstring
    said a repeated message id "was verified to always carry identical usage
    numbers", which is true of replays and false of streamed responses, whose
    entries grow in output_tokens (measured in usage_increment's docstring).
    Each entry also carries the model that produced it (via the shared
    model_key rule), so callers can build per-model as-of totals without
    re-reading the transcript.
    """
    return scan_unit(path).timeline


def read_transcript_entries(path: str) -> Iterator[dict[str, Any]]:
    """Every parsed line of a transcript, in file order, skipping blank and
    malformed lines - the same lines the hook skips."""
    with Path(path).open() as transcript_file:
        for line in transcript_file:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield entry


class EntryFacts(NamedTuple):
    """What one assistant entry says about tool calls and finalisation."""

    stamp: str
    message_id: Any
    finalised: bool
    # tool_use block ids in content order; None for a block without an id.
    tool_ids: tuple[Any, ...]


class TranscriptScan(NamedTuple):
    """Everything the plans need from one transcript, from ONE read of it:
    scan_transcript's timeline, scan_entry_facts' facts and
    scan_cut_points' cuts. A live transcript grows between reads, and a
    grown file can replay a line stamped with an old timestamp_end, so cuts
    taken from a later read than the totals can index past them."""

    timeline: list[TimelineEntry]
    facts: list[EntryFacts]
    cut_points: dict[str, tuple[int, ...]]


def scan_unit(path: str) -> TranscriptScan:
    """Read a transcript once and build the timeline, the entry facts and
    the cut points from the same lines, so their counts index the same
    entries."""
    timeline: list[TimelineEntry] = []
    facts: list[EntryFacts] = []
    cuts: dict[str, set[int]] = collections.defaultdict(set)
    seen_ids: set[Any] = set()
    last: str | None = None
    for entry in read_transcript_entries(path):
        timestamp = entry.get("timestamp")
        if timestamp:
            last = timestamp
        if entry.get("type") == "assistant":
            message = entry.get("message") or {}
            message_id = message.get("id")
            is_duplicate = message_id is not None and message_id in seen_ids
            if message_id is not None:
                seen_ids.add(message_id)
            stamp = timestamp or ""
            timeline.append(
                (stamp, is_duplicate, usage_values(message), model_key(message), message_id)
            )
            content = message.get("content") or []
            tool_ids = tuple(
                block.get("id")
                for block in (content if isinstance(content, list) else [])
                if isinstance(block, dict) and block.get("type") == "tool_use"
            )
            facts.append(
                EntryFacts(stamp, message_id, message.get("stop_reason") is not None, tool_ids)
            )
        if last is not None:
            cuts[last].add(len(timeline))
    cut_points = {stamp: tuple(sorted(counts)) for stamp, counts in cuts.items()}
    return TranscriptScan(timeline, facts, cut_points)


def build_prefix_sums(timeline: list[TimelineEntry]) -> PrefixSums:
    """Sorted timestamps plus running peak/first/raw totals, for as-of lookups.

    Timestamps are NOT monotonic in file order (a replayed turn keeps its
    original timestamp), so they are sorted here. Every total is
    order-independent over a set of entries - a sum, a sum over entries
    flagged in file order, or a per-id maximum built from increments - so
    sorting changes nothing but the lookup cost.
    """
    return _running_sums(sorted(timeline, key=lambda item: item[0]))


def build_file_order_sums(timeline: list[TimelineEntry]) -> PrefixSums:
    """Running peak/first/raw totals over the timeline in FILE order, so
    at_cut(sums, count) is what a hook that had read the first `count`
    assistant entries of the file counted (see scan_cut_points)."""
    return _running_sums(timeline)


def at_cut(sums: PrefixSums, count: int) -> AsOf:
    """Peak, first and raw totals after the first `count` entries of `sums`."""
    return AsOf(sums.peak[count], sums.first[count], sums.raw[count])


def scan_cut_points(path: str) -> dict[str, tuple[int, ...]]:
    """timestamp -> every count of assistant entries a hook had read, in
    FILE order, at a moment when the last timestamped line it had read
    carried that timestamp.

    This is the cut a hook really made. parse_transcript reads the file top
    to bottom and records as timestamp_end the timestamp of the last
    timestamped line it read - any line type (an attachment, a
    queue-operation, a pr-link), not only an assistant entry. Claude Code
    does not write lines in timestamp order, so the time cut (every
    assistant entry stamped at or before timestamp_end) can include an entry
    appended after the hook's read, or leave out one read before it.
    Measured 2026-09-29 over real session history: 24 rows matched no
    formula at the time cut - 11 missing an entry stamped earlier but
    written later, 13 holding one stamped later but written earlier - and
    all 24 matched exactly at one of these cuts.

    A timestamp can close several cuts: untimestamped lines after it leave
    it the last one read, and the same stamp can recur further down the
    file. The counts index the same entries scan_transcript and
    scan_entry_facts return, because all three come from one scan_unit.
    """
    return scan_unit(path).cut_points


def reproducing_cuts(
    stored: list[int], counts: tuple[int, ...], file_sums: PrefixSums
) -> list[int]:
    """The candidate cuts at which one of the three reconstructions - peak,
    first or raw - equals a row's stored tokens exactly. Tokens are the
    evidence of which cut the hook made: the formula they match says which
    hook wrote them, the cut they match says what it had read."""
    return [count for count in counts if stored in at_cut(file_sums, count)]


def _running_sums(ordered: list[TimelineEntry]) -> PrefixSums:
    stamps = [item[0] for item in ordered]
    peak: list[list[int]] = [[0] * 4]
    first: list[list[int]] = [[0] * 4]
    raw: list[list[int]] = [[0] * 4]
    peaks: dict[Any, tuple[int, ...]] = {}
    for _stamp, is_duplicate, values, _model, message_id in ordered:
        increment = usage_increment(peaks, message_id, values)
        peak.append([peak[-1][field_index] + increment[field_index] for field_index in range(4)])
        raw.append([raw[-1][field_index] + values[field_index] for field_index in range(4)])
        if is_duplicate:
            first.append(list(first[-1]))
        else:
            first.append([first[-1][field_index] + values[field_index] for field_index in range(4)])
    return PrefixSums(stamps, peak, first, raw)


def as_of(sums: PrefixSums, end_timestamp: str | None) -> AsOf:
    """Cumulative peak, first and raw totals for entries at or before a moment."""
    stamps = sums.stamps
    count = len(stamps) if end_timestamp is None else bisect.bisect_right(stamps, end_timestamp)
    return AsOf(sums.peak[count], sums.first[count], sums.raw[count])


def model_totals_as_of(timeline: list[TimelineEntry], end_timestamp: str | None) -> ModelMap:
    """Per-model cumulative totals for entries at or before a moment.

    The per-model counterpart of as_of's `peak`, and deliberately a linear
    scan rather than a prefix-sum table: a session owns a few dozen snapshots
    against a transcript of a few thousand entries, so the table would cost
    more to build (one dict copy per entry per model) than the scans it saves.

    Each message counts once at its per-field peak, through the same
    usage_increment the hooks use - so a replay cannot inflate the model
    whose turns were replayed, and a streamed message is not cut short at
    its first content block.
    """
    totals: ModelMap = {}
    peaks: dict[Any, tuple[int, ...]] = {}
    for stamp, _is_duplicate, values, model, message_id in timeline:
        if end_timestamp is not None and stamp > end_timestamp:
            continue
        first_seen = message_id is None or message_id not in peaks
        increment = usage_increment(peaks, message_id, values)
        # The same bucket rule as parse_transcript, so a rebuilt map has the
        # same keys as one the hook wrote.
        if not any(increment) and not (first_seen and model != SYNTHETIC_MODEL):
            continue
        bucket = totals.get(model)
        if bucket is None:
            bucket = totals[model] = dict.fromkeys(TOKEN_FIELDS, 0)
        for index, field in enumerate(TOKEN_FIELDS):
            bucket[field] += increment[index]
    return totals


def model_totals_at_row_cut(
    scan: TranscriptScan, file_sums: PrefixSums, record: dict[str, Any]
) -> ModelMap:
    """A row's per-model map at the cut its tokens say the hook made.

    That is the file cut (see scan_cut_points) whose PEAK equals the row's
    stored tokens - a per-model map is a peak-rule map, so it only belongs
    beside peak totals - when every such cut gives the same map. Otherwise
    (no such cut, or cuts that disagree on the map) it is the time cut, as
    model_totals_as_of gives it, and the caller's gates judge the result.
    Measured 2026-09-29: 12 native session maps the time cut over-counted,
    by the 1-3 entries written after the hook's last read, or at the wrong
    one of two cuts sharing a stamp, all matched exactly at this cut.

    `file_sums` is build_file_order_sums(scan.timeline), taken once per
    transcript by the caller.
    """
    end = record["timestamp_end"]
    stored = [record.get(field, 0) or 0 for field in TOKEN_FIELDS]
    maps = [
        model_totals_as_of(scan.timeline[:count], None)
        for count in scan.cut_points.get(end, ())
        if file_sums.peak[count] == stored
    ]
    if maps and all(item == maps[0] for item in maps):
        return maps[0]
    return model_totals_as_of(scan.timeline, end)


def token_sum(record: dict[str, Any]) -> int:
    return sum(record.get(field, 0) or 0 for field in TOKEN_FIELDS)


# The two formulas a stored row can be recognised as, and so corrected from.
RAW = "raw"
FIRST = "first"
# A tool_call_count an earlier run of this pass wrote at the time cut (see
# _tool_reading); it has no direction of its own.
TIME_CUT = "time_cut"
# Disposition keys a corrected row is counted under, by formula.
CORRECTED_REASONS = {
    RAW: "corrected: stored the raw sum (every entry)",
    FIRST: "corrected: stored the first-occurrence sum",
}


class CorrectionPlan(NamedTuple):
    """What plan_corrections_by_formula decided.

    `values` maps a row index to its corrected four totals; `formulas` names
    which prior formula each of those rows was recognised as (RAW or FIRST),
    which fixes the only direction its values may move in; `model_maps` holds
    each planned row's per-model map rebuilt by the same rule, for callers
    that rewrite tokens_by_model alongside the totals.
    """

    values: dict[int, list[int]]
    formulas: dict[int, str]
    model_maps: dict[int, ModelMap]
    stats: collections.Counter[str]
    factors: list[float]


def _recognise(stored: list[int], now: AsOf) -> str | None:
    """Which prior formula produced `stored`, or None if neither did.

    Exact matches only. The August run accepted a raw-sum near-miss within
    2% (58 of 59 such rows then sat within 0.3% of the raw sum), and those
    rows are corrected now. Extended to the first-occurrence formula, the
    same tolerance recognised 9 real session rows (2026-09-23) whose
    transcripts hold no streamed ids (first == peak) and which sat one late
    message below the as-of reconstruction - rows the hook got right, that a
    "correction" would have rewritten from reconstruction noise. Every row
    the 2026-09-23 plan corrects (862 subagent, 41 session) matches its
    formula exactly, so exactness costs nothing and closes that door for
    both formulas.
    """
    if stored == now.raw:
        return RAW
    if stored == now.first:
        return FIRST
    return None


# What a row reproducing the peak is read as: already correct.
PEAK = "peak"


def _recognise_or_peak(stored: list[int], now: AsOf) -> str | None:
    """PEAK when `stored` is already correct at `now`, else _recognise."""
    return PEAK if stored == now.peak else _recognise(stored, now)


def plan_corrections_by_formula(
    records: list[dict[str, Any]],
    transcripts: dict[str, str],
    cutoff: str,
    id_key: str = "session_id",
) -> CorrectionPlan:
    """Decide which units can be recomputed, from which formula, and to what.

    Every row's stored totals are compared, as of its own timestamp_end,
    with three reconstructions from its transcript: the correct PEAK total,
    and the two formulas earlier hooks wrote - the RAW sum of every entry,
    and the FIRST-occurrence sum. A row equal to the peak is already
    correct. A row recognised as raw or first is corrected to the peak, and
    the formula fixes the direction: a raw sum can only shrink (it counts
    every entry the peak counts, and more), a first-occurrence sum can only
    grow (the peak of an id is at least its first entry). Both are
    recognised only on an exact match, so neither can move the wrong way. A
    row recognised as neither refuses its unit.

    "As of its own timestamp_end" means the cut the hook made (see
    scan_cut_points): a file prefix whose last timestamped line carries
    that timestamp, chosen because one of the three reconstructions there
    reproduces the row's tokens exactly. Only when no such prefix does is
    the time cut - every entry stamped at or before timestamp_end - used,
    as it was before 2026-09-29. Two prefixes that both reproduce the
    tokens but disagree on the formula or the peak refuse the unit.

    Decisions are taken per UNIT - the rows sharing `id_key` - and applied to
    all of its rows or none of them; every skip reason is counted so the dry
    run shows exactly what was and was not touched. `transcripts` is keyed by
    that same id. For subagents.jsonl pass id_key="agent_id" (see
    UNIT_ID_KEYS): the session_id on a subagent row is its parent's.

    Two of the disposition labels below name the unit by noun ("session" or
    "agent") rather than hard-coding "session" - scripts/backfill-token-dedup.py
    prints these stats verbatim in its "Row disposition" report, and a
    --kind subagents run grouped by agent_id calling every one of its units
    a "session" there was simply wrong (2026-09-23 review).
    """
    unit_noun = "agent" if id_key == "agent_id" else "session"
    stats: collections.Counter[str] = collections.Counter()
    factors: list[float] = []
    plan: dict[int, list[int]] = {}
    formulas: dict[int, str] = {}
    model_maps: dict[int, ModelMap] = {}

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
            stats[f"untouched: {unit_noun} spans the cutoff"] += len(indexes)
            continue

        timeline, _facts, cut_points = scan_unit(transcript)
        sums = build_prefix_sums(timeline)
        file_sums = build_file_order_sums(timeline)
        proposals: dict[int, tuple[list[int], str, int | None]] = {}
        already_correct = 0
        refused: str | None = None
        for index in indexes:
            record = records[index]
            stored = [record.get(field, 0) or 0 for field in TOKEN_FIELDS]
            # The cut the hook made, when a file cut reproduces the tokens;
            # the time cut otherwise.
            counts = reproducing_cuts(
                stored, cut_points.get(record["timestamp_end"], ()), file_sums
            )
            cut: int | None = counts[0] if counts else None
            if cut is None:
                now = as_of(sums, record["timestamp_end"])
            else:
                now = at_cut(file_sums, cut)
                readings = {
                    (
                        _recognise_or_peak(stored, at_cut(file_sums, count)),
                        tuple(file_sums.peak[count]),
                    )
                    for count in counts
                }
                if len(readings) > 1:
                    # Two cuts the hook could have made disagree on whether
                    # the row is broken or on what it should hold: refuse
                    # rather than guess.
                    refused = f"untouched: {unit_noun} has a row whose file cut is ambiguous"
                    break
            if stored == now.peak:
                already_correct += 1
                continue
            formula = _recognise(stored, now)
            if formula is None:
                refused = f"untouched: {unit_noun} has a row matching neither formula"
                break
            # No direction refusal is needed: both formulas are recognised
            # exactly, a raw sum is at least the peak in every field (it
            # counts every entry the peak counts) and a first sum at most
            # (a message's peak is at least its first entry). verify_fields
            # still enforces each row's direction at write time.
            proposals[index] = (now.peak, formula, cut)

        if refused:
            stats[refused] += len(indexes)
            continue
        if not proposals:
            stats["untouched: already correct"] += len(indexes)
            continue

        stats["untouched: already correct"] += already_correct
        for index, (values, formula, cut) in proposals.items():
            stats[CORRECTED_REASONS[formula]] += 1
            plan[index] = values
            formulas[index] = formula
            # The map is rebuilt from the same cut as the totals.
            if cut is None:
                model_maps[index] = model_totals_as_of(timeline, records[index]["timestamp_end"])
            else:
                model_maps[index] = model_totals_as_of(timeline[:cut], None)
            before = sum(records[index].get(field, 0) or 0 for field in TOKEN_FIELDS)
            # A planned row's peak is never zero: a zero peak means every
            # entry up to that moment is zero, so the raw and first sums are
            # zero too, and a row equal to either is equal to the peak -
            # already correct, never planned. The guard stays for the
            # division.
            after = sum(values)
            if after:  # pragma: no branch - see the comment above
                factors.append(before / after)
    return CorrectionPlan(plan, formulas, model_maps, stats, factors)


def plan_corrections(
    records: list[dict[str, Any]],
    transcripts: dict[str, str],
    cutoff: str,
    id_key: str = "session_id",
) -> tuple[dict[int, list[int]], collections.Counter[str], list[float]]:
    """(plan, stats, factors) of plan_corrections_by_formula - the values
    each row is corrected to, without the formula and per-model detail."""
    result = plan_corrections_by_formula(records, transcripts, cutoff, id_key)
    return result.values, result.stats, result.factors


# --- non-token fields --------------------------------------------------------
#
# The token correction above rewrites only the four token fields. Two other
# fields of a historical row can be rebuilt from the same transcript, as of
# the same timestamp_end, each in its own explicitly requested pass
# (scripts/backfill-token-dedup.py --fields): tool_call_count, which every
# hook before 2026-09-23 undercounted, and incomplete_usage_messages, which
# no hook before 2026-09-25 recorded.


def scan_entry_facts(path: str) -> list[EntryFacts]:
    """Every assistant entry of a transcript, in FILE order - the order the
    hook read them in, which decides first occurrences."""
    return scan_unit(path).facts


def _entries_as_of(facts: list[EntryFacts], end_timestamp: str | None) -> list[EntryFacts]:
    """The entries at or before a moment, still in file order - what a hook
    that wrote a row at `end_timestamp` had read (a replay keeps its original
    timestamp, so it sorts with the turn it repeats)."""
    if end_timestamp is None:
        return list(facts)
    return [fact for fact in facts if fact.stamp <= end_timestamp]


class ToolCounts(NamedTuple):
    """tool_call_count by each rule a hook has used.

    `distinct` is the current rule (transcript_ingest.parse_transcript): each
    tool_use block id once, wherever in the message's entries it appears; a
    block without an id on its message's first entry only. `first` is the
    2026-08-11 hook's: the blocks of each message's first entry only, which
    misses every block a streamed message writes in a later entry. `raw` is
    the original hook's: every block of every entry, replays included.
    raw >= distinct >= first always, so a row recognised as raw can only
    fall and one recognised as first can only rise.
    """

    distinct: int
    first: int
    raw: int


def tool_calls_as_of(facts: list[EntryFacts], end_timestamp: str | None) -> ToolCounts:
    distinct = first = raw = 0
    seen_messages: set[Any] = set()
    seen_tools: set[Any] = set()
    for fact in _entries_as_of(facts, end_timestamp):
        first_occurrence = fact.message_id is None or fact.message_id not in seen_messages
        if fact.message_id is not None:
            seen_messages.add(fact.message_id)
        raw += len(fact.tool_ids)
        if first_occurrence:
            first += len(fact.tool_ids)
        for tool_id in fact.tool_ids:
            if tool_id is None:
                distinct += first_occurrence
            elif tool_id not in seen_tools:
                seen_tools.add(tool_id)
                distinct += 1
    return ToolCounts(distinct, first, raw)


def incomplete_as_of(facts: list[EntryFacts], end_timestamp: str | None) -> int:
    """incomplete_usage_messages as the current hook would have written it at
    `end_timestamp` - through the hook's own count_incomplete."""
    order: list[Any] = []
    seen: set[Any] = set()
    finalised: set[Any] = set()
    for fact in _entries_as_of(facts, end_timestamp):
        if fact.message_id is None:
            continue
        if fact.message_id not in seen:
            seen.add(fact.message_id)
            order.append(fact.message_id)
        if fact.finalised:
            finalised.add(fact.message_id)
    return count_incomplete(order, finalised)


class FieldPlan(NamedTuple):
    """What a field pass decided: each planned row index's new value, the
    formula it was recognised as (tool calls only), and every disposition."""

    values: dict[int, int]
    formulas: dict[int, str]
    stats: collections.Counter[str]


def _eligible_units(
    records: list[dict[str, Any]],
    transcripts: dict[str, str],
    cutoff: str,
    id_key: str,
    stats: collections.Counter[str],
) -> list[tuple[list[int], str]]:
    """(row indexes, transcript) of every unit a field pass may look at, with
    the same two unit-level refusals as the token correction: no transcript
    on disk, or any row without a timestamp_end or at/after the cutoff."""
    unit_noun = "agent" if id_key == "agent_id" else "session"
    rows_by_unit: dict[Any, list[int]] = collections.defaultdict(list)
    for index, record in enumerate(records):
        rows_by_unit[record.get(id_key)].append(index)
    units = []
    for unit_id, indexes in rows_by_unit.items():
        transcript = transcripts.get(unit_id)
        if transcript is None:
            stats["untouched: no transcript on disk"] += len(indexes)
        elif any(
            not records[index].get("timestamp_end") or records[index]["timestamp_end"] >= cutoff
            for index in indexes
        ):
            stats[f"untouched: {unit_noun} spans the cutoff"] += len(indexes)
        else:
            units.append((indexes, transcript))
    return units


TOOL_CALL_REASONS = {
    RAW: "corrected: stored the every-entry count",
    FIRST: "corrected: stored the first-entry count",
    TIME_CUT: "corrected: stored an earlier backfill's time-cut count",
}


def _stored_tokens(record: dict[str, Any]) -> list[int]:
    return [record.get(field, 0) or 0 for field in TOKEN_FIELDS]


def _tool_reading(
    stored: Any, now: ToolCounts, time_cut_count: int | None = None
) -> tuple[int, str | None] | None:
    """(the count the row should hold, the formula it was recognised as or
    None when it already holds it), or None when it matches no rule.

    `time_cut_count` is the distinct count at the time cut, passed only when
    `now` is a file cut: before 2026-09-29 this pass rebuilt every row at the
    time cut, so a row it wrote holds that count - which is neither hook
    formula at the file cut when the two cuts disagree. Measured on the
    2026-09-28 apply: 2 session rows, 149 and 152 where the file cut gives
    148 and 151 (their hook values, 31, were the first-entry count at both
    cuts). Only an exact match is recognised, and only after both hook
    formulas, so a value a hook wrote is always read as the hook's. The
    caller passes it only when the move is explained (see
    _explained_time_cut_count).
    """
    if stored == now.distinct:
        return (now.distinct, None)
    if stored == now.raw:
        return (now.distinct, RAW)
    if stored == now.first:
        return (now.distinct, FIRST)
    if time_cut_count is not None and stored == time_cut_count:
        return (now.distinct, TIME_CUT)
    return None


def _explained_time_cut_count(
    facts: list[EntryFacts], count: int, end_timestamp: str
) -> int | None:
    """The distinct tool count at the time cut, or None when the entries
    lying between it and the file cut after `count` entries do not explain
    how it differs.

    The time cut is trusted only when it IS a file prefix: the first `size`
    entries, where `size` is how many are stamped at or before
    `end_timestamp` - none of them stamped later. The two cuts then differ
    exactly by the entries between the two positions, and by the distinct
    tool_use blocks those entries carry, in either direction. That is the
    measured mechanism behind the 2026-09-28 rows (one in-flight message
    written just below the hook's last read). A time cut that takes an
    entry further down while skipping one between the cuts can match a
    stored count by coincidence - even with the same number of distinct
    calls as the prefix of its size - and is not trusted.

    The entries between the cuts must also belong to ONE message, as the
    measured ones did: past that the move is not the known mechanism (40
    entries of 40 messages stamped at the end could otherwise move a count
    from 41 to 1). An entry without a message id is a message of its own.
    """
    size = sum(1 for fact in facts if fact.stamp <= end_timestamp)
    if any(fact.stamp > end_timestamp for fact in facts[:size]):
        return None
    low, high = sorted((count, size))
    messages = {
        fact.message_id if fact.message_id is not None else ("no id", position)
        for position, fact in enumerate(facts[low:high])
    }
    if len(messages) > 1:
        return None
    return tool_calls_as_of(facts[:size], None).distinct


def plan_tool_call_corrections(
    records: list[dict[str, Any]],
    transcripts: dict[str, str],
    cutoff: str,
    id_key: str = "session_id",
) -> FieldPlan:
    """Which rows' tool_call_count to rebuild by the current rule, and to what.

    The same evidence rule as the token correction: a row is rewritten only
    when its stored count EXACTLY equals, as of its own timestamp_end, the
    count one of the two earlier hooks would have written - proof of which
    hook wrote it, and that the reconstruction models it. A row equal to the
    current rule is left alone (so a second run is a no-op); a row matching
    none refuses its whole unit, and a unit is corrected all or nothing, so
    one agent's series never mixes two rules.

    "As of its own timestamp_end" is the cut the row's TOKENS say the hook
    made: the file prefix (see scan_cut_points) at which a token formula
    reproduces them exactly, else the time cut. A tool_use block can sit in
    an entry that carries no new usage, so the time cut can count a tool
    call the hook had not read even where the tokens agree - measured
    2026-09-29, 7 real rows whose time and file cuts disagreed on the target.
    Prefixes that reproduce the tokens but disagree on the count, or on
    whether the row matches at all, refuse the unit.
    """
    unit_noun = "agent" if id_key == "agent_id" else "session"
    stats: collections.Counter[str] = collections.Counter()
    values: dict[int, int] = {}
    formulas: dict[int, str] = {}
    for indexes, transcript in _eligible_units(records, transcripts, cutoff, id_key, stats):
        timeline, facts, cut_points = scan_unit(transcript)
        file_sums = build_file_order_sums(timeline)
        proposals: dict[int, tuple[int, str]] = {}
        refused: str | None = None
        for index in indexes:
            record = records[index]
            stored = record.get("tool_call_count")
            end = record["timestamp_end"]
            counts = reproducing_cuts(_stored_tokens(record), cut_points.get(end, ()), file_sums)
            if counts:
                readings = {
                    _tool_reading(
                        stored,
                        tool_calls_as_of(facts[:count], None),
                        _explained_time_cut_count(facts, count, end),
                    )
                    for count in counts
                }
            else:
                readings = {_tool_reading(stored, tool_calls_as_of(facts, end))}
            if len(readings) > 1:
                refused = "has a row whose file cut is ambiguous"
                break
            (reading,) = readings
            if reading is None:
                refused = "has a row matching no formula"
                break
            value, formula = reading
            if formula is not None:
                proposals[index] = (value, formula)
        if refused:
            stats[f"untouched: {unit_noun} {refused}"] += len(indexes)
            continue
        stats["untouched: already correct"] += len(indexes) - len(proposals)
        for index, (value, formula) in proposals.items():
            stats[TOOL_CALL_REASONS[formula]] += 1
            values[index] = value
            formulas[index] = formula
    return FieldPlan(values, formulas, stats)


def plan_incomplete_usage(
    records: list[dict[str, Any]],
    transcripts: dict[str, str],
    cutoff: str,
    id_key: str = "session_id",
) -> FieldPlan:
    """Which rows gain incomplete_usage_messages, and with what count.

    There is no earlier formula to recognise - no hook wrote the key before
    2026-09-25 - so the evidence that the as-of reconstruction models a row
    is its TOKENS: every row of the unit must equal the transcript's as-of
    peak total exactly, or the unit is refused. "As-of" is the cut those
    tokens say the hook made (see plan_corrections_by_formula), and the
    count is taken at that same cut; prefixes that reproduce the tokens but
    disagree on the count refuse the unit. A row that already carries
    the key must carry what the transcript gives, or the unit is refused.
    Only positive counts are added (absent means zero, as the hooks write
    it), so a row with nothing to flag is left byte-for-byte alone.
    """
    unit_noun = "agent" if id_key == "agent_id" else "session"
    stats: collections.Counter[str] = collections.Counter()
    values: dict[int, int] = {}
    for indexes, transcript in _eligible_units(records, transcripts, cutoff, id_key, stats):
        timeline, facts, cut_points = scan_unit(transcript)
        sums = build_prefix_sums(timeline)
        file_sums = build_file_order_sums(timeline)
        proposals: dict[int, int] = {}
        refusal = None
        for index in indexes:
            record = records[index]
            stored_tokens = _stored_tokens(record)
            end = record["timestamp_end"]
            # (whether the tokens are the peak there, the count there), at
            # the cut the tokens say the hook made - see the token pass.
            counts = reproducing_cuts(stored_tokens, cut_points.get(end, ()), file_sums)
            if counts:
                readings = {
                    (stored_tokens == file_sums.peak[cut], incomplete_as_of(facts[:cut], None))
                    for cut in counts
                }
            else:
                readings = {(stored_tokens == as_of(sums, end).peak, incomplete_as_of(facts, end))}
            if len(readings) > 1:
                refusal = "has a row whose file cut is ambiguous"
                break
            ((reproduced, count),) = readings
            if not reproduced:
                refusal = "has a row whose tokens the transcript does not reproduce"
                break
            if "incomplete_usage_messages" in record:
                if record["incomplete_usage_messages"] != count:
                    refusal = "has a recorded count the transcript does not reproduce"
                    break
            elif count:
                proposals[index] = count
        if refusal:
            stats[f"untouched: {unit_noun} {refusal}"] += len(indexes)
            continue
        stats["untouched: already correct"] += len(indexes) - len(proposals)
        if proposals:
            stats["flagged: messages without a final usage entry"] += len(proposals)
            values.update(proposals)
    return FieldPlan(values, {}, stats)


def apply_field_plan(
    records: list[dict[str, Any]], field: str, values: dict[int, int]
) -> list[dict[str, Any]]:
    """The records with `field` set on each planned row - replaced in place
    where the row has it, appended where it does not. Unplanned rows are the
    same objects; planned ones are copies."""
    corrected = []
    for index, record in enumerate(records):
        if index in values:
            updated = dict(record)
            updated[field] = values[index]
            corrected.append(updated)
        else:
            corrected.append(record)
    return corrected


def row_identities(records: list[dict[str, Any]], indexes: Iterable[int], id_key: str) -> list[str]:
    """A stable name for each of the rows at `indexes`, in that order.

    A row is named by its unit (`id_key`), its timestamp_end and its
    ordinal among the unit's earlier rows with the same timestamp_end in
    file order - never by a value a pass may change, so a row keeps its
    name when it is corrected, and rows appended later do not rename it.
    Hashed, so the state file holds no session ids.
    """
    ordinals: dict[int, int] = {}
    seen: collections.Counter[tuple[Any, Any]] = collections.Counter()
    for index, record in enumerate(records):
        moment = (record.get(id_key), record.get("timestamp_end"))
        ordinals[index] = seen[moment]
        seen[moment] += 1
    identities = []
    for index in indexes:
        record = records[index]
        name = json.dumps([record.get(id_key), record.get("timestamp_end"), ordinals[index]])
        identities.append(sha256_bytes(name.encode("utf-8"))[:16])
    return identities


def write_field_state(
    kind: str,
    field: str,
    cutoff: str,
    applied_at: str,
    identities: list[str],
    path: str | None = None,
) -> dict[str, Any]:
    """Record a successful field pass under state["fields"]["<kind>.<field>"],
    leaving the token entries - the ones the dashboards read - untouched.

    `identities` names the rows this run changed (see row_identities). The
    entry's keys:

    - `cutoff`, `applied_at`: this run's.
    - `corrected_rows`: CUMULATIVE over every run of this kind and field -
      the distinct rows ever corrected, counted by name, plus
      `unidentified_earlier_rows` when there are any.
    - `rows_changed_this_run`: how many rows this run changed.
    - `corrected_row_ids`: the names of every row any run changed, sorted.
    - `unidentified_earlier_rows`: present only when the entry was first
      written before 2026-09-29, when runs recorded a count and no names.
      That count is carried here, and while it is present
      `corrected_rows` may count a row twice: which of those rows a later
      run touched again cannot be told.

    Adding each run's count, as every entry did before 2026-09-29, counted
    a row corrected by two runs twice: the tool_calls pass re-corrected 2
    session rows its 2026-09-28 run had written, and the state said 2034
    rows where 2032 distinct ones had changed. An old entry that reported
    no rows (0 or no count) leaves nothing unidentified.
    """
    path = path or BACKFILL_STATE_FILE
    existing = load_backfill_state(path)
    state: dict[str, Any] = existing if isinstance(existing, dict) else {}
    fields = state.get("fields")
    if not isinstance(fields, dict):
        fields = state["fields"] = {}
    key = f"{kind}.{field}"
    previous = fields.get(key)
    names = set(identities)
    unidentified = 0
    if isinstance(previous, dict):
        earlier = previous.get("corrected_row_ids")
        if isinstance(earlier, list):
            names.update(earlier)
            unidentified = previous.get("unidentified_earlier_rows") or 0
        else:
            unidentified = previous.get("corrected_rows") or 0
    entry: dict[str, Any] = {
        "cutoff": cutoff,
        "applied_at": applied_at,
        "corrected_rows": unidentified + len(names),
        "rows_changed_this_run": len(identities),
        "corrected_row_ids": sorted(names),
    }
    if unidentified:
        entry["unidentified_earlier_rows"] = unidentified
    fields[key] = entry
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w") as handle:
        json.dump(state, handle, indent=2)
    return state


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
        snapshots = sorted(snapshots, key=lambda snapshot: snapshot.get("duration_seconds") or 0)
        previous = [0] * 4
        for snapshot in snapshots:
            current = [snapshot.get(field, 0) or 0 for field in TOKEN_FIELDS]
            if any(current[field_index] < previous[field_index] for field_index in range(4)):
                offenders.add(session_id)
                break
            previous = current
    return offenders


def apply_plan(
    records: list[dict[str, Any]],
    plan: dict[int, list[int]],
    model_maps: dict[int, ModelMap] | None = None,
) -> list[dict[str, Any]]:
    """The records with each planned row's four totals replaced.

    A planned row's tokens_by_model was computed against the totals being
    replaced, so it is wrong afterwards. Where `model_maps` holds that row's
    map rebuilt by the same rule as the new totals, it replaces the old one
    in place (same key position); otherwise the key is dropped, because
    absent is how this schema spells "unknown". A row that had no map does
    not gain one here - filling maps is scripts/backfill-tokens-by-model.py's
    job, behind its own gates.
    """
    corrected = []
    for index, record in enumerate(records):
        if index in plan:
            updated = dict(record)
            for position, field in enumerate(TOKEN_FIELDS):
                updated[field] = plan[index][position]
            if "tokens_by_model" in updated:
                if model_maps is not None and index in model_maps:
                    updated["tokens_by_model"] = model_maps[index]
                else:
                    del updated["tokens_by_model"]
            corrected.append(updated)
        else:
            corrected.append(record)
    return corrected


def daily_totals(records: list[dict[str, Any]], days: int | None) -> list[tuple[str, int]]:
    """Per-day token totals through the same code path the trend chart uses."""
    totals: collections.Counter[str] = collections.Counter()
    for delta in expand_records_to_daily_deltas([dict(record) for record in records]):
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
    may_increase: frozenset[int] = frozenset(),
    allow_changed_keys: frozenset[str] = frozenset(),
) -> list[str]:
    """Every non-token field must survive untouched, tokens may only drop.

    A key in allow_new_keys may appear where it was absent; a key in
    allow_removed_keys may disappear where it was present. Each direction is
    declared separately and grants only that direction - allowing a key's
    addition does not also allow its removal, nor the reverse.

    `may_increase` names row indexes (0-based) whose tokens may only RISE
    instead - rows recognised as first-occurrence sums, which undercount -
    so every row still has exactly one permitted direction. A key in
    allow_changed_keys may change its value, but not appear, disappear or
    move.
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
                if index in may_increase:
                    if (new[key] or 0) < (old[key] or 0):
                        problems.append(f"line {index + 1}: {key} decreased")
                elif (new[key] or 0) > (old[key] or 0):
                    problems.append(f"line {index + 1}: {key} increased")
            elif key in allow_changed_keys:
                continue
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
        with Path(path).open() as state_file:
            return cast("dict[str, Any]", json.load(state_file))
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
    subagents run nests its entry under a "subagents" key. Each kind touches
    only its own entry, so neither run erases the other's record. A file that
    does not hold a dict is replaced rather than merged into.

    A later run on a kind that already has an entry EXTENDS it: it corrects
    only what earlier runs left, so its rows are added to theirs and the
    retention boundary stays at the earliest row any run corrected. An
    earlier entry that never recorded that boundary (the state predates the
    key) makes it unknown for good - the later run's own earliest row is not
    the boundary of rows it never saw. Cutoff, time and the uncorrectable
    count describe the file as of this run, so they are replaced.
    """
    path = path or BACKFILL_STATE_FILE
    existing = load_backfill_state(path)
    state: dict[str, Any] = existing if isinstance(existing, dict) else {}
    previous = state.get("subagents") if kind == "subagents" else state
    if isinstance(previous, dict) and isinstance(previous.get("applied_at"), str):
        corrected_rows += previous.get("corrected_rows") or 0
        earlier = previous.get("earliest_corrected")
        if not isinstance(earlier, str):
            earliest_corrected = None
        elif earliest_corrected is None or earlier < earliest_corrected:
            earliest_corrected = earlier
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
    with target.open("w") as state_file:
        json.dump(state, state_file, indent=2)
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
