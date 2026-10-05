"""Project identity, grouping, and per-project aggregate helpers -
shared by both dashboard entry points."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .i18n import t, tn
from .paths import WORKTREE_MARKERS, split_worktree
from .records import record_token_total


def compute_project_first_seen(records: list[dict[str, Any]]) -> dict[str, str]:
    first_seen: dict[str, str] = {}
    for record in records:
        project = record.get("project_path")
        timestamp_start = record.get("timestamp_start")
        if not project or not timestamp_start:
            continue
        if project not in first_seen or timestamp_start < first_seen[project]:
            first_seen[project] = timestamp_start
    return first_seen


def compute_project_last_seen(records: list[dict[str, Any]]) -> dict[str, str]:
    last_seen: dict[str, str] = {}
    for record in records:
        project = record.get("project_path")
        timestamp_start = record.get("timestamp_start")
        if not project or not timestamp_start:
            continue
        if project not in last_seen or timestamp_start > last_seen[project]:
            last_seen[project] = timestamp_start
    return last_seen


def project_display_label(project_path: str | None, *, lang: str) -> str:
    if not project_path:
        return t("no_project", lang)
    normalized = project_path.rstrip("/")
    split = split_worktree(normalized)
    if split is not None:
        parent_path, worktree_name = split
        parent_label = Path(parent_path.rstrip("/")).name or parent_path
        return f"{parent_label} / {worktree_name}"
    return Path(normalized).name or normalized


def color_group_key(project_path: str | None) -> str | None:
    # A worktree's own color group is its PARENT project (2026-07-21) -
    # they're the same underlying project as far as "what color am I"
    # goes, so the trend chart's legend can show one merged entry for
    # both instead of a separate near-duplicate one per worktree.
    if not project_path:
        return project_path
    normalized = project_path.rstrip("/")
    for marker in WORKTREE_MARKERS:
        if marker in normalized:
            parent_path, _, _ = normalized.partition(marker)
            return parent_path
    return project_path


def compute_project_totals(
    records: list[dict[str, Any]], project_last_seen: dict[str, str]
) -> tuple[list[str | None], dict[str | None, int], int]:
    totals: dict[str | None, int] = {}
    for record in records:
        project = record.get("project_path")
        # All four token kinds - this panel is titled "Total de tokens por
        # projeto", and until 2026-08-10 it summed input+output only,
        # reporting a tiny fraction of real volume under a label promising totals.
        totals[project] = totals.get(project, 0) + record_token_total(record)
    # Sorted by spend, with most-recent activity only as a tie-break.
    # This panel used to sort by recency, answering "what have I touched
    # lately" - a reasonable question, but not the one it is titled for
    # and not the one Alberto opens it to ask (2026-08-13). Recency
    # survives elsewhere as a marker on the row, not as the order.
    ordered_projects = sorted(
        totals.keys(),
        key=lambda project: (
            totals[project],
            project_last_seen.get(project, "") if project is not None else "",
        ),
        reverse=True,
    )
    maximum_total = max(totals.values()) if totals else 0
    return ordered_projects, totals, maximum_total


def compute_project_series_totals(
    records: list[dict[str, Any]],
) -> tuple[dict[str | None, dict[str, int]], dict[str | None, int]]:
    """Each project's tokens split by type (series-1..4 = input, output,
    cache read, cache creation), and each project's grand total. Shared by
    both dashboards' Panorama rankings since 2026-10-05."""
    per_project: dict[str | None, dict[str, int]] = {}
    for record in records:
        project = record.get("project_path")
        bucket = per_project.setdefault(
            project, {"series-1": 0, "series-2": 0, "series-3": 0, "series-4": 0}
        )
        bucket["series-1"] += record.get("input_tokens", 0) or 0
        bucket["series-2"] += record.get("output_tokens", 0) or 0
        bucket["series-3"] += record.get("cache_read_tokens", 0) or 0
        bucket["series-4"] += record.get("cache_creation_tokens", 0) or 0
    grand_totals = {project: sum(series.values()) for project, series in per_project.items()}
    return per_project, grand_totals


def project_days_running(
    project_first_seen: dict[str, str],
    project: str | None,
    *,
    lang: str,
    now: datetime | None = None,
) -> str:
    # `now` is test-only (real callers never pass it - it defaults to the
    # real clock) - it exists so the calendar-day boundary below can be
    # tested without depending on when the suite happens to run.
    first_seen = project_first_seen.get(project) if project is not None else None
    if not first_seen:
        return ""
    try:
        first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00")).astimezone()
        resolved_now = (now or datetime.now()).astimezone()
        # CALENDAR days, not elapsed 24h periods (Package B follow-up): a
        # project first used at 23:00 yesterday must read "1 day ago" the
        # moment the clock crosses midnight, not still "today" until a
        # full 24h has elapsed. `.date()` compares local calendar dates,
        # so only the number of midnights crossed counts, not the number
        # of whole 24h periods.
        days = (resolved_now.date() - first_dt.date()).days
        # 0 renders as "today", not the plural machinery's "0 days ago".
        # A negative count (clock skew: first_seen recorded in the future)
        # is not a day count at all - it renders the same as today rather
        # than printing "-1 days ago".
        if days <= 0:
            return t("first_used.today", lang)
        return tn("first_used", days, lang)
    except (ValueError, AttributeError, TypeError):
        return ""


