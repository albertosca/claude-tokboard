"""The branch breakdown as strings, shared by both dashboards.

Both interfaces render this dict and nothing else, so they cannot disagree
about a number or a label.
"""

from __future__ import annotations

from typing import Any

from .branches import BRANCH_OTHERS_KEY, list_group_branches
from .format import format_tokens
from .i18n import t, tn
from .projects import project_display_label

PROJECTS_SHOWN = 8
BRANCHES_SHOWN = 8
DETACHED_BRANCH = "HEAD"
COLUMN_KEYS = ("branch", "sessions", "tokens", "share")


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
    return _branch_label(key, lang)


def _share(amount: int, total: int) -> str:
    return f"{round(100 * amount / total) if total else 0}%"


def _row(label: str, sessions: int, amount: int, total: int) -> dict[str, str]:
    return {
        "branch": label,
        "sessions": str(sessions),
        "tokens": format_tokens(amount),
        "share": _share(amount, total),
    }


def _project_rows(item: dict[str, Any], lang: str) -> list[dict[str, str]]:
    shown = item["branches"][:BRANCHES_SHOWN]
    rest = item["branches"][BRANCHES_SHOWN:]
    rows = [
        _row(_branch_label(row["branch"], lang), row["sessions"], row["total"], item["total"])
        for row in shown
    ]
    if rest:
        rows.append(
            _row(
                t("branches.others", lang, count=len(rest)),
                sum(row["sessions"] for row in rest),
                sum(row["total"] for row in rest),
                item["total"],
            )
        )
    return rows


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


def describe_branches(breakdown: list[dict[str, Any]], lang: str) -> dict[str, Any]:
    projects = [
        {
            "project": project_display_label(item["group"], lang=lang),
            "rows": _project_rows(item, lang),
        }
        for item in breakdown[:PROJECTS_SHOWN]
    ]
    return {
        "empty": not projects,
        "lang": lang,
        "heading": t("branches.heading", lang),
        "note": t("branches.note", lang),
        "columns": {key: t(f"branches.col.{key}", lang) for key in COLUMN_KEYS},
        "projects": projects,
    }
