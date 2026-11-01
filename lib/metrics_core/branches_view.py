"""The branch breakdown as strings, shared by both dashboards.

Both interfaces render this dict and nothing else, so they cannot disagree
about a number or a label.
"""

from __future__ import annotations

from typing import Any

from .branches import BRANCH_OTHERS_KEY, SERIES_SEPARATOR, branch_of, list_group_branches
from .i18n import t, tn
from .projects import project_display_label
from .records import record_token_total

BRANCHES_SHOWN = 8
DETACHED_BRANCH = "HEAD"
SUMMARY_BRANCHES = 2


def _branch_label(branch: str | None, lang: str) -> str:
    if branch is None:
        return t("branches.none", lang)
    if branch == DETACHED_BRANCH:
        return t("branches.detached", lang)
    return branch


def branch_series_label(key: str | None, lang: str) -> str:
    """A chart segment's name, shared by the legend and the tooltips."""
    if key == BRANCH_OTHERS_KEY:
        return t("others", lang)
    if key is None:
        return _branch_label(None, lang)
    group, _, branch = key.partition(SERIES_SEPARATOR)
    return f"{project_display_label(group or None, lang=lang)}:{_branch_label(branch, lang)}"


def _share(amount: int, total: int) -> str:
    return f"{round(100 * amount / total) if total else 0}%"


def describe_branch_summary(records: list[dict[str, Any]], lang: str) -> str:
    """One project group's branch split on a single line, e.g.
    "main 73% · feat/x 16% · +2": the two biggest branches by tokens and how
    many more there are. Empty when the group has fewer than two branches.
    Sessions without tokens are left out, as the session list leaves them out."""
    totals: dict[str | None, int] = {}
    for record in records:
        tokens = record_token_total(record)
        if tokens > 0:
            branch = branch_of(record)
            totals[branch] = totals.get(branch, 0) + tokens
    if len(totals) < 2:
        return ""
    ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0] or ""))
    grand_total = sum(totals.values())
    parts = [
        f"{_branch_label(branch, lang)} {_share(amount, grand_total)}"
        for branch, amount in ordered[:SUMMARY_BRANCHES]
    ]
    if len(ordered) > SUMMARY_BRANCHES:
        parts.append(f"+{len(ordered) - SUMMARY_BRANCHES}")
    return " · ".join(parts)


NO_BRANCH_KEY = "-"


def describe_branch_filter(
    records: list[dict[str, Any]], project_label: str, lang: str
) -> dict[str, Any] | None:
    """The chips that narrow one project group of the session list to a
    branch, or None when the group has fewer than two branches. A chip's
    `key` is what the session row shows in its branch column ("-" for none),
    so the page matches rows by text and rows need no attribute of their own."""
    options = list_group_branches(records)
    if len(options) < 2:
        return None
    rest = options[BRANCHES_SHOWN:]
    return {
        "label": t("branches.filter.label", lang, project=project_label),
        "all": t("branches.filter.all", lang),
        "chips": [
            {
                "key": NO_BRANCH_KEY if branch is None else branch,
                "label": _branch_label(branch, lang),
                "sessions": str(sessions),
            }
            for branch, sessions in options[:BRANCHES_SHOWN]
        ],
        # The branches past the chip cap, narrowed to together: without it
        # they could never be filtered at all.
        "rest": _filter_rest(rest, lang) if rest else None,
    }


def _filter_rest(rest: list[tuple[str | None, int]], lang: str) -> dict[str, Any]:
    return {
        "keys": [NO_BRANCH_KEY if branch is None else branch for branch, _ in rest],
        "branch_labels": [_branch_label(branch, lang) for branch, _ in rest],
        "label": tn("branches.filter.rest", len(rest), lang),
        "sessions": str(sum(sessions for _, sessions in rest)),
    }
