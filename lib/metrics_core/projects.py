"""Project identity, grouping, and per-project aggregate helpers -
shared by both dashboard entry points."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .i18n import t, tn
from .paths import WORKTREE_MARKERS, split_worktree
from .records import record_token_total


def compute_project_first_seen(records: list[dict[str, Any]]) -> dict[str, str]:
    first_seen: dict[str, str] = {}
    for r in records:
        project = r.get("project_path")
        timestamp_start = r.get("timestamp_start")
        if not project or not timestamp_start:
            continue
        if project not in first_seen or timestamp_start < first_seen[project]:
            first_seen[project] = timestamp_start
    return first_seen


def compute_project_last_seen(records: list[dict[str, Any]]) -> dict[str, str]:
    last_seen: dict[str, str] = {}
    for r in records:
        project = r.get("project_path")
        timestamp_start = r.get("timestamp_start")
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
    for r in records:
        project = r.get("project_path")
        # All four token kinds - this panel is titled "Total de tokens por
        # projeto", and until 2026-08-10 it summed input+output only,
        # reporting ~0.3% of real volume under a label promising totals.
        totals[project] = totals.get(project, 0) + record_token_total(r)
    # Sorted by spend, with most-recent activity only as a tie-break.
    # This panel used to sort by recency, answering "what have I touched
    # lately" - a reasonable question, but not the one it is titled for
    # and not the one Alberto opens it to ask (2026-08-13). Recency
    # survives elsewhere as a marker on the row, not as the order.
    ordered_projects = sorted(
        totals.keys(),
        key=lambda p: (totals[p], project_last_seen.get(p, "") if p is not None else ""),
        reverse=True,
    )
    max_total = max(totals.values()) if totals else 0
    return ordered_projects, totals, max_total


def project_days_running(
    project_first_seen: dict[str, str], project: str | None, *, lang: str
) -> str:
    first_seen = project_first_seen.get(project) if project is not None else None
    if not first_seen:
        return ""
    try:
        first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00")).astimezone()
        days = (datetime.now().astimezone() - first_dt).days
        # 0 renders as "today", not the plural machinery's "0 days ago".
        # A negative count (clock skew: first_seen recorded in the future)
        # is not a day count at all - it renders the same as today rather
        # than printing "-1 days ago".
        if days <= 0:
            return t("first_used.today", lang)
        return tn("first_used", days, lang)
    except (ValueError, AttributeError, TypeError):
        return ""


def get_open_projects(sessions_dir: str | None = None) -> set[str]:
    if sessions_dir is None:
        sessions_dir = str(Path("~/.claude/sessions").expanduser())
    open_projects: set[str] = set()
    try:
        # Path.glob (unlike glob.glob) also matches dot-prefixed entries; safe
        # here because Claude Code's own naming scheme never produces a
        # leading dot.
        paths = list(Path(sessions_dir).glob("*.json"))
    except Exception:
        return open_projects
    for path in paths:
        try:
            with path.open() as f:
                data = json.load(f)
        except Exception:
            continue
        cwd = data.get("cwd")
        if cwd:
            open_projects.add(cwd)
    return open_projects


def assign_group_colors(records: list[dict[str, Any]]) -> dict[str | None, str]:
    # Keyed by raw project_path (so every existing `group_colors.get(project)`
    # call site keeps working unchanged) but the SLOT is decided by
    # color_group_key, so a worktree gets the exact same color as its
    # parent project instead of its own slot (2026-07-21).
    assignment: dict[str | None, str] = {}
    key_to_color: dict[str | None, str] = {}
    next_slot = 0
    for record in records:
        project = record.get("project_path")
        if project in assignment:
            continue
        if project is None:
            assignment[project] = "group-color-none"
            continue
        key = color_group_key(project)
        if key not in key_to_color:
            slot = (next_slot % 30) + 1
            key_to_color[key] = f"group-color-{slot}"
            next_slot += 1
        assignment[project] = key_to_color[key]
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
    ordered_desc = list(reversed(records))
    groups: dict[str | None, dict[str, Any]] = {}
    group_order: list[str | None] = []
    for record in ordered_desc:
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
