"""The branch breakdown as strings, shared by both dashboards.

Both interfaces render this dict and nothing else, so they cannot disagree
about a number or a label.
"""

from __future__ import annotations

from typing import Any

from .format import format_tokens
from .i18n import t
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
