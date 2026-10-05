"""Keyboard access and semantics of the HTML dashboard (2026-10-05 review,
items 2 and 9), checked by running the page's own JavaScript in jsdom.

The review found 63,690 elements with a pointer cursor and no role or
tabindex: from the period chips, Tab went straight into the session list's
scroll box, skipping the chart tabs, the legend chips, the group headers
and every session row. tests/js/drive_keyboard.js presses keys on the real
generated page and reports what changed; every expectation lives here.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import metrics_core as mr
import pytest
from conftest import DashboardPage
from js_runtime import require_node_with_jsdom

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def keyboard_report(dashboard_page: DashboardPage) -> dict[str, Any]:
    node = require_node_with_jsdom()
    result = subprocess.run(
        [node, str(HERE / "js" / "drive_keyboard.js")],
        env=dict(os.environ, DASHBOARD=dashboard_page.html_path),
        cwd=str((HERE / "..").resolve()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def _all_focusable(elements: list[dict[str, Any]], role: str) -> bool:
    return bool(elements) and all(
        element["role"] == role and element["tabindex"] == "0" for element in elements
    )


def test_every_tab_is_a_focusable_tab_inside_a_tablist(keyboard_report: dict[str, Any]) -> None:
    assert _all_focusable(keyboard_report["tabs"], "tab")
    assert keyboard_report["tablists"] and set(keyboard_report["tablists"]) == {"tablist"}


def test_every_clickable_legend_chip_is_a_focusable_button(keyboard_report: dict[str, Any]) -> None:
    assert _all_focusable(keyboard_report["legendChips"], "button")


def test_every_group_header_and_session_row_is_a_focusable_button(
    keyboard_report: dict[str, Any],
) -> None:
    assert _all_focusable(keyboard_report["groupHeaders"], "button")
    assert _all_focusable(keyboard_report["sessionRows"], "button")


def test_enter_on_a_group_header_expands_it_and_says_so(keyboard_report: dict[str, Any]) -> None:
    assert keyboard_report["headerExpandedBefore"] == "false"
    assert keyboard_report["enterOnHeaderPrevented"] is True
    assert keyboard_report["headerExpandedAfter"] == "true"
    assert keyboard_report["rowsCollapsedAfterEnter"] == 0


def test_space_on_a_session_row_opens_its_panel_without_scrolling_the_page(
    keyboard_report: dict[str, Any],
) -> None:
    # Review Focus 4: Space must activate, and preventDefault stops the
    # browser's own page scroll.
    assert keyboard_report["spaceOnRowPrevented"] is True
    assert keyboard_report["panelOpenAfterSpace"] is True
    assert keyboard_report["rowControls"].startswith("detail-")


def test_a_letter_key_on_a_row_is_left_alone(keyboard_report: dict[str, Any]) -> None:
    assert keyboard_report["letterOnRowPrevented"] is False


def test_selecting_a_period_presses_exactly_its_chip_and_announces_it(
    keyboard_report: dict[str, Any],
) -> None:
    assert keyboard_report["pressedChips"] == [keyboard_report["selectedPeriod"]]
    assert keyboard_report["liveText"] == mr.t(
        "notify.period", "pt", name=keyboard_report["selectedPeriodName"]
    )


def test_the_chart_mode_tabs_report_which_one_is_selected(keyboard_report: dict[str, Any]) -> None:
    assert keyboard_report["modeTabsSelected"] == ["false", "true", "false"]


def test_the_page_tabs_report_which_one_is_selected(keyboard_report: dict[str, Any]) -> None:
    assert keyboard_report["pageTabsSelected"] == ["false", "true"]


def test_a_pressed_legend_chip_is_the_only_one_marked_pressed(
    keyboard_report: dict[str, Any],
) -> None:
    assert keyboard_report["chipPressed"] == "true"
    assert keyboard_report["otherChipsPressed"]
    assert set(keyboard_report["otherChipsPressed"]) == {"false"}


def test_the_page_has_a_heading_a_main_region_and_named_charts(
    keyboard_report: dict[str, Any],
) -> None:
    assert keyboard_report["h1"] == mr.t("page_title", "pt")
    assert keyboard_report["hasMain"] is True
    assert keyboard_report["charts"]
    for chart in keyboard_report["charts"]:
        assert chart["role"] == "img"
        assert chart["label"]


def test_the_empty_detail_pane_says_what_to_do_until_a_session_is_open(
    keyboard_report: dict[str, Any],
) -> None:
    assert keyboard_report["hintText"] == mr.t("detail.hint", "pt")
    assert keyboard_report["hintDisplayBefore"] != "none"
    assert keyboard_report["hintDisplayAfterOpen"] == "none"
    assert keyboard_report["hintDisplayAfterClose"] != "none"
