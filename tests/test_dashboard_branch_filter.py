"""The branch filter of the session list on the HTML dashboard (markup)."""

import re
from typing import Any

import metrics_core as mr
from test_dashboard_html_page import _empty_render_html_kwargs, _session_record

import dashboard_html

PROJECT = "/Users/username/my-api"
QUIET_PROJECT = "/Users/username/blog-site"


def _view(*names: str | None) -> dict[str, Any]:
    records = [{"git_branch": name} for name in names]
    view = mr.describe_branch_filter(records, "my-api", "en")
    assert view is not None
    return view


def _page(records: list[dict[str, Any]], lang: str = "en") -> str:
    page_arguments = _empty_render_html_kwargs()
    page_arguments["records"] = records
    return dashboard_html.render_html(**page_arguments, lang=lang)


def test_no_view_renders_no_bar() -> None:
    assert dashboard_html.render_branch_filter(3, None) == ""


def test_the_bar_is_a_labelled_group_of_pressable_chips() -> None:
    bar = dashboard_html.render_branch_filter(3, _view("main", "main", "feature/example"))

    assert 'class="branch-filter" role="group" aria-label="Filter my-api by branch"' in bar
    assert 'data-group="3"' in bar
    assert bar.count("<button") == 3
    assert bar.count('aria-pressed="true"') == 1
    assert re.search(r'data-branch="" aria-pressed="true"[^>]*>All</button>', bar)
    assert 'data-branch="main"' in bar
    assert 'data-branch="feature/example"' in bar
    assert "main · 2" in bar


def test_a_hostile_branch_name_is_escaped_in_the_attribute_and_the_label() -> None:
    bar = dashboard_html.render_branch_filter(0, _view('x"><script>1</script>', "main"))

    assert "<script>" not in bar
    assert 'data-branch="x&quot;&gt;&lt;script&gt;1&lt;/script&gt;"' in bar


def test_the_bar_sits_right_after_the_header_of_a_group_with_two_branches() -> None:
    page = _page(
        [
            _session_record("a", PROJECT, git_branch="main"),
            _session_record("b", PROJECT, git_branch="feature/example"),
            _session_record("c", QUIET_PROJECT, git_branch="main"),
        ]
    )

    assert page.count('class="branch-filter"') == 1
    before_bar = page[: page.index('<div class="branch-filter"')]
    header_start = before_bar.rindex('<div class="group-header')
    assert "my-api" in before_bar[header_start:]
    assert before_bar[header_start:].count("<div") == 1
    assert before_bar.endswith("</span></div>")


def test_session_rows_get_no_attribute_of_their_own_for_the_filter() -> None:
    page = _page(
        [
            _session_record("a", PROJECT, git_branch="main"),
            _session_record("b", PROJECT, git_branch="feature/example"),
        ]
    )

    row = re.search(r'<div class="session-row[^>]*id="row-a"[^>]*>', page)
    assert row is not None
    assert "data-branch" not in row.group(0)


def test_the_bar_is_in_portuguese_when_asked() -> None:
    page = _page(
        [
            _session_record("a", PROJECT, git_branch="main"),
            _session_record("b", PROJECT, git_branch="feature/example"),
        ],
        lang="pt",
    )

    assert "Filtrar my-api por branch" in page
    assert ">Todas</button>" in page
