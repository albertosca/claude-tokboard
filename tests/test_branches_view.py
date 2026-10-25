"""The strings and shares both dashboards render the branch breakdown from."""

from collections.abc import Sequence
from typing import Any

from metrics_core import branches_view

GROUP = "/Users/username/my-api"


def _breakdown(rows: Sequence[tuple[str | None, int, int]]) -> list[dict[str, Any]]:
    branch_rows = [
        {"branch": name, "sessions": count, "total": total} for name, count, total in rows
    ]
    return [{"group": GROUP, "total": sum(total for _, _, total in rows), "branches": branch_rows}]


def test_nothing_to_show_is_an_empty_view() -> None:
    view = branches_view.describe_branches([], "en")

    assert view["empty"] is True
    assert view["projects"] == []


def test_rows_carry_formatted_numbers_and_the_share_of_the_project() -> None:
    view = branches_view.describe_branches(
        _breakdown([("main", 3, 7_500), ("dev", 1, 2_500)]), "en"
    )

    assert view["empty"] is False
    assert view["projects"][0]["project"] == "my-api"
    assert view["projects"][0]["rows"] == [
        {"branch": "main", "sessions": "3", "tokens": "7.5k", "share": "75%"},
        {"branch": "dev", "sessions": "1", "tokens": "2.5k", "share": "25%"},
    ]
    assert "last branch" in view["note"]
    assert list(view["columns"]) == ["branch", "sessions", "tokens", "share"]
    assert branches_view.describe_branches([], "pt")["columns"]["share"] == "Parcela"


def test_detached_and_missing_branches_are_named_in_both_languages() -> None:
    breakdown = _breakdown([("HEAD", 1, 2), (None, 1, 1)])

    english = branches_view.describe_branches(breakdown, "en")["projects"][0]["rows"]
    portuguese = branches_view.describe_branches(breakdown, "pt")["projects"][0]["rows"]

    assert [row["branch"] for row in english] == ["(detached)", "(no branch)"]
    assert [row["branch"] for row in portuguese] == ["(destacado)", "(sem branch)"]


def test_only_the_largest_projects_are_shown() -> None:
    breakdown = [
        {
            "group": f"/Users/username/project-{index}",
            "total": 100 - index,
            "branches": [
                {"branch": "a", "sessions": 1, "total": 50 - index},
                {"branch": "b", "sessions": 1, "total": 50},
            ],
        }
        for index in range(branches_view.PROJECTS_SHOWN + 3)
    ]

    view = branches_view.describe_branches(breakdown, "en")

    assert len(view["projects"]) == branches_view.PROJECTS_SHOWN


def test_branches_past_the_cap_fold_into_one_others_row() -> None:
    total_branches = branches_view.BRANCHES_SHOWN + 3
    rows = [
        (f"branch-{index}", 2, 1_000 * (total_branches - index)) for index in range(total_branches)
    ]

    shown = branches_view.describe_branches(_breakdown(rows), "en")["projects"][0]["rows"]

    assert len(shown) == branches_view.BRANCHES_SHOWN + 1
    assert shown[-1]["branch"] == "others (3)"
    assert shown[-1]["sessions"] == "6"
    assert shown[-1]["tokens"] == "6.0k"


def test_a_branch_named_like_the_others_row_stays_its_own_row() -> None:
    rows = [("others (3)", 1, 5), ("main", 1, 4)]

    shown = branches_view.describe_branches(_breakdown(rows), "en")["projects"][0]["rows"]

    assert [row["branch"] for row in shown] == ["others (3)", "main"]


def test_a_project_without_a_path_is_labelled() -> None:
    breakdown = _breakdown([("main", 1, 2), ("dev", 1, 1)])
    breakdown[0]["group"] = None

    view = branches_view.describe_branches(breakdown, "pt")

    assert view["projects"][0]["project"] == "sem projeto"
