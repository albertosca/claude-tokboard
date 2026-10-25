"""The branch filter of the session list, checked in jsdom.

tests/js/drive_branch_filter.js clicks the chips of a generated page and
reports what happened to the rows; every expectation lives here.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from js_runtime import require_node_with_jsdom
from test_dashboard_html_page import _empty_render_html_kwargs, _session_record

import dashboard_html

HERE = Path(__file__).resolve().parent
PROJECT = "/Users/username/my-api"


@pytest.fixture(scope="module")
def filter_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    node = require_node_with_jsdom()
    page_arguments = _empty_render_html_kwargs()
    page_arguments["records"] = [
        _session_record("main-session", PROJECT, git_branch="main"),
        _session_record("second-main-session", PROJECT, git_branch="main"),
        _session_record("feature-session", PROJECT, git_branch="feature/example"),
        _session_record("quoted-session", PROJECT, git_branch='quote"d'),
        _session_record("bare-session", PROJECT),
    ]
    page = dashboard_html.render_html(**page_arguments, lang="en")
    page_path = tmp_path_factory.mktemp("branch-filter") / "dashboard.html"
    page_path.write_text(page, encoding="utf-8")
    result = subprocess.run(
        [node, str(HERE / "js" / "drive_branch_filter.js")],
        env=dict(
            os.environ, DASHBOARD=str(page_path), OUTSIDE_PERIOD="ontem", INSIDE_PERIOD="tudo"
        ),
        cwd=str((HERE / "..").resolve()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def test_all_is_pressed_and_every_row_shows_until_a_chip_is_chosen(
    filter_report: dict[str, Any],
) -> None:
    assert sorted(filter_report["open"]["shown"]) == sorted(
        ["main-session", "second-main-session", "feature-session", "quoted-session", "bare-session"]
    )
    assert filter_report["open"]["pressed"] == [""]


def test_a_chip_hides_the_rows_of_the_other_branches_and_takes_the_press(
    filter_report: dict[str, Any],
) -> None:
    main = filter_report["main"]

    assert sorted(main["shown"]) == ["main-session", "second-main-session"]
    assert sorted(main["hidden"]) == ["bare-session", "feature-session", "quoted-session"]
    assert main["pressed"] == ["main"]


def test_a_period_change_keeps_the_filter(filter_report: dict[str, Any]) -> None:
    assert sorted(filter_report["afterPeriodSweep"]["hidden"]) == [
        "bare-session",
        "feature-session",
        "quoted-session",
    ]
    assert filter_report["afterPeriodSweep"]["pressed"] == ["main"]


def test_a_branch_name_with_a_quote_matches_by_text(filter_report: dict[str, Any]) -> None:
    assert filter_report["quoted"]["shown"] == ["quoted-session"]
    assert filter_report["quoted"]["pressed"] == ['quote"d']


def test_a_session_without_a_branch_has_its_own_chip(filter_report: dict[str, Any]) -> None:
    assert filter_report["noBranchChip"]["present"] is True
    assert filter_report["noBranchChip"]["shown"] == ["bare-session"]


def test_opening_a_filtered_out_session_by_id_clears_the_filter(
    filter_report: dict[str, Any],
) -> None:
    shown = filter_report["showHidden"]

    assert shown["hidden"] == []
    assert shown["pressed"] == [""]
    assert shown["active"] is True


def test_the_panel_of_a_session_follows_its_row(filter_report: dict[str, Any]) -> None:
    panel = filter_report["panel"]

    assert panel["open"] is True
    assert panel["hiddenWithRow"] is True
    assert panel["hiddenAfterAll"] is False
