"""The one-line branch split a project group's header carries, in both dashboards."""

from typing import Any

from metrics_core import branches_view

PROJECT = "/Users/username/my-api"


def _session(branch: str | None, tokens: int) -> dict[str, Any]:
    return {
        "project_path": PROJECT,
        "git_branch": branch,
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def test_a_group_with_one_branch_has_no_summary() -> None:
    records = [_session("main", 10), _session("main", 20)]

    assert branches_view.describe_branch_summary(records, "en") == ""
    assert branches_view.describe_branch_summary([], "en") == ""


def test_the_two_biggest_branches_are_named_with_their_share_of_tokens() -> None:
    records = [_session("feature/example", 25), _session("main", 50), _session("main", 25)]

    summary = branches_view.describe_branch_summary(records, "en")

    assert summary == "main 75% · feature/example 25%"


def test_the_branches_past_the_second_fold_into_a_count() -> None:
    records = [
        _session(name, tokens) for name, tokens in (("a", 50), ("b", 30), ("c", 15), ("d", 5))
    ]

    assert branches_view.describe_branch_summary(records, "en") == "a 50% · b 30% · +2"


def test_equal_branches_are_ordered_by_name() -> None:
    records = [_session("zeta", 10), _session("alpha", 10)]

    assert branches_view.describe_branch_summary(records, "en") == "alpha 50% · zeta 50%"


def test_a_session_without_tokens_does_not_count_as_a_branch() -> None:
    records = [_session("main", 40), _session("scratch", 0)]

    assert branches_view.describe_branch_summary(records, "en") == ""


def test_detached_and_missing_branches_are_named_in_both_languages() -> None:
    records = [_session("HEAD", 60), _session(None, 40)]

    assert (
        branches_view.describe_branch_summary(records, "en") == "(detached) 60% · (no branch) 40%"
    )
    assert (
        branches_view.describe_branch_summary(records, "pt") == "(destacado) 60% · (sem branch) 40%"
    )


def test_a_branch_named_like_the_others_key_stays_a_branch() -> None:
    records = [_session("others", 70), _session("main", 30)]

    assert branches_view.describe_branch_summary(records, "en") == "others 70% · main 30%"
