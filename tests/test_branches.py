"""The per-project branch breakdown both dashboards render from."""

from typing import Any

from metrics_core import branches


def _session(
    project: str | None, branch: str | None, tokens: int = 10, **extra: int
) -> dict[str, Any]:
    return {
        "project_path": project,
        "git_branch": branch,
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        **extra,
    }


def test_a_worktree_folds_into_its_parent_project() -> None:
    records = [
        _session("/Users/username/my-api", "main", 30),
        _session("/Users/username/my-api/.claude/worktrees/spike", "feature/example", 70),
    ]

    breakdown = branches.compute_branch_breakdown(records)

    assert [group["group"] for group in breakdown] == ["/Users/username/my-api"]
    assert [(row["branch"], row["total"]) for row in breakdown[0]["branches"]] == [
        ("feature/example", 70),
        ("main", 30),
    ]


def test_a_project_with_one_branch_is_left_out() -> None:
    records = [
        _session("/Users/username/blog-site", "main"),
        _session("/Users/username/blog-site", "main"),
    ]

    assert branches.compute_branch_breakdown(records) == []


def test_a_missing_or_empty_branch_is_the_same_branch() -> None:
    records = [
        _session("/Users/username/my-api", None, 1),
        _session("/Users/username/my-api", "", 2),
        _session("/Users/username/my-api", "HEAD", 4),
    ]

    breakdown = branches.compute_branch_breakdown(records)

    rows = {row["branch"]: row for row in breakdown[0]["branches"]}
    assert set(rows) == {None, "HEAD"}
    assert rows[None]["sessions"] == 2
    assert rows[None]["total"] == 3


def test_every_token_kind_counts_toward_the_total() -> None:
    record = _session("/Users/username/my-api", "main", 1, output_tokens=2)
    record["cache_read_tokens"] = 4
    record["cache_creation_tokens"] = 8
    records = [record, _session("/Users/username/my-api", "dev", 0)]

    breakdown = branches.compute_branch_breakdown(records)

    assert breakdown[0]["total"] == 15
    assert breakdown[0]["branches"][0] == {"branch": "main", "sessions": 1, "total": 15}


def test_groups_and_branches_are_ordered_by_total_then_name() -> None:
    records = [
        _session("/Users/username/small", "b", 1),
        _session("/Users/username/small", "a", 1),
        _session("/Users/username/big", "x", 50),
        _session("/Users/username/big", "y", 40),
    ]

    breakdown = branches.compute_branch_breakdown(records)

    assert [group["group"] for group in breakdown] == [
        "/Users/username/big",
        "/Users/username/small",
    ]
    assert [row["branch"] for row in breakdown[1]["branches"]] == ["a", "b"]


def test_the_no_project_group_is_kept() -> None:
    records = [_session(None, "main"), _session(None, "dev")]

    assert branches.compute_branch_breakdown(records)[0]["group"] is None
