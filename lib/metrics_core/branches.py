"""Tokens per git branch inside each project, from the session records.

A session record holds one branch: the last one its transcript showed, so a
session that moved between branches counts whole under the second.
"""

from __future__ import annotations

from typing import Any

from .projects import color_group_key
from .records import record_token_total


def _branch_of(record: dict[str, Any]) -> str | None:
    return record.get("git_branch") or None


def list_group_branches(records: list[dict[str, Any]]) -> list[tuple[str | None, int]]:
    """The distinct branches of one session-list group with their session
    counts, busiest first."""
    counts: dict[str | None, int] = {}
    for record in records:
        branch = _branch_of(record)
        counts[branch] = counts.get(branch, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0] or ""))


def compute_branch_breakdown(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Projects with at least two branches in `records`, largest first. A
    worktree belongs to its parent project, as it does in the chart legend."""
    totals: dict[str | None, dict[str | None, dict[str, int]]] = {}
    for record in records:
        group = color_group_key(record.get("project_path"))
        branch = totals.setdefault(group, {}).setdefault(
            _branch_of(record), {"sessions": 0, "total": 0}
        )
        branch["sessions"] += 1
        branch["total"] += record_token_total(record)
    breakdown: list[dict[str, Any]] = []
    for group, by_branch in totals.items():
        if len(by_branch) < 2:
            continue
        rows: list[dict[str, Any]] = [
            {"branch": branch, "sessions": counts["sessions"], "total": counts["total"]}
            for branch, counts in by_branch.items()
        ]
        rows.sort(key=lambda row: (-row["total"], row["branch"] or ""))
        breakdown.append(
            {"group": group, "total": sum(row["total"] for row in rows), "branches": rows}
        )
    breakdown.sort(key=lambda item: (-item["total"], item["group"] or ""))
    return breakdown
