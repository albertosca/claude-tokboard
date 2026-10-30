"""The Hypotheses tab: the third page-level tab, holding the meter and the causes."""

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


def test_the_hypotheses_tab_holds_the_meter_and_the_causes() -> None:
    page = _page()

    sessions = page.split('id="tab-sessions"', 1)[1].split('id="tab-panorama"', 1)[0]
    hypotheses = page.split('id="tab-hypotheses"', 1)[1]
    for heading in ("weekly-meter-heading", "cache-causes-heading"):
        assert heading not in sessions
        assert heading in hypotheses
    assert hypotheses.index("weekly-meter-heading") < hypotheses.index("cache-causes-heading")


def test_the_tab_bar_lists_sessions_overview_hypotheses_in_that_order() -> None:
    page = _page()

    order = [page.index(f'id="tab-btn-{name}"') for name in ("sessions", "panorama", "hypotheses")]
    assert order == sorted(order)
    assert 'aria-controls="tab-hypotheses"' in page
    assert 'aria-labelledby="tab-btn-hypotheses"' in page
    assert ">Hypotheses</span>" in page
    assert ">Hipóteses</span>" in _page("pt")


def test_the_new_tab_starts_hidden_and_sessions_stays_the_default() -> None:
    page = _page()

    assert 'class="tab-pane active" id="tab-sessions"' in page
    assert 'class="tab-pane" id="tab-hypotheses"' in page
    assert 'class="tab-btn" id="tab-btn-hypotheses"' in page


def test_without_meter_data_the_tab_says_so_in_one_line() -> None:
    hypotheses = _page(with_meter=False).split('id="tab-hypotheses"', 1)[1]

    assert "No weekly-limit data yet." in hypotheses
    assert "<details" not in hypotheses
    assert "cause-bar" not in hypotheses


def test_without_meter_data_the_tab_says_so_in_portuguese() -> None:
    hypotheses = _page("pt", with_meter=False).split('id="tab-hypotheses"', 1)[1]

    assert "Ainda não há dados do limite semanal." in hypotheses


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


def test_the_page_opens_on_sessions_with_three_tabs(tabs_report: dict[str, Any]) -> None:
    assert tabs_report["initial"]["activePanes"] == ["tab-sessions"]
    assert tabs_report["initial"]["tabOrder"] == [
        "tab-btn-sessions",
        "tab-btn-panorama",
        "tab-btn-hypotheses",
    ]


def test_clicking_the_tab_shows_its_pane_and_only_its_pane(tabs_report: dict[str, Any]) -> None:
    assert tabs_report["afterClick"]["activePanes"] == ["tab-hypotheses"]
    assert tabs_report["afterClick"]["selectedTabs"] == ["tab-btn-hypotheses"]


def test_arrow_keys_reach_the_new_tab_and_wrap_to_the_first(tabs_report: dict[str, Any]) -> None:
    assert tabs_report["afterArrowRight"]["selectedTabs"] == ["tab-btn-hypotheses"]
    assert tabs_report["afterWrap"]["selectedTabs"] == ["tab-btn-sessions"]


def test_a_summary_opens_its_disclosure(tabs_report: dict[str, Any]) -> None:
    assert tabs_report["summaryFound"] is True
    assert tabs_report["openBefore"] is False
    assert tabs_report["openAfter"] is True
