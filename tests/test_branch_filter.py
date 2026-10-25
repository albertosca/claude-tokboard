"""The branch options of one project group of the session list."""

from typing import Any

from metrics_core import branches, branches_view


def _records(*names: str | None) -> list[dict[str, Any]]:
    return [{"session_id": f"s{index}", "git_branch": name} for index, name in enumerate(names)]


def test_branches_are_listed_most_sessions_first_then_by_name() -> None:
    listed = branches.list_group_branches(_records("dev", "main", "main", "alpha", "dev"))

    assert listed == [("dev", 2), ("main", 2), ("alpha", 1)]


def test_a_session_without_a_branch_is_its_own_option() -> None:
    listed = branches.list_group_branches(_records("main", None, ""))

    assert listed == [(None, 2), ("main", 1)]


def test_a_group_with_a_single_branch_has_no_filter() -> None:
    assert branches_view.describe_branch_filter(_records("main", "main"), "my-api", "en") is None


def test_chips_carry_the_match_key_the_label_and_the_count() -> None:
    view = branches_view.describe_branch_filter(
        _records("main", "main", "HEAD", None), "my-api", "en"
    )

    assert view is not None
    assert view["label"] == "Filter my-api by branch"
    assert view["all"] == "All"
    assert view["chips"] == [
        {"key": "main", "label": "main", "sessions": "2"},
        {"key": "-", "label": "(no branch)", "sessions": "1"},
        {"key": "HEAD", "label": "(detached)", "sessions": "1"},
    ]


def test_the_portuguese_labels_are_translated() -> None:
    view = branches_view.describe_branch_filter(_records("main", None), "my-api", "pt")

    assert view is not None
    assert view["all"] == "Todas"
    assert view["label"] == "Filtrar my-api por branch"


def test_only_the_busiest_branches_become_chips() -> None:
    names = [f"branch-{index}" for index in range(branches_view.BRANCHES_SHOWN + 3)]

    view = branches_view.describe_branch_filter(_records(*names), "my-api", "en")

    assert view is not None
    assert len(view["chips"]) == branches_view.BRANCHES_SHOWN
