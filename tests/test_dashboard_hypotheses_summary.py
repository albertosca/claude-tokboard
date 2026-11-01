"""The weekly-limit summary under the quota line, and its closed details."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from cache_causes_fixtures import causes_for
from js_runtime import require_node_with_jsdom
from metrics_core import cache_causes_view, weekly_meter_view
from test_dashboard_html_page import _empty_render_html_kwargs
from weekly_meter_fixtures import meter_for

import dashboard_html

HERE = Path(__file__).resolve().parent


def _page(lang: str = "en", *, with_meter: bool = True) -> str:
    weekly = None
    if with_meter:
        weekly = weekly_meter_view.describe_weekly_meter(meter_for([4_000, 9_000], 3_000), lang)
        weekly["causes"] = cache_causes_view.describe_cache_causes(causes_for(), lang)
    return dashboard_html.render_html(**_empty_render_html_kwargs(), lang=lang, weekly_meter=weekly)


def _sessions_pane(page: str) -> str:
    return page.split('id="tab-sessions"', 1)[1].split('id="tab-panorama"', 1)[0]


def test_there_is_no_third_tab() -> None:
    page = _page()

    assert "tab-hypotheses" not in page
    assert "Hypotheses</span>" not in page
    assert page.count('id="tab-btn-') == 2


def test_the_summary_sits_under_the_quota_line_and_the_chips() -> None:
    sessions = _sessions_pane(_page())

    assert sessions.index('class="quota-line"') < sessions.index('class="comp-legend"')
    assert sessions.index('class="comp-legend"') < sessions.index('class="hyp-summary"')
    assert "Weekly limit: " in sessions
    assert "Cache creation: " in sessions


def test_the_summary_is_in_portuguese_for_a_portuguese_page() -> None:
    sessions = _sessions_pane(_page("pt"))

    assert "Limite semanal: " in sessions


def test_the_cards_sit_behind_one_closed_disclosure_after_the_summary() -> None:
    sessions = _sessions_pane(_page())

    assert sessions.count('class="meter-details hypotheses-details"') == 1
    assert '<details class="meter-details hypotheses-details" open' not in sessions
    assert sessions.index('class="hyp-summary"') < sessions.index("hypotheses-details")
    for heading in ("weekly-meter-heading", "cache-causes-heading"):
        assert sessions.index("hypotheses-details") < sessions.index(heading)


def test_without_meter_data_nothing_is_added() -> None:
    sessions = _sessions_pane(_page(with_meter=False))

    assert "hyp-summary" not in sessions
    assert "hypotheses-details" not in sessions


@pytest.fixture(scope="module")
def tabs_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    node = require_node_with_jsdom()
    page_path = tmp_path_factory.mktemp("hypotheses") / "dashboard.html"
    page_path.write_text(_page(), encoding="utf-8")
    result = subprocess.run(
        [node, str(HERE / "js" / "drive_tabs.js")],
        env=dict(os.environ, DASHBOARD=str(page_path)),
        cwd=str((HERE / "..").resolve()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def test_the_page_opens_on_sessions_with_two_tabs(tabs_report: dict[str, Any]) -> None:
    assert tabs_report["initial"]["activePanes"] == ["tab-sessions"]
    assert tabs_report["initial"]["tabOrder"] == ["tab-btn-sessions", "tab-btn-panorama"]


def test_clicking_a_tab_shows_its_pane_and_only_its_pane(tabs_report: dict[str, Any]) -> None:
    assert tabs_report["afterClick"]["activePanes"] == ["tab-panorama"]
    assert tabs_report["afterClick"]["selectedTabs"] == ["tab-btn-panorama"]


def test_the_right_arrow_wraps_to_the_first_tab(tabs_report: dict[str, Any]) -> None:
    assert tabs_report["afterWrap"]["selectedTabs"] == ["tab-btn-sessions"]


def test_a_summary_opens_its_disclosure(tabs_report: dict[str, Any]) -> None:
    assert tabs_report["summaryFound"] is True
    assert tabs_report["openBefore"] is False
    assert tabs_report["openAfter"] is True
