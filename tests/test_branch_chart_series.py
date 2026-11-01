"""The chart's branch buckets: colour slots, per-day sums and labels."""

from typing import Any

from metrics_core import branches, branches_view


def _record(branch: str | None, tokens: int, project: str = "/proj/a") -> dict[str, Any]:
    return {"project_path": project, "git_branch": branch, "input_tokens": tokens}


def _key(branch: str, project: str = "/proj/a") -> str:
    return f"{project}{branches.SERIES_SEPARATOR}{branch}"


def test_slots_go_to_the_branches_that_spent_most_ties_by_name() -> None:
    records = [_record("beta", 5), _record("alpha", 5), _record("main", 9), _record("idle", 0)]

    assert branches.assign_branch_slots(records, slot_count=3) == {
        _key("main"): 0,
        _key("alpha"): 1,
        _key("beta"): 2,
    }


def test_a_session_without_a_branch_never_takes_a_slot() -> None:
    assert branches.assign_branch_slots([_record(None, 99), _record("main", 1)]) == {
        _key("main"): 0
    }


def test_a_record_lands_in_its_slot_in_no_branch_or_in_others() -> None:
    slots = {_key("main"): 0}

    assert branches.branch_bucket(_record("main", 1), slots) == _key("main")
    assert branches.branch_bucket(_record(None, 1), slots) is None
    assert branches.branch_bucket(_record("", 1), slots) is None
    assert (
        branches.branch_bucket(_record("feature/example", 1), slots) == branches.BRANCH_OTHERS_KEY
    )


def test_series_lists_slotted_branches_by_spend_then_no_branch_then_others() -> None:
    slots = {_key("main"): 0, _key("dev"): 1}
    records = [
        _record("dev", 5),
        _record("main", 2),
        _record(None, 1),
        _record("feature/example", 4),
    ]

    assert branches.compute_branch_series(records, slots) == [
        _key("dev"),
        _key("main"),
        None,
        branches.BRANCH_OTHERS_KEY,
    ]


def test_series_leaves_out_buckets_with_no_tokens() -> None:
    slots = {_key("main"): 0, _key("dev"): 1}

    assert branches.compute_branch_series([_record("main", 2), _record(None, 0)], slots) == [
        _key("main")
    ]


def test_totals_per_bucket_add_every_token_type() -> None:
    slots = {_key("main"): 0}
    records = [
        {
            "project_path": "/proj/a",
            "git_branch": "main",
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_read_tokens": 3,
        },
        _record("feature/example", 4),
        _record("other-example", 5),
    ]

    assert branches.sum_tokens_by_branch(records, slots) == {
        _key("main"): 6,
        branches.BRANCH_OTHERS_KEY: 9,
    }


def test_series_labels_say_no_branch_detached_and_others_in_both_languages() -> None:
    label = branches_view.branch_series_label

    assert label(_key("main"), "en") == "a:main"
    assert label(None, "en") == "(no branch)"
    assert label(_key("HEAD"), "pt") == "a:(destacado)"
    assert label(branches.BRANCH_OTHERS_KEY, "en") == "others"
    assert label(branches.BRANCH_OTHERS_KEY, "pt") == "outros"


def test_a_branch_named_like_the_others_label_stays_a_branch() -> None:
    slots = {_key("others"): 0}

    assert branches.branch_bucket(_record("others", 1), slots) == _key("others")
    assert branches_view.branch_series_label(_key("others"), "en") == "a:others"


def test_the_same_branch_name_in_two_projects_is_two_segments() -> None:
    records = [_record("main", 5, "/proj/a"), _record("main", 3, "/proj/b")]

    slots = branches.assign_branch_slots(records)

    assert slots == {_key("main", "/proj/a"): 0, _key("main", "/proj/b"): 1}
    assert [branches_view.branch_series_label(key, "en") for key in slots] == ["a:main", "b:main"]


def test_a_record_with_no_project_is_labelled_like_the_session_list() -> None:
    key = branches.series_key_of({"project_path": None, "git_branch": "main"})

    assert key is not None
    assert branches_view.branch_series_label(key, "en").endswith(":main")
