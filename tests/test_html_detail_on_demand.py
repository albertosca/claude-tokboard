"""The page ships session detail panels on demand, not one per session.

2026-10-24: the full-history page held 3,775 detail panels (49% of its
116,462 elements) and the axe collector timed out walking them. Panels that
carry subagents now travel as inert JSON, the rest share one template, and
the page script builds `detail-{id}` on first open.
"""

import json
import re
from pathlib import Path
from typing import Any

import metrics_core as metrics_core_module
from test_dashboard_html_page import _empty_render_html_kwargs, _session_record
from test_language_leak import fixture_page_html

import dashboard_html

HOSTILE_LABEL = "</script><!-- <script>alert(1)</script>"
SOURCE_RE = re.compile(r'<script type="application/json" id="detail-source">(.*?)</script>', re.S)
INERT_RE = re.compile(r"<script.*?</script>|<template.*?</template>", re.S)


def _subagent(session_id: str, label: str = "reviewer") -> dict[str, Any]:
    return {
        "session_id": session_id,
        "agent_id": "agent-1",
        "agent_type": "general-purpose",
        "agent_label": label,
        "input_tokens": 5,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "duration_seconds": 3.0,
        "timestamp_start": "2026-08-10T09:00:00Z",
    }


def _page(
    subagents_by_session: dict[str, list[dict[str, Any]]], *session_ids: str, lang: str = "en"
) -> str:
    keyword_arguments = _empty_render_html_kwargs()
    keyword_arguments["records"] = [
        _session_record(session_id, "/work/proj-a") for session_id in session_ids
    ]
    keyword_arguments["subagents_by_session"] = subagents_by_session
    return dashboard_html.render_html(**keyword_arguments, lang=lang)


def _source(page: str) -> dict[str, str]:
    match = SOURCE_RE.search(page)
    assert match, "the page must carry its detail-source block"
    loaded: dict[str, str] = json.loads(match.group(1))
    return loaded


def _element_count(page: str) -> int:
    return len(re.findall(r"<[a-zA-Z]", page))


def test_a_session_without_subagents_emits_no_panel() -> None:
    page = _page({}, "plain-1", "plain-2")

    assert 'id="detail-plain-1"' not in page
    assert 'id="detail-plain-2"' not in page


def test_a_session_with_subagents_travels_in_the_source_not_in_the_live_markup() -> None:
    page = _page({"rich-1": [_subagent("rich-1")]}, "rich-1", "plain-1")

    assert 'id="detail-rich-1"' not in INERT_RE.sub("", page)
    assert list(_source(page)) == ["rich-1"]
    assert 'id="detail-rich-1"' in _source(page)["rich-1"]
    assert "reviewer" in _source(page)["rich-1"]


def test_the_empty_template_appears_once_and_speaks_the_page_language() -> None:
    page = _page({}, "plain-1", "plain-2", lang="pt")

    assert page.count('id="detail-empty-template"') == 1
    template = re.search(r'<template id="detail-empty-template">(.*?)</template>', page, re.S)
    assert template
    assert metrics_core_module.t("detail.no_subagents", "pt") in template.group(1)
    assert metrics_core_module.t("detail.no_subagents", "en") not in template.group(1)


def test_a_hostile_label_cannot_close_the_data_block_and_round_trips() -> None:
    page = _page({"rich-1": [_subagent("rich-1", HOSTILE_LABEL)]}, "rich-1")

    block = SOURCE_RE.search(page)
    assert block
    assert "</" not in block.group(1)
    assert "<!--" not in block.group(1)
    assert "alert(1)" in _source(page)["rich-1"]


def test_a_page_with_no_sessions_has_no_detail_source() -> None:
    page = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="en")

    assert 'id="detail-source"' not in page


def test_a_row_carries_aria_controls_only_when_asked() -> None:
    record = _session_record("row-1", "/work/proj-a")
    with_controls = dashboard_html.render_session_row(record, False, {}, 0, lang="en")
    without_controls = dashboard_html.render_session_row(
        record, False, {}, 0, controls_panel=False, lang="en"
    )

    assert 'aria-controls="detail-row-1"' in with_controls
    assert "aria-controls" not in without_controls


def test_no_row_of_the_page_points_at_a_panel_that_is_not_in_the_markup(tmp_path: Path) -> None:
    page = fixture_page_html(tmp_path, "en")
    live_markup = INERT_RE.sub("", page)

    for target in re.findall(r'aria-controls="(detail-[^"]*)"', live_markup):
        assert f'id="{target}"' in live_markup


def test_live_dom_grows_with_rows_only() -> None:
    few = _page({}, *[f"session-{index}" for index in range(5)])
    many = _page({}, *[f"session-{index}" for index in range(50)])

    per_session = (_element_count(many) - _element_count(few)) / 45
    # Measured 2026-10-24: a row is 11 elements; its panel added 4 more.
    assert per_session <= 12, "a session without subagents must cost its row only"
