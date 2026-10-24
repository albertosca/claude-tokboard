"""Detail panels are built on first open, checked in jsdom (2026-10-24).

tests/js/drive_detail_panels.js opens sessions on a generated page and
reports what the page script built; every expectation lives here. The page
holds a session with subagents (pre-rendered in the JSON block), plain
sessions (cloned from the shared template) and one with an empty id.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import metrics_core as metrics_core_module
import pytest
from js_runtime import require_node_with_jsdom
from test_dashboard_html_page import _empty_render_html_kwargs, _session_record
from test_html_detail_on_demand import HOSTILE_LABEL, _subagent

import dashboard_html

HERE = Path(__file__).resolve().parent
RICH = "rich-session"
PLAIN = "plain-session"
OTHER_PLAIN = "other-plain-session"


@pytest.fixture(scope="module")
def panel_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    node = require_node_with_jsdom()
    keyword_arguments = _empty_render_html_kwargs()
    keyword_arguments["records"] = [
        _session_record(session_id, "/work/proj-a") for session_id in (RICH, PLAIN, OTHER_PLAIN, "")
    ]
    keyword_arguments["subagents_by_session"] = {RICH: [_subagent(RICH, HOSTILE_LABEL)]}
    page = dashboard_html.render_html(**keyword_arguments, lang="pt")
    page_path = tmp_path_factory.mktemp("detail-panels") / "dashboard.html"
    page_path.write_text(page, encoding="utf-8")
    result = subprocess.run(
        [node, str(HERE / "js" / "drive_detail_panels.js")],
        env=dict(
            os.environ,
            DASHBOARD=str(page_path),
            PLAIN_ID=PLAIN,
            OTHER_PLAIN_ID=OTHER_PLAIN,
            RICH_ID=RICH,
            OUTSIDE_PERIOD="ontem",
            INSIDE_PERIOD="tudo",
        ),
        cwd=str((HERE / "..").resolve()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    report: dict[str, Any] = json.loads(result.stdout)
    return report


def test_no_panel_exists_before_its_session_is_opened(panel_report: dict[str, Any]) -> None:
    assert panel_report["before"] == {"plain": 0, "rich": 0}


def test_opening_a_plain_session_builds_its_panel_from_the_template(
    panel_report: dict[str, Any],
) -> None:
    plain = panel_report["plain"]
    assert plain["count"] == 1
    assert plain["open"] is True
    assert plain["heading"] == PLAIN
    assert plain["controls"] == f"detail-{PLAIN}"
    assert plain["periods"] == plain["rowPeriods"]
    assert plain["emptyLine"] == metrics_core_module.t("detail.no_subagents", "pt")


def test_the_close_button_of_a_built_panel_closes_it(panel_report: dict[str, Any]) -> None:
    assert panel_report["plain"]["openAfterCloseButton"] is False


def test_reopening_reuses_the_same_panel_and_never_repeats_an_id(
    panel_report: dict[str, Any],
) -> None:
    assert panel_report["plain"]["countAfterReopen"] == 1
    assert panel_report["plain"]["sameElementAfterReopen"] is True


def test_opening_a_session_with_subagents_shows_its_pre_rendered_rows(
    panel_report: dict[str, Any],
) -> None:
    rich = panel_report["rich"]
    assert rich["count"] == 1
    assert rich["open"] is True
    assert rich["subagentRows"] == 1
    assert rich["controls"] == f"detail-{RICH}"
    assert HOSTILE_LABEL in rich["text"], "the label must round-trip as text, not markup"
    assert rich["scriptsInside"] == 0


def test_two_sessions_in_turn_leave_one_panel_open(panel_report: dict[str, Any]) -> None:
    assert panel_report["rich"]["openPanelsAfterSwitch"] == 1


def test_a_built_panel_follows_its_session_out_of_the_period_and_back(
    panel_report: dict[str, Any],
) -> None:
    period = panel_report["period"]
    assert period["hiddenOutside"] is True
    assert period["hiddenBack"] is False
    assert period["stillOpen"] is True


def test_a_panel_born_outside_the_active_period_is_born_hidden(
    panel_report: dict[str, Any],
) -> None:
    assert panel_report["period"]["bornHidden"] is True


def test_a_session_with_an_empty_id_does_not_throw(panel_report: dict[str, Any]) -> None:
    assert panel_report["emptyId"] == {"threw": False, "count": 1, "open": True}


def test_opening_an_unknown_session_builds_nothing(panel_report: dict[str, Any]) -> None:
    unknown = panel_report["unknownSession"]
    assert unknown["panelsAfter"] == unknown["panelsBefore"]
    assert unknown["open"] == 0