def get_open_projects(sessions_directory: str | None = None) -> set[str]:
    if sessions_directory is None:
        sessions_directory = str(Path("~/.claude/sessions").expanduser())
    open_projects: set[str] = set()
    try:
        # Path.glob (unlike glob.glob) also matches dot-prefixed entries; safe
        # here because Claude Code's own naming scheme never produces a
        # leading dot.
        paths = list(Path(sessions_directory).glob("*.json"))
    except Exception:
        return open_projects
    for path in paths:
        try:
            with path.open() as session_file:
                data = json.load(session_file)
        except Exception:
            continue
        cwd = data.get("cwd")
        if cwd:
            open_projects.add(cwd)
    return open_projects


# Eight colours, not thirty (2026-10-05 review): the old thirty slots cycled
# through a generated hue sweep whose neighbours read as the same colour
# (normal-vision delta E 1.6). Eight is what the validated palette in
# dashboard_html_assets.py holds; the TUI's PANE_COLORS uses its first eight.
PROJECT_COLOR_SLOTS = 8
PROJECT_SLOT_WINDOW_DAYS = 30


def _started_on_or_after(record: dict[str, Any], cutoff: datetime) -> bool:
    timestamp_start = record.get("timestamp_start")
    if not timestamp_start:
        return False
    try:
        started = datetime.fromisoformat(timestamp_start.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        # A malformed row (a number, a list) is simply not recent - the same
        # shapes session_date tolerates; it must not take the render down.
        return False
    if started.tzinfo is None:
        started = started.astimezone()
    return started >= cutoff


def assign_project_slots(
    records: list[dict[str, Any]],
    now: datetime,
    slot_count: int = PROJECT_COLOR_SLOTS,
    window_days: int = PROJECT_SLOT_WINDOW_DAYS,
) -> dict[str, int]:
    """Which projects get one of the colour slots, keyed by color_group_key.

    The busiest projects of the last `window_days` come first, so the
    projects a reader is working on now are the coloured ones in every
    period (Alberto's choice, 2026-10-05). Slots still free go to the
    biggest all-time spenders, so an old heavy project is not gray only
    because few projects were active lately. Ties break by name, so a
    render is deterministic. "No project" never takes a slot.
    """
    cutoff = now - timedelta(days=window_days)
    recent_totals: dict[str, int] = {}
    all_time_totals: dict[str, int] = {}
    for record in records:
        key = color_group_key(record.get("project_path"))
        tokens = record_token_total(record)
        # A project with no tokens never takes a slot: opened lately but
        # silent, it would outrank every all-time spender (2026-10-05 review).
        if key is None or tokens <= 0:
            continue
        all_time_totals[key] = all_time_totals.get(key, 0) + tokens
        if _started_on_or_after(record, cutoff):
            recent_totals[key] = recent_totals.get(key, 0) + tokens

    def by_spend(totals: dict[str, int]) -> list[str]:
        return sorted(totals, key=lambda key: (-totals[key], key))

    ordered = by_spend(recent_totals)
    ordered += [key for key in by_spend(all_time_totals) if key not in recent_totals]
    return {key: slot for slot, key in enumerate(ordered[:slot_count])}


def assign_group_colors(
    records: list[dict[str, Any]],
    now: datetime | None = None,
    ranking_records: list[dict[str, Any]] | None = None,
) -> dict[str | None, str]:
    """The colour class of every raw project_path in `records`.

    Keyed by raw path (every `group_colors.get(project)` call site keeps
    working) while the slot is decided by color_group_key, so a worktree
    shares its parent's colour. A project without a slot is
    "group-color-other" - drawn and listed as "others" - and a record with
    no project is "group-color-none".

    `ranking_records` is the feed whose spend decides the slots when
    `records` holds more than one view of the same tokens (the HTML
    dashboard's session snapshots plus their per-day deltas)."""
    slots = assign_project_slots(
        records if ranking_records is None else ranking_records,
        now or datetime.now().astimezone(),
    )
    assignment: dict[str | None, str] = {}
    for record in records:
        project = record.get("project_path")
        if project in assignment:
            continue
        if project is None:
            assignment[project] = "group-color-none"
            continue
        key = color_group_key(project)
        slot = slots.get(key) if key is not None else None
        assignment[project] = "group-color-other" if slot is None else f"group-color-{slot + 1}"
    return assignment


def build_project_groups(
    records: list[dict[str, Any]], group_colors: dict[str | None, str]
) -> list[dict[str, Any]]:
    # Grouped by color_group_key, not the raw project_path (2026-07-22
    # fix: "grupos duplicados/fragmentados pro mesmo projeto real") - a
    # worktree's sessions were showing up as their own separate group
    # next to their parent project's, even though they already share a
    # color (assign_group_colors keys the actual color slot by this same
    # function). Individual records keep their own real project_path
    # (still used for e.g. the session row's own detail panel/title) -
    # only the grouping key changes.
    ordered_descending = list(reversed(records))
    groups: dict[str | None, dict[str, Any]] = {}
    group_order: list[str | None] = []
    for record in ordered_descending:
        project = record.get("project_path")
        key = color_group_key(project)
        if key not in groups:
            groups[key] = {"records": [], "sample_project": project}
            group_order.append(key)
        groups[key]["records"].append(record)
    result = []
    for key in group_order:
        # Look up the color via any member's own raw path (guaranteed to
        # be a key in group_colors) rather than the canonical key itself,
        # which is only a real dict key when some session was ever
        # logged directly at that parent path (not always true - a
        # project sometimes only ever gets worked on inside worktrees).
        sample_project = groups[key]["sample_project"]
        result.append(
            {
                "project": key,
                "class": group_colors.get(sample_project, "group-color-none"),
                "records": groups[key]["records"],
            }
        )
    return result
