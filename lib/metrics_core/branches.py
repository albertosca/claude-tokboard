"""Tokens per git branch inside each project, from the session records.

A session record holds one branch: the last one its transcript showed, so a
session that moved between branches counts whole under the second.
"""

from __future__ import annotations

from typing import Any

from .projects import PROJECT_COLOR_SLOTS
from .records import record_token_total

# The chart's own slots are as many as a project's, so a branch tab reads like
# the project tab. NUL cannot occur in a branch name, so the bucket's key can
# never be a real branch (a branch called "others" stays a branch).
BRANCH_COLOR_SLOTS = PROJECT_COLOR_SLOTS
BRANCH_OTHERS_KEY = "\x00others"


def branch_of(record: dict[str, Any]) -> str | None:
    return record.get("git_branch") or None


def list_group_branches(records: list[dict[str, Any]]) -> list[tuple[str | None, int]]:
    """The distinct branches of one session-list group with their session
    counts, busiest first."""
    counts: dict[str | None, int] = {}
    for record in records:
        branch = branch_of(record)
        counts[branch] = counts.get(branch, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0] or ""))


def assign_branch_slots(
    records: list[dict[str, Any]], slot_count: int = BRANCH_COLOR_SLOTS
) -> dict[str, int]:
    """Which branches get a colour slot: the biggest spenders over `records`,
    ties by name. "No branch" and branches with no tokens never take one."""
    totals: dict[str, int] = {}
    for record in records:
        branch = branch_of(record)
        tokens = record_token_total(record)
        if branch is None or tokens <= 0:
            continue
        totals[branch] = totals.get(branch, 0) + tokens
    ordered = sorted(totals, key=lambda branch: (-totals[branch], branch))
    return {branch: slot for slot, branch in enumerate(ordered[:slot_count])}


def branch_bucket(record: dict[str, Any], slots: dict[str, int]) -> str | None:
    """The chart segment a record's tokens go to: its slotted branch, None
    for no branch, or BRANCH_OTHERS_KEY."""
    branch = branch_of(record)
    if branch is None:
        return None
    return branch if branch in slots else BRANCH_OTHERS_KEY


def sum_tokens_by_branch(
    records: list[dict[str, Any]], slots: dict[str, int]
) -> dict[str | None, int]:
    """Tokens per chart segment; a segment with none is absent."""
    totals: dict[str | None, int] = {}
    for record in records:
        tokens = record_token_total(record)
        if tokens <= 0:
            continue
        key = branch_bucket(record, slots)
        totals[key] = totals.get(key, 0) + tokens
    return totals


def compute_branch_series(records: list[dict[str, Any]], slots: dict[str, int]) -> list[str | None]:
    """The segments `records` fill, in drawing order: slotted branches by
    spend here (busiest first), then "no branch", then the others bucket."""
    totals = sum_tokens_by_branch(records, slots)
    named = sorted(
        (key for key in totals if key is not None and key != BRANCH_OTHERS_KEY),
        key=lambda key: (-totals[key], key),
    )
    series: list[str | None] = [*named]
    if None in totals:
        series.append(None)
    if BRANCH_OTHERS_KEY in totals:
        series.append(BRANCH_OTHERS_KEY)
    return series
