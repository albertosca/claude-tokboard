"""The "other branches" chip of the branch filter, checked in jsdom.

tests/js/drive_branch_rest.js clicks the chip of a generated page and reports
what happened to the rows; every expectation lives here.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from js_runtime import require_node_with_jsdom
from metrics_core import branches_view
from test_dashboard_html_page import _empty_render_html_kwargs, _session_record

import dashboard_html

HERE = Path(__file__).resolve().parent
PROJECT = "/Users/username/my-api"


@pytest.fixture(scope="module")
def rest_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    node = require_node_with_jsdom()
    page_arguments = _empty_render_html_kwargs()
    busy = branches_view.BRANCHES_SHOWN
    # The busy branches have two sessions each, so the two quiet ones are the
    # branches past the chip cap whatever the tie-break by name does.
    records = [
        _session_record(f"busy-{number}-{copy}", PROJECT, git_branch=f"busy-{number}")
        for number in range(busy)
        for copy in range(2)
    ]
    records += [
        _session_record("quiet-a", PROJECT, git_branch="quiet-a"),
        _session_record("quiet-b", PROJECT, git_branch="quiet-b"),
    ]
    page_arguments["records"] = records
    page = dashboard_html.render_html(**page_arguments, lang="en")
    page_path = tmp_path_factory.mktemp("branch-rest") / "dashboard.html"
    page_path.write_text(page, encoding="utf-8")
    result = subprocess.run(
        [node, str(HERE / "js" / "drive_branch_rest.js")],
        env=dict(os.environ, DASHBOARD=str(page_path)),
        cwd=str((HERE / "..").resolve()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def test_the_rest_chip_shows_only_the_sessions_of_the_branches_past_the_cap(
    rest_report: dict[str, Any],
) -> None:
    assert sorted(rest_report["rest"]["shown"]) == ["quiet-a", "quiet-b"]
    assert rest_report["rest"]["pressed"] == ["2 other branches · 2"]


def test_all_brings_every_session_back_after_the_rest_chip(rest_report: dict[str, Any]) -> None:
    assert len(rest_report["all"]["shown"]) == 2 * branches_view.BRANCHES_SHOWN + 2


def test_the_bar_is_all_plus_the_capped_chips_plus_the_rest_chip(
    rest_report: dict[str, Any],
) -> None:
    assert rest_report["chips"] == 1 + branches_view.BRANCHES_SHOWN + 1
