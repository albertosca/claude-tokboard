"""Project identity, grouping, and per-project aggregate helpers -
shared by both dashboard entry points."""
import glob
import json
import os
from datetime import datetime


def compute_project_first_seen(records):
    first_seen = {}
    for r in records:
        project = r.get("project_path")
        timestamp_start = r.get("timestamp_start")
        if not project or not timestamp_start:
            continue
        if project not in first_seen or timestamp_start < first_seen[project]:
            first_seen[project] = timestamp_start
    return first_seen


def compute_project_last_seen(records):
    last_seen = {}
    for r in records:
        project = r.get("project_path")
        timestamp_start = r.get("timestamp_start")
        if not project or not timestamp_start:
            continue
        if project not in last_seen or timestamp_start > last_seen[project]:
            last_seen[project] = timestamp_start
    return last_seen


def project_display_label(project_path):
    if not project_path:
        return "sem projeto"
    normalized = project_path.rstrip("/")
    marker = "/.claude/worktrees/"
    if marker in normalized:
        parent_path, _, remainder = normalized.partition(marker)
        worktree_name = remainder.split("/")[0]
        if worktree_name:
            parent_label = os.path.basename(parent_path.rstrip("/")) or parent_path
            return f"{parent_label} / {worktree_name}"
    return os.path.basename(normalized) or normalized


def color_group_key(project_path):
    # A worktree's own color group is its PARENT project (2026-07-21) -
    # they're the same underlying project as far as "what color am I"
    # goes, so the trend chart's legend can show one merged entry for
    # both instead of a separate near-duplicate one per worktree.
    if not project_path:
        return project_path
    normalized = project_path.rstrip("/")
    marker = "/.claude/worktrees/"
    if marker in normalized:
        parent_path, _, _ = normalized.partition(marker)
        return parent_path
    return project_path


def compute_project_totals(records, project_last_seen):
    totals = {}
    for r in records:
        project = r.get("project_path")
        totals[project] = totals.get(project, 0) + (r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0)
    # Sorted by most-recent-activity, not by token volume — answers "what
    # have I touched lately" rather than "what costs the most," and pairs
    # naturally with the top-10-then-collapse cutoff callers apply (the most
    # recently active projects are the ones most likely to matter right now).
    ordered_projects = sorted(totals.keys(), key=lambda p: project_last_seen.get(p, ""), reverse=True)
    max_total = max(totals.values()) if totals else 0
    return ordered_projects, totals, max_total


def project_days_running(project_first_seen, project):
    first_seen = project_first_seen.get(project)
    if not first_seen:
        return ""
    try:
        first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00")).astimezone()
        return f" · há {(datetime.now().astimezone() - first_dt).days} dias"
    except (ValueError, AttributeError, TypeError):
        return ""


def get_open_projects(sessions_dir=None):
    if sessions_dir is None:
        sessions_dir = os.path.expanduser("~/.claude/sessions")
    open_projects = set()
    try:
        paths = glob.glob(os.path.join(sessions_dir, "*.json"))
    except Exception:
        return open_projects
    for path in paths:
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            continue
        cwd = data.get("cwd")
        if cwd:
            open_projects.add(cwd)
    return open_projects


def assign_group_colors(records):
    # Keyed by raw project_path (so every existing `group_colors.get(project)`
    # call site keeps working unchanged) but the SLOT is decided by
    # color_group_key, so a worktree gets the exact same color as its
    # parent project instead of its own slot (2026-07-21).
    assignment = {}
    key_to_color = {}
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


def build_project_groups(records, group_colors):
    # Grouped by color_group_key, not the raw project_path (2026-07-22
    # fix: "grupos duplicados/fragmentados pro mesmo projeto real") - a
    # worktree's sessions were showing up as their own separate group
    # next to their parent project's, even though they already share a
    # color (assign_group_colors keys the actual color slot by this same
    # function). Individual records keep their own real project_path
    # (still used for e.g. the session row's own detail panel/title) -
    # only the grouping key changes.
    ordered_desc = list(reversed(records))
    groups = {}
    group_order = []
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
