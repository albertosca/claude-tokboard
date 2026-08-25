"""Loading, deduping, normalizing, and filtering raw session/subagent
records - the data-ingestion layer shared by both dashboard entry
points."""

import collections
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

METRICS_FILE = str(Path("~/.claude/metrics/sessions.jsonl").expanduser())
SUBAGENTS_FILE = str(Path("~/.claude/metrics/subagents.jsonl").expanduser())


def load_records(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not Path(path).exists():
        return records
    with Path(path).open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def dedupe_by_key(records: list[dict[str, Any]], key_field: str) -> list[dict[str, Any]]:
    # metrics-log.py / subagent-metrics-log.py append a fresh cumulative
    # snapshot on every Stop/SubagentStop event during a session or
    # subagent's life, not just once at the end - a long/busy one can log
    # many rows sharing one id, each with growing
    # duration_seconds/tokens/turn_count. Every downstream consumer here
    # (trend chart day totals, Panorama project totals, the composition
    # legend, the subagent detail table) just sums/lists records, so
    # without this dedup real usage gets counted once per snapshot logged
    # - inflating every total by however many times that id got logged
    # (found 2026-07-21: one session had 33 duplicate rows; 17 subagent
    # ids had up to 4). Keep the snapshot with the largest
    # duration_seconds per id, first-seen order otherwise.
    best_by_id: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    no_id_records: list[dict[str, Any]] = []
    for r in records:
        key = r.get(key_field)
        if key is None:
            no_id_records.append(r)
            continue
        if key not in best_by_id:
            order.append(key)
            best_by_id[key] = r
        elif (r.get("duration_seconds") or 0) >= (best_by_id[key].get("duration_seconds") or 0):
            best_by_id[key] = r
    return [best_by_id[key] for key in order] + no_id_records


def normalize_project_paths(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # A session's project_path is whatever directory Claude Code's cwd
    # happened to be, not necessarily the project's real root - working
    # from a subdirectory (2026-07-21: found 45 sessions logged from
    # .../claude-metrics-dashboard/hooks instead of
    # .../claude-metrics-dashboard, 1356 sessions strong) creates a
    # spurious extra "project" for a folder that isn't one. Roll a path
    # up to its IMMEDIATE parent when that parent is ALSO a project_path
    # present in the dataset - if not, the path is left alone (most
    # projects/paths are real, standalone roots).
    #
    # "Parent is also a known path" alone is NOT enough of a signal, and
    # two broken attempts at this found out why the hard way:
    #   1. Walking up to the nearest matching ancestor at ANY depth: found
    #      /Users/username (33 sessions) and /Users/username/Programming
    #      (205 sessions) are THEMSELVES real project_paths (one-off
    #      sessions that never cd'ed anywhere specific) - every project
    #      merely NESTED under either got swallowed into that one bucket.
    #   2. Limiting to one level up didn't fix it either: EVERY project
    #      directly under ~/Programming shares that same immediate
    #      parent, and "Programming" being a known path made ALL of them
    #      collapse into it (2114 of ~2500 sessions ended up there,
    #      wiping out almost every real project in the dashboard).
    # The actual distinguishing signal is fan-out: a real subdirectory
    # mistake (.../claude-metrics-dashboard/hooks) is the ONLY child its
    # parent has ever had recorded. A generic container directory
    # (~/Programming, home) has DOZENS of different children - that's
    # what makes it a container, not a project. Only collapse a child
    # into its immediate parent when that parent has a small number of
    # distinct children total (<=2) - i.e. it looks like "one project
    # occasionally logged from a subdirectory," not "many unrelated
    # projects that happen to share a folder."
    #
    # Worktree paths are excluded: they're intentionally kept as their
    # own path here and merged later, by color_group_key, which
    # preserves the "parent / worktree-name" label instead of collapsing
    # to a bare parent name.
    known_paths: set[str] = {
        cast(str, r.get("project_path")) for r in records if r.get("project_path")
    }
    marker = "/.claude/worktrees/"
    max_children_to_collapse = 2

    parent_of: dict[str, str] = {}
    for path in known_paths:
        if marker in path:
            continue
        parent = str(Path(path.rstrip("/")).parent)
        if parent and parent in known_paths:
            parent_of[path] = parent

    children_count = collections.Counter(parent_of.values())

    canonical: dict[str, str] = {}
    for path in known_paths:
        if marker in path:
            canonical[path] = path
            continue
        collapse_parent = parent_of.get(path)
        if collapse_parent and children_count[collapse_parent] <= max_children_to_collapse:
            canonical[path] = collapse_parent
        else:
            canonical[path] = path
    for r in records:
        p = r.get("project_path")
        if p in canonical and canonical[p] != p:
            r["project_path"] = canonical[p]
    # A session cwd'd into a scratchpad/tmp dir isn't a "project" (2026-07-21:
    # "tem um scratchpad que não faço ideia do que seja") - fold these into
    # the same "sem projeto" bucket None already means, rather than giving
    # every random ephemeral path its own legend entry.
    for r in records:
        p = r.get("project_path")
        if p and is_ephemeral_path(p):
            r["project_path"] = None
    return records


def is_ephemeral_path(path: str) -> bool:
    normalized = path.rstrip("/")
    return (
        normalized.startswith("/tmp/")
        or normalized.startswith("/private/tmp/")
        or "/scratchpad" in normalized
    )


def dedupe_sessions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return dedupe_by_key(records, "session_id")


def dedupe_subagents(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return dedupe_by_key(records, "agent_id")


def filter_records(
    records: list[dict[str, Any]], tag: str | None = None, project: str | None = None
) -> list[dict[str, Any]]:
    filtered = records
    if tag is not None:
        filtered = [r for r in filtered if r.get("tag") == tag]
    if project is not None:
        filtered = [r for r in filtered if r.get("project_path") == project]
    return filtered


def filter_by_days(records: list[dict[str, Any]], days: int | None) -> list[dict[str, Any]]:
    if days is None:
        return records
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    filtered = []
    for r in records:
        timestamp_start = r.get("timestamp_start")
        if not timestamp_start:
            continue
        try:
            dt = datetime.fromisoformat(timestamp_start.replace("Z", "+00:00")).astimezone()
        except (ValueError, AttributeError, TypeError):
            continue
        if dt >= cutoff:
            filtered.append(r)
    return filtered


def _parse_moment(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError, TypeError):
        return None


def filter_sessions_by_window(
    records: list[dict[str, Any]], window: tuple[datetime, datetime] | None
) -> list[dict[str, Any]]:
    # A session belongs to a period when its ACTIVE SPAN OVERLAPS the
    # window, not when it started inside it. Alberto keeps sessions open
    # for weeks - one running since 2026-07-20 was still logging on
    # 08-10 - so a start-date test would empty the session list for
    # "ontem" while the trend chart, which is built from per-snapshot
    # deltas, correctly showed yesterday's work. Two surfaces, two
    # answers about one period, which the design forbids.
    #
    # A record with no end (never seen in real data, but a hook killed
    # mid-write could produce one) falls back to being a point at its
    # start rather than being silently dropped.
    if window is None:
        return records
    window_start, window_end = window
    kept: list[dict[str, Any]] = []
    for record in records:
        start = _parse_moment(record.get("timestamp_start"))
        if start is None:
            continue
        end = _parse_moment(record.get("timestamp_end")) or start
        if start < window_end and end >= window_start:
            kept.append(record)
    return kept


def filter_deltas_by_window(
    records: list[dict[str, Any]], window: tuple[datetime, datetime] | None
) -> list[dict[str, Any]]:
    # The counterpart for expand_records_to_daily_deltas' output, where
    # each record is a single dated chunk of usage rather than a span -
    # so a plain point-in-window test is the correct one. Note the field
    # read here is timestamp_start, which for these records holds the
    # snapshot's END time; see expand_records_to_daily_deltas.
    if window is None:
        return records
    window_start, window_end = window
    kept: list[dict[str, Any]] = []
    for record in records:
        moment = _parse_moment(record.get("timestamp_start"))
        if moment is not None and window_start <= moment < window_end:
            kept.append(record)
    return kept


def record_token_total(record: dict[str, Any]) -> int:
    # Every token a record consumed, all four kinds. Cache dominates real
    # usage by orders of magnitude - measured 2026-08-10 against Claude
    # Code's own session transcripts, input+output was ~0.3% of the day's
    # true volume (4.2M shown against 1.42B actually spent). Any view that
    # calls its number a token "total" has to use this, or it reports a
    # rounding error as if it were the whole story.
    return (
        (record.get("input_tokens", 0) or 0)
        + (record.get("output_tokens", 0) or 0)
        + (record.get("cache_read_tokens", 0) or 0)
        + (record.get("cache_creation_tokens", 0) or 0)
    )


def compute_token_type_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "series-1": sum(r.get("input_tokens", 0) or 0 for r in records),
        "series-2": sum(r.get("output_tokens", 0) or 0 for r in records),
        "series-3": sum(r.get("cache_read_tokens", 0) or 0 for r in records),
        "series-4": sum(r.get("cache_creation_tokens", 0) or 0 for r in records),
    }


def expand_records_to_daily_deltas(raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # The Stop/SubagentStop hooks append a fresh CUMULATIVE snapshot every
    # turn, not one row per session/subagent - dedupe_sessions/
    # dedupe_subagents pick a single representative (largest
    # duration_seconds) for every other purpose, which is correct for
    # all-time totals but wrong for day-by-day charts: that single row's
    # timestamp_start is fixed at the session's START, so a long-running
    # session's ENTIRE cumulative total lands on one day bucket, however
    # long it actually ran. Found 2026-08-06: 77 of 4518 sessions (43 of
    # 1034 subagents) span multiple snapshots, the longest running 33.8
    # days - each one dumped its whole multi-million-token total onto a
    # single day up to a month in the past, making any given week look
    # artificially quiet no matter how much real usage happened inside
    # one of these long sessions.
    #
    # This must run on RAW (pre-dedupe) records - by the time
    # dedupe_sessions/dedupe_subagents runs, every snapshot but one is
    # already gone. Call this BEFORE dedup, on its own copy of the raw
    # list; the dashboards' regular deduped `records`/`chart_records`
    # pipeline (used by the composition legend, project rankings, and
    # every per-session row) is untouched and still correct - only the
    # day-based trend chart needs this expanded, delta-attributed view.
    #
    # Confirmed against real data before this was written: token counts
    # are monotonically non-decreasing across a session/subagent's
    # snapshots (no resets), and sorting by duration_seconds gives the
    # exact same order as sorting by timestamp_end - safe to reuse the
    # same sort key dedupe_by_key already relies on.
    token_fields = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
    # agent_id checked FIRST: subagent records carry BOTH their own
    # agent_id AND their parent session's session_id - checking
    # session_id first would group every subagent dispatched by the same
    # session together as if they were sequential snapshots of one
    # entity, corrupting deltas across unrelated subagents (found
    # 2026-08-06: 1012 of 1036 agent_ids mismatched dedup's own totals
    # this way on real data). Session records never carry agent_id at
    # all, so they always fall through to session_id correctly.
    id_fields = ("agent_id", "session_id")
    by_id: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for record in raw_records:
        record_id: tuple[str, Any] | None = None
        for field in id_fields:
            value = record.get(field)
            if value is not None:
                record_id = (field, value)
                break
        if record_id is None:
            continue
        by_id.setdefault(record_id, []).append(record)

    deltas: list[dict[str, Any]] = []
    for snapshots in by_id.values():
        snapshots.sort(key=lambda r: r.get("duration_seconds") or 0)
        previous = dict.fromkeys(token_fields, 0)
        for snapshot in snapshots:
            delta_record = dict(snapshot)
            for field in token_fields:
                current = snapshot.get(field, 0) or 0
                # max(..., 0): a defensive floor, not expected to trigger -
                # every snapshot checked in real data was monotonically
                # non-decreasing, but a future hook change or a corrupted
                # row shouldn't be able to produce a negative delta.
                delta_record[field] = max(current - previous[field], 0)
                previous[field] = current
            # Dated by THIS snapshot's own timestamp_end (when this chunk
            # of usage was actually recorded), not the session's fixed
            # timestamp_start - that's the entire point of this function.
            delta_record["timestamp_start"] = snapshot.get("timestamp_end") or snapshot.get(
                "timestamp_start"
            )
            deltas.append(delta_record)
    return deltas


def fold_subagent_tokens_into_sessions(
    records: list[dict[str, Any]], subagent_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    # A session's own record only counts tokens spent on its own direct
    # messages - every subagent it dispatched burns real quota too, but
    # lives in a separate record (subagent_records) linked only by
    # session_id. Without this, every aggregate view (composition
    # legend, trend chart, project totals, even each session row's own
    # total) silently excludes subagent usage - found 2026-08-05 when
    # a heavy subagent-driven-development week made the dashboard show
    # well under half of real token consumption (subagents outweighed
    # direct session tokens 1.3x in that window). Mutates records
    # in-place so every list derived from the same dicts earlier in the
    # pipeline (all_time_records, chart_records, the day/limit-filtered
    # records) picks up the fold automatically.
    token_fields = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
    subagent_totals_by_session: dict[Any, dict[str, int]] = {}
    for subagent in subagent_records:
        session_id = subagent.get("session_id")
        if session_id is None:
            continue
        totals = subagent_totals_by_session.setdefault(session_id, dict.fromkeys(token_fields, 0))
        for field in token_fields:
            totals[field] += subagent.get(field, 0) or 0
    for record in records:
        session_totals = subagent_totals_by_session.get(record.get("session_id"))
        if not session_totals:
            continue
        for field in token_fields:
            record[field] = (record.get(field, 0) or 0) + session_totals[field]
    return records
