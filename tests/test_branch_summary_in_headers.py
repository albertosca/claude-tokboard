"""The branch split on a project group's header: the HTML page and the TUI tree."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_dashboard_html_page import _empty_render_html_kwargs, _session_record  # noqa: E402

import dashboard_html  # noqa: E402
import dashboard_tui as tui  # noqa: E402

PROJECT = "/Users/username/my-api"


def test_the_html_header_carries_the_split_in_every_period_block() -> None:
    page_arguments = _empty_render_html_kwargs()
    page_arguments["records"] = [
        _session_record("a", PROJECT, tokens=75, git_branch="main"),
        _session_record("b", PROJECT, tokens=25, git_branch="feature/example"),
    ]

    page = dashboard_html.render_html(**page_arguments, lang="en")

    assert "tokens) · main 75% · feature/example 25%</span>" in page


def test_the_html_header_has_no_split_for_a_one_branch_project_and_escapes_names() -> None:
    page_arguments = _empty_render_html_kwargs()
    page_arguments["records"] = [
        _session_record("a", "/Users/username/blog-site", git_branch="main"),
        _session_record("b", PROJECT, tokens=60, git_branch="<script>"),
        _session_record("c", PROJECT, tokens=40, git_branch="main"),
    ]

    page = dashboard_html.render_html(**page_arguments, lang="en")

    assert "&lt;script&gt; 60% · main 40%" in page
    assert "<script> 60%" not in page


def test_the_standalone_branches_section_is_gone() -> None:
    page_arguments = _empty_render_html_kwargs()
    page_arguments["records"] = [
        _session_record("a", PROJECT, git_branch="main"),
        _session_record("b", PROJECT, git_branch="feature/example"),
    ]

    page = dashboard_html.render_html(**page_arguments, lang="en")

    assert "branches-heading" not in page
    assert "Tokens by branch" not in page


def _group_state(branch: str | None) -> dict[str, object]:
    return {
        "label": "my-api",
        "branch": branch,
        "subagents_by_session": {},
        "records": [
            _session_record("a", PROJECT, tokens=75, git_branch="main"),
            _session_record("b", PROJECT, tokens=25, git_branch="[wip]"),
        ],
    }


def test_the_tui_label_carries_the_split_until_the_group_is_narrowed() -> None:
    everything = tui.group_label(_group_state(None), mr, lang="en")
    narrowed = tui.group_label(_group_state("main"), mr, lang="en")

    assert everything == r"my-api (2 sessions · 100 tokens) · main 75% · \[wip] 25%"
    assert narrowed == r"my-api (1 session · 75 tokens) \[main]"
