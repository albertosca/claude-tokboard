"""Loading, deduping, normalizing, and filtering raw session/subagent
records - the data-ingestion layer shared by both dashboard entry
points."""

from __future__ import annotations

import collections
import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

from .paths import split_worktree, worktree_marker_of
from .transcript_ingest import TOKEN_FIELDS

METRICS_FILE = str(Path("~/.claude/metrics/sessions.jsonl").expanduser())
SUBAGENTS_FILE = str(Path("~/.claude/metrics/subagents.jsonl").expanduser())


def load_records(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not Path(path).exists():
        return records
    with Path(path).open() as records_file:
        for line in records_file:
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
    # (found 2026-07-21: one session had dozens of duplicate rows, and
    # subagent ids had several). Keep the snapshot with the largest
    # duration_seconds per id, first-seen order otherwise.
    best_by_id: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    no_id_records: list[dict[str, Any]] = []
    for record in records:
        key = record.get(key_field)
        if key is None:
            no_id_records.append(record)
            continue
        if key not in best_by_id:
            order.append(key)
            best_by_id[key] = record
        elif (record.get("duration_seconds") or 0) >= (
            best_by_id[key].get("duration_seconds") or 0
        ):
            best_by_id[key] = record
    return [best_by_id[key] for key in order] + no_id_records


def normalize_project_paths(
    records: list[dict[str, Any]], project_roots: Sequence[str] = ()
) -> list[dict[str, Any]]:
    # A session's project_path is whatever directory Claude Code's cwd
    # happened to be, not necessarily the project's real root - working
    # from a subdirectory (2026-07-21: found sessions logged from
    # .../claude-metrics-dashboard/hooks instead of
    # .../claude-metrics-dashboard itself) creates a
    # spurious extra "project" for a folder that isn't one. Roll a path
    # up to its IMMEDIATE parent when that parent is ALSO a project_path
    # present in the dataset - if not, the path is left alone (most
    # projects/paths are real, standalone roots).
    #
    # "Parent is also a known path" alone is NOT enough of a signal, and
    # two broken attempts at this found out why the hard way:
    #   1. Walking up to the nearest matching ancestor at ANY depth: found
    #      /Users/username and /Users/username/Programming
    #      are THEMSELVES real project_paths (one-off
    #      sessions that never cd'ed anywhere specific) - every project
    #      merely NESTED under either got swallowed into that one bucket.
    #   2. Limiting to one level up didn't fix it either: EVERY project
    #      directly under ~/Programming shares that same immediate
    #      parent, and "Programming" being a known path made ALL of them
    #      collapse into it (most sessions ended up there,
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
    #
    # The heuristic can only ever roll a path up ONE level, so a session
    # logged from `<project>/docs/vendor` still names itself "vendor".
    # Nothing in a bare cwd says where the project stops - so `project_roots`
    # lets the reader DECLARE the directories their projects live in
    # (2026-09-21). A direct child of a declared root is a project however
    # deep the cwd went, and that answer is taken instead of the heuristic,
    # never on top of it: the container would otherwise be exactly the kind
    # of many-children parent the heuristic already refuses to collapse into,
    # and the two would disagree.
    # Records written from 2026-09-21 on carry `project_root`: the project
    # directory Claude Code itself hands every hook. That is not a guess,
    # so it is taken FIRST and the two guesses below never see a cwd that
    # already had an answer - which also keeps the fan-out counts honest,
    # since two cwds under one root collapse to one known path here.
    #
    # Except inside a worktree. There the recorded root is the project the
    # session STARTED in while the cwd followed Claude into the worktree,
    # so taking it would merge every worktree back into its project and
    # lose the "project / worktree" the chart has always shown.
    authoritative: set[str] = set()
    for record in records:
        root = record.get("project_root")
        path = record.get("project_path")
        if (
            isinstance(root, str)
            and root
            and isinstance(path, str)
            and split_worktree(path.rstrip("/")) is None
        ):
            record["project_path"] = root
            authoritative.add(root)

    known_paths: set[str] = {
        cast(str, record.get("project_path")) for record in records if record.get("project_path")
    }
    marker = "/.claude/worktrees/"
    maximum_children_to_collapse = 2

    roots = sorted(
        (root.rstrip("/") for root in project_roots if root.rstrip("/")), key=len, reverse=True
    )

    def declared_root_of(path: str) -> str | None:
        """The project a declared root says this path belongs to, or None
        if no root contains it. The longest root wins, so roots may nest."""
        normalized = path.rstrip("/")
        for root in roots:
            if not normalized.startswith(root + "/"):
                continue
            # Empty segments dropped rather than guarded against: a
            # doubled slash in a cwd is real, and after the rstrip above a
            # path that starts with `root + "/"` always has at least one
            # non-empty segment left, so there is no empty case to guard.
            head = next(seg for seg in normalized[len(root) + 1 :].split("/") if seg)
            project = f"{root}/{head}"
            # A worktree keeps its own path: collapsing to the project
            # would erase WHICH worktree, and color_group_key already
            # merges it into the project's colour anyway.
            split = split_worktree(normalized)
            if split is not None and split[0] == project:
                return f"{project}{worktree_marker_of(normalized)}{split[1]}"
            return project
        return None

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
        if path in authoritative:
            canonical[path] = path
            continue
        declared = declared_root_of(path)
        if declared is not None:
            canonical[path] = declared
            continue
        if marker in path:
            canonical[path] = path
            continue
        collapse_parent = parent_of.get(path)
        if collapse_parent and children_count[collapse_parent] <= maximum_children_to_collapse:
            canonical[path] = collapse_parent
        else:
            canonical[path] = path
    for record in records:
        project_path = record.get("project_path")
        if project_path in canonical and canonical[project_path] != project_path:
            record["project_path"] = canonical[project_path]
    # A session cwd'd into a scratchpad/tmp dir isn't a "project" (2026-07-21:
    # "tem um scratchpad que não faço ideia do que seja") - fold these into
    # the same "sem projeto" bucket None already means, rather than giving
    # every random ephemeral path its own legend entry.
    for record in records:
        project_path = record.get("project_path")
        if project_path and is_ephemeral_path(project_path):
            record["project_path"] = None
    return records


# macOS gives every user a private TMPDIR under /var/folders (reached as
# /private/var/folders through the /var symlink). Tools that create a
# throwaway cwd there - skill evals, for one - were each becoming a
# "project" of their own: 201 of them on a real history (2026-10-05).
EPHEMERAL_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")


def is_ephemeral_path(path: str) -> bool:
    # The trailing slash makes a temp root itself (a session started in
    # /tmp) match its own prefix, while "/tmpfiles" still does not.
    normalized = path.rstrip("/") + "/"
    return normalized.startswith(EPHEMERAL_PREFIXES) or "/scratchpad" in normalized


def dedupe_sessions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return dedupe_by_key(records, "session_id")


def dedupe_subagents(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return dedupe_by_key(records, "agent_id")


def filter_records(
    records: list[dict[str, Any]], tag: str | None = None, project: str | None = None
) -> list[dict[str, Any]]:
    filtered = records
    if tag is not None:
        filtered = [record for record in filtered if record.get("tag") == tag]
    if project is not None:
        filtered = [record for record in filtered if record.get("project_path") == project]
    return filtered


def filter_by_days(records: list[dict[str, Any]], days: int | None) -> list[dict[str, Any]]:
    if days is None:
        return records
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    filtered = []
    for record in records:
        timestamp_start = record.get("timestamp_start")
        if not timestamp_start:
            continue
        try:
            parsed_start = datetime.fromisoformat(
                timestamp_start.replace("Z", "+00:00")
            ).astimezone()
        except (ValueError, AttributeError, TypeError):
            continue
        if parsed_start >= cutoff:
            filtered.append(record)
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


def incomplete_usage_messages(record: dict[str, Any]) -> int:
    """How many of this record's messages never got their final usage entry,
    so its output is a floor - 0 when the key is absent (every row written
    before 2026-09-25, and every complete one since) or not a positive int
    (the file is hand-editable). `type(value) is int`, not `isinstance`:
    bool is an int subclass, so isinstance(True, int) is True and would let
    a hand-edited `"incomplete_usage_messages": true` return as the
    non-int value True instead of being rejected like every other non-int."""
    value = record.get("incomplete_usage_messages")
    return value if type(value) is int and value > 0 else 0


def record_token_total(record: dict[str, Any]) -> int:
    # Every token a record consumed, all four kinds. Cache dominates real
    # usage by orders of magnitude - measured 2026-08-10 against Claude
    # Code's own session transcripts, input+output was a tiny fraction of
    # the day's true volume. Any view that
    # calls its number a token "total" has to use this, or it reports a
    # rounding error as if it were the whole story.
    return (
        (record.get("input_tokens", 0) or 0)
        + (record.get("output_tokens", 0) or 0)
        + (record.get("cache_read_tokens", 0) or 0)
        + (record.get("cache_creation_tokens", 0) or 0)
    )


# A sentinel later than any real record, for a model whose every
# occurrence carries an unparseable timestamp (only possible on corrupted
# input - every genuine record's timestamp already round-trips through
# _parse_moment). Placed here rather than compared as None so
# order_models_by_first_appearance's sort key stays one consistent type.
_NEVER_SEEN = datetime.max.replace(tzinfo=timezone.utc)


def order_models_by_first_appearance(records: list[dict[str, Any]]) -> list[str]:
    """Every model in these records' tokens_by_model maps, oldest first.

    assign_model_slots (format.py) keeps a model's colour forever once
    given - the caller only has to hand it models in the order they
    genuinely first appeared. That is NOT insertion order for
    expand_records_to_daily_deltas' output: snapshots are sorted
    chronologically WITHIN one session/subagent, but the outer loop over
    different sessions follows raw_records' own order, which is not
    guaranteed to be chronological across sessions. This walks every
    record instead and keeps each model's earliest timestamp_start.

    Fix round 2 (backlog sweep 3 / package M): timestamp alone is not a
    total order - two models attributed at the exact same instant used to
    have their tie settled by model id, which let a model that only
    appears in a LATER record (in `records`' own order - see below for
    what that order actually is) sort into the MIDDLE of the arrival
    order just by having a smaller id, moving a model that already held a
    slot the id-sort now placed the newcomer ahead of. Reproduced through
    the real caller path
    (order_models_by_first_appearance -> assign_model_slots): appending a
    record whose model's id sorts alphabetically early, but which is
    genuinely the LAST of three models sharing one timestamp, bumped an
    existing model out of a real slot into overflow.

    The fix makes "first appearance" a total order that can only ever
    APPEND as `records` grows, never insert into the middle:
    (timestamp, the position within `records` of the first record that
    gives the model its earliest timestamp, the model's own position
    within THAT record's tokens_by_model map, and the model id only as a
    last resort - which cannot actually be reached: a `(timestamp,
    record index, position-within-record)` triple already uniquely
    identifies one dict entry in one record, so two different models can
    never produce an equal triple. It stays in the sort key anyway,
    exactly as assign_model_slots documents its own equivalent
    last-resort tie-break, to make that guarantee explicit rather than
    load-bearing by accident).

    This relies on `records` itself being in a stable, meaningful order -
    specifically, that an earlier index here means "encountered earlier
    when the underlying files were scanned". Both dashboards build the
    list passed here (daily_chart_records) the same way:
    load_records() reads each JSONL file by iterating its lines in
    on-disk order (the hooks only ever APPEND a line, never rewrite one),
    normalize_project_paths/filter_records/filter_by_days are plain
    order-preserving maps/filters over that list, and
    expand_records_to_daily_deltas groups the result by session/agent id
    in the order each id is FIRST ENCOUNTERED in its input (a session's
    own snapshots are re-sorted chronologically WITHIN that group, but
    the groups themselves keep the relative order their ids first
    appeared in) - both dashboard_html.py and dashboard_tui.py run this
    exact pipeline (load_records -> normalize_project_paths ->
    filter_records -> expand_records_to_daily_deltas, concatenated with
    the same pipeline over the subagents file) before calling this
    function, so the two dashboards agree on one deterministic order
    without either of them needing to know why.
    """
    first_seen: dict[str, tuple[datetime, int, int]] = {}
    for record_index, record in enumerate(records):
        moment = _parse_moment(record.get("timestamp_start")) or _NEVER_SEEN
        for model_position, model in enumerate(record.get("tokens_by_model") or {}):
            candidate = (moment, record_index, model_position)
            existing = first_seen.get(model)
            if existing is None or candidate < existing:
                first_seen[model] = candidate
    return sorted(first_seen, key=lambda model: (*first_seen[model], model))


def compute_token_type_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "series-1": sum(record.get("input_tokens", 0) or 0 for record in records),
        "series-2": sum(record.get("output_tokens", 0) or 0 for record in records),
        "series-3": sum(record.get("cache_read_tokens", 0) or 0 for record in records),
        "series-4": sum(record.get("cache_creation_tokens", 0) or 0 for record in records),
    }


def compute_model_totals(records: list[dict[str, Any]]) -> tuple[dict[str, int], int]:
    """(tokens per model, tokens attributed to no model) over these records.

    The third trend-chart mode slices the same quantity the other two do -
    a day's total tokens - by the model that produced them. Most of the
    history cannot be sliced that way: `tokens_by_model` only exists for
    records whose transcript survived long enough to be captured natively
    or reconstructed, which was a minority of the volume when this was written.

    That remainder is returned rather than dropped, and the caller draws it
    as its own band. Dropping it would make a day's bar shorter in this mode
    than in the other two, so switching tabs would silently rescale the day
    instead of re-slicing it - the one property all three modes share.

    A map claiming MORE than its record's own totals cannot push the
    remainder negative: the shortfall floors at zero, per record, so one bad
    row cannot eat another row's remainder. The backfill's gate refuses to
    write such a row, but nothing stops one written before that gate existed.
    """
    by_model: dict[str, int] = {}
    unattributed = 0
    for record in records:
        attributed = 0
        for model, bucket in (record.get("tokens_by_model") or {}).items():
            amount = sum(bucket.get(field, 0) or 0 for field in TOKEN_FIELDS)
            by_model[model] = by_model.get(model, 0) + amount
            attributed += amount
        unattributed += max(record_token_total(record) - attributed, 0)
    return by_model, unattributed


def compute_quota_totals(totals: dict[str, int]) -> tuple[int, int]:
    """(quota_input, quota_output), derived from compute_token_type_totals's
    own output - the closest DOCUMENTED proxy available for how much of a
    usage limit a set of records actually consumed.

    Backlog, 2026-09-02: record_token_total's flat 4-field sum can be almost
    entirely cache_read on a real heavy day, while the tokens that actually move a
    rate limit are a small fraction of that - a headline built on the flat
    sum answers "how much text moved," not "how much of my quota did this
    eat." Anthropic's Messages API rate-limit docs are explicit about the
    weighting this uses: input_tokens and cache_creation_input_tokens both
    count toward the ITPM limit, cache_read_input_tokens does not (for
    every current model), and output_tokens counts toward a SEPARATE OTPM
    limit rather than being summed with input at all - hence two numbers
    here, not one. Source (verified 2026-09-14):
    https://platform.claude.com/docs/en/api/rate-limits

    This is a proxy, not a claim about Claude Code's own session/weekly
    usage limit: Anthropic does not publish that formula, and an accurate
    dollar-cost weighting would additionally need to know which MODEL a
    record used (input:output price ratios differ sharply by model),
    which nothing in this pipeline records yet.
    """
    quota_input = totals.get("series-1", 0) + totals.get("series-4", 0)
    quota_output = totals.get("series-2", 0)
    return quota_input, quota_output


def expand_records_to_daily_deltas(raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # The Stop/SubagentStop hooks append a fresh CUMULATIVE snapshot every
    # turn, not one row per session/subagent - dedupe_sessions/
    # dedupe_subagents pick a single representative (largest
    # duration_seconds) for every other purpose, which is correct for
    # all-time totals but wrong for day-by-day charts: that single row's
    # timestamp_start is fixed at the session's START, so a long-running
    # session's ENTIRE cumulative total lands on one day bucket, however
    # long it actually ran. Found 2026-08-06: sessions and subagents
    # spanning multiple snapshots, some running for weeks - each one
    # dumped its whole multi-million-token total onto a
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
    # agent_id checked FIRST: subagent records carry BOTH their own
    # agent_id AND their parent session's session_id - checking
    # session_id first would group every subagent dispatched by the same
    # session together as if they were sequential snapshots of one
    # entity, corrupting deltas across unrelated subagents (found
    # 2026-08-06: nearly every agent_id mismatched dedup's own totals
    # this way). Session records never carry agent_id at
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
        snapshots.sort(key=lambda snapshot: snapshot.get("duration_seconds") or 0)
        previous = dict.fromkeys(TOKEN_FIELDS, 0)
        previous_by_model: dict[str, dict[str, int]] = {}
        for snapshot in snapshots:
            delta_record = dict(snapshot)
            for field in TOKEN_FIELDS:
                current = snapshot.get(field, 0) or 0
                # max(..., 0): a defensive floor, not expected to trigger -
                # every snapshot checked in real data was monotonically
                # non-decreasing, but a future hook change or a corrupted
                # row shouldn't be able to produce a negative delta.
                delta_record[field] = max(current - previous[field], 0)
                previous[field] = current
            # tokens_by_model is cumulative too, so it has to be differenced
            # exactly like the four fields above, floor included. The plain
            # `dict(snapshot)` copy that every other key gets would leave
            # each delta carrying the session's RUNNING per-model map beside
            # its own per-day totals, so summing the per-model numbers across
            # a session's deltas overcounts (measured on a two-snapshot
            # session, 100 -> 250: the four totals sum to 250, the copied map
            # to 350) - and these delta records are what the day-based trend
            # chart reads, so a per-model mode would disagree with the
            # existing per-project and per-token-type modes on the same day.
            # A model first seen in a later snapshot differences against zero;
            # previous_by_model keeps a model it has seen even if a later
            # snapshot omits it, so a reappearance is never counted twice.
            #
            # That same zero-start is also the deploy seam: a session with a
            # pre-deploy snapshot (no tokens_by_model at all) followed by a
            # post-deploy one lands its whole session-to-date total on the
            # FIRST snapshot that carries the key, because previous_by_model
            # has no history for a model it has never seen before - the
            # pre-deploy portion has no per-model data to attribute at all.
            # Unfixable at this layer: the earlier delta correctly has
            # non-zero four-field totals and no per-model map, and the later
            # one correctly attributes more tokens per-model than its own
            # four-field delta shows. Pinned, not treated as a bug to fix, by
            # the deploy-seam test in tests/test_records.py (search for
            # "pre_deploy" there).
            snapshot_by_model = snapshot.get("tokens_by_model")
            if snapshot_by_model:
                delta_by_model: dict[str, dict[str, int]] = {}
                for model, bucket in snapshot_by_model.items():
                    previous_bucket = previous_by_model.setdefault(
                        model, dict.fromkeys(TOKEN_FIELDS, 0)
                    )
                    delta_bucket: dict[str, int] = {}
                    for field in TOKEN_FIELDS:
                        current = bucket.get(field, 0) or 0
                        delta_bucket[field] = max(current - previous_bucket[field], 0)
                        previous_bucket[field] = current
                    delta_by_model[model] = delta_bucket
                delta_record["tokens_by_model"] = delta_by_model
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
    # direct session tokens in that window). Mutates records
    # in-place so every list derived from the same dicts earlier in the
    # pipeline (all_time_records, chart_records, the day/limit-filtered
    # records) picks up the fold automatically.
    #
    # incomplete_usage_messages folds the same way: once a session's output
    # includes a subagent's, it is a floor whenever that subagent's is. It is
    # only ever ADDED when positive, so absent keeps meaning zero.
    subagent_totals_by_session: dict[Any, dict[str, int]] = {}
    incomplete_by_session: dict[Any, int] = {}
    for subagent in subagent_records:
        session_id = subagent.get("session_id")
        if session_id is None:
            continue
        totals = subagent_totals_by_session.setdefault(session_id, dict.fromkeys(TOKEN_FIELDS, 0))
        for field in TOKEN_FIELDS:
            totals[field] += subagent.get(field, 0) or 0
        incomplete_by_session[session_id] = incomplete_by_session.get(
            session_id, 0
        ) + incomplete_usage_messages(subagent)
    for record in records:
        session_id = record.get("session_id")
        session_totals = subagent_totals_by_session.get(session_id)
        if not session_totals:
            continue
        for field in TOKEN_FIELDS:
            record[field] = (record.get(field, 0) or 0) + session_totals[field]
        if incomplete_by_session[session_id]:
            record["incomplete_usage_messages"] = (
                incomplete_usage_messages(record) + incomplete_by_session[session_id]
            )
    return records
