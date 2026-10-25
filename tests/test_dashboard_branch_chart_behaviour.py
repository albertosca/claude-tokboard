"""The "By branch" tab of the trend chart, checked in jsdom.

tests/js/drive_branch_chart.js switches tabs and clicks a legend chip on a
generated page; every expectation lives here.
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


def _daily(day: str, branch: str) -> dict[str, Any]:
    record = _session_record(f"{branch}-{day}", PROJECT, git_branch=branch)
    record["timestamp_start"] = f"{day}T10:00:00Z"
    record["input_tokens"] = 100
    return record


@pytest.fixture(scope="module")
def chart_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    node = require_node_with_jsdom()
    page_arguments = _empty_render_html_kwargs()
    page_arguments["daily_chart_records"] = [
        _daily("2026-01-01", "main"),
        _daily("2026-01-02", "feature/example"),
    ]
    page_arguments["records"] = page_arguments["daily_chart_records"]
    page = dashboard_html.render_html(**page_arguments, lang="en")
    page_path = tmp_path_factory.mktemp("branch-chart") / "dashboard.html"
    page_path.write_text(page, encoding="utf-8")
    result = subprocess.run(
        [node, str(HERE / "js" / "drive_branch_chart.js")],
        env=dict(os.environ, DASHBOARD=str(page_path), PERIOD="tudo"),
        cwd=str((HERE / "..").resolve()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def test_the_project_tab_is_the_one_open_at_first(chart_report: dict[str, Any]) -> None:
    assert chart_report["initial"]["panes"] == ["trend-pane-pane-tudo"]
    assert chart_report["initial"]["tabs"] == ["trend-btn-pane-tudo"]


def test_switching_to_the_branch_tab_opens_it_alone_and_drops_the_old_highlight(
    chart_report: dict[str, Any],
) -> None:
    assert chart_report["projectHighlight"] == "group-color-1"
    switched = chart_report["afterSwitch"]

    assert switched["panes"] == ["trend-pane-branch-tudo"]
    assert switched["tabs"] == ["trend-btn-branch-tudo"]
    assert switched["highlight"] is None


def test_a_branch_legend_chip_spotlights_its_colour(chart_report: dict[str, Any]) -> None:
    assert chart_report["chip"]["highlight"] == "group-color-1"
    assert chart_report["chip"]["pressed"] == "true"
