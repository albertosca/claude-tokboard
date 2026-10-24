"""The generated page's headings never skip a level."""

import re
from itertools import pairwise
from pathlib import Path

from test_language_leak import fixture_page_html

import dashboard_html

HEADING_RE = re.compile(r"<h([1-6])[\s>]")

SUBAGENT = {
    "session_id": "s-1",
    "agent_id": "a-1",
    "agent_type": "general-purpose",
    "input_tokens": 5,
    "output_tokens": 5,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "duration_seconds": 3.0,
    "timestamp_start": "2026-01-01T09:00:00",
}


def jumps_in(html_text: str, *, first_level: int) -> set[tuple[int, int]]:
    levels = [first_level] + [int(level) for level in HEADING_RE.findall(html_text)]
    return {(previous, current) for previous, current in pairwise(levels) if current > previous + 1}


def test_the_page_never_skips_a_heading_level(tmp_path: Path) -> None:
    # 2026-10-24, found by a Lighthouse/axe audit: the page went from its
    # one h1 straight to h3 (project chart title, session detail panels), so
    # a screen reader's heading list had no h2.
    page = fixture_page_html(tmp_path, "en")

    assert HEADING_RE.findall(page)[0] == "1", "the page must open with its h1"
    assert not jumps_in(page, first_level=1)
    assert "2" in HEADING_RE.findall(page), "fixture must reach the h2s"


def test_a_detail_panel_with_subagents_nests_its_list_one_level_down() -> None:
    # The panel opens with an h2 (sitting under the page's h1) and its
    # subagent list was an h4, two levels below it.
    panel = dashboard_html.render_detail_panel("s-1", [SUBAGENT], True, lang="en")

    assert HEADING_RE.findall(panel) == ["2", "3"]
