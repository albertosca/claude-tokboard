"""The branches panel on the HTML dashboard."""

from typing import Any

import metrics_core as mr
from test_dashboard_html_page import _empty_render_html_kwargs, _session_record

import dashboard_html

PROJECT = "/Users/username/my-api"


def _view(lang: str = "en") -> dict[str, Any]:
    breakdown = [
        {
            "group": PROJECT,
            "total": 100,
            "branches": [
                {"branch": "main", "sessions": 3, "total": 75},
                {"branch": "feature/example", "sessions": 1, "total": 25},
            ],
        }
    ]
    return mr.describe_branches(breakdown, lang)


def test_the_panel_is_a_labelled_section_with_a_captioned_table_per_project() -> None:
    page = dashboard_html.render_branches(_view(), "tudo")

    assert 'aria-labelledby="branches-heading-tudo"' in page
    assert 'id="branches-heading-tudo"' in page
    assert page.count("<caption>") == 1
    assert "<caption>my-api: tokens by branch</caption>" in page
    assert page.count('<th scope="col">') == 4
    assert 'class="table-scroll" role="region"' in page


def test_the_table_carries_the_view_rows() -> None:
    view = _view()
    page = dashboard_html.render_branches(view, "tudo")

    for row in view["projects"][0]["rows"]:
        for key in ("branch", "sessions", "tokens", "share"):
            assert f"<td>{row[key]}</td>" in page


def test_the_panel_is_in_portuguese_when_asked() -> None:
    page = dashboard_html.render_branches(_view("pt"), "tudo")

    assert "Tokens por branch" in page
    assert "Parcela" in page
    assert "Share" not in page


def test_an_empty_view_renders_nothing() -> None:
    assert dashboard_html.render_branches(mr.describe_branches([], "en"), "tudo") == ""


def test_text_is_escaped() -> None:
    view = _view()
    view["projects"][0]["rows"][0]["branch"] = "<script>alert(1)</script>"

    page = dashboard_html.render_branches(view, "tudo")

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_render_html_has_one_block_per_period_and_only_the_default_is_visible() -> None:
    page_arguments = _empty_render_html_kwargs()
    records = [
        _session_record("a", PROJECT, git_branch="main"),
        _session_record("b", PROJECT, git_branch="feature/example"),
    ]
    page_arguments["records"] = records

    page = dashboard_html.render_html(**page_arguments, lang="en")

    visible = '<div class="period-block" data-period="tudo"><section class="weekly-meter branches"'
    assert visible in page
    assert page.index('id="branches-heading-tudo"') < page.index('class="main-row"')
    for period in mr.PERIODS:
        window = page_arguments["period_windows"][period.id]
        holds_records = bool(mr.filter_sessions_by_window(records, window))
        assert (f'id="branches-heading-{period.id}"' in page) is holds_records


def test_a_period_with_one_branch_per_project_has_no_section() -> None:
    page_arguments = _empty_render_html_kwargs()
    page_arguments["records"] = [_session_record("a", PROJECT, git_branch="main")]

    page = dashboard_html.render_html(**page_arguments, lang="en")

    assert "branches-heading" not in page
