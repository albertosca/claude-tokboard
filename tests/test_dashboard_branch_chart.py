"""The trend chart's fourth tab: tokens per day, sliced by git branch."""

import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core.branches import BRANCH_COLOR_SLOTS
from test_dashboard_html_charts import _day_record, _stack_heights_by_x, _svg_block, _titles

import dashboard_html

COLORS: dict[str | None, str] = {"/proj/a": "group-color-1"}


def _branch_record(day: str, branch: str | None, tokens: int, index: int = 0) -> dict[str, Any]:
    record = _day_record(day, index=index, input_tokens=tokens)
    record["git_branch"] = branch
    return record


def _chart(records: list[dict[str, Any]], lang: str = "en", **keywords: Any) -> str:
    return dashboard_html.render_trend_chart(records, COLORS, "p1", lang=lang, **keywords)


def _two_days() -> list[dict[str, Any]]:
    return [
        _branch_record("2026-01-01", "main", 300),
        _branch_record("2026-01-01", "feature/example", 100, index=1),
        _branch_record("2026-01-02", "main", 50),
        _branch_record("2026-01-02", None, 25, index=1),
    ]


def test_the_branch_tab_sits_beside_the_others_and_the_project_tab_stays_the_default() -> None:
    out = _chart(_two_days())

    assert 'id="trend-btn-branch-p1"' in out
    assert 'id="trend-pane-branch-p1"' in out
    assert 'aria-controls="trend-pane-branch-p1"' in out
    assert "By branch" in out
    assert 'class="tab-btn active" id="trend-btn-pane-p1"' in out
    assert 'class="trend-pane active" id="trend-pane-pane-p1"' in out


def test_the_tab_is_named_in_portuguese_too() -> None:
    assert "Por branch" in _chart(_two_days(), lang="pt")


def test_re_slicing_by_branch_keeps_every_bucket_as_tall_as_in_the_other_tabs() -> None:
    out = _chart(_two_days())
    pane = _stack_heights_by_x(_svg_block(out, "chart-pane-"))
    branch = _stack_heights_by_x(_svg_block(out, "chart-branch-"))

    assert set(branch) == set(pane)
    assert len(branch) == 2
    for position, height in pane.items():
        assert branch[position] == pytest.approx(height)


def test_tooltips_name_the_branch_the_bucket_and_the_tokens() -> None:
    titles = _titles(_svg_block(_chart(_two_days()), "chart-branch-"))

    assert any(title.startswith("main · ") and "300 tokens" in title for title in titles), titles
    assert any(
        title.startswith("feature/example · ") and "100 tokens" in title for title in titles
    ), titles
    assert any(title.startswith("(no branch) · ") and "25 tokens" in title for title in titles), (
        titles
    )


def test_a_branch_name_with_html_is_escaped_in_the_tooltip_and_the_legend() -> None:
    records = [
        _branch_record("2026-01-01", "<b>x</b>", 100),
        _branch_record("2026-01-02", "<b>x</b>", 100),
    ]
    out = _chart(records)

    assert "<b>x</b>" not in out
    assert out.count("&lt;b&gt;x&lt;/b&gt;") >= 2


def test_branches_past_the_slots_share_one_others_segment_listed_last() -> None:
    records = [
        _branch_record("2026-01-01", f"feature/example-{number}", 100 + number, index=number)
        for number in range(10)
    ] + [_branch_record("2026-01-02", "main", 5)]
    out = _chart(records)
    pane = out[out.index('id="trend-pane-branch-p1"') :]
    legend = re.findall(r'<span class="legend-swatch [^"]*"></span>([^<]*)</span>', pane)
    titles = _titles(_svg_block(out, "chart-branch-"))

    assert any(title.startswith("others · ") and "5 tokens" in title for title in titles), titles
    assert legend[-1] == "others"
    assert len(legend) == BRANCH_COLOR_SLOTS + 1


def test_slots_given_by_the_page_fix_each_branchs_colour() -> None:
    out = _chart(_two_days(), branch_slots={"feature/example": 0, "main": 1})
    svg = _svg_block(out, "chart-branch-")

    assert re.search(r'class="chart-bar group-color-1"[^>]*>\s*<title>feature/example', svg)
    assert re.search(r'class="chart-bar group-color-2"[^>]*>\s*<title>main', svg)


def test_a_chart_built_alone_settles_its_own_slots_by_spend() -> None:
    svg = _svg_block(_chart(_two_days()), "chart-branch-")

    assert re.search(r'class="chart-bar group-color-1"[^>]*>\s*<title>main', svg)


def test_a_single_branch_still_draws_one_series() -> None:
    records = [
        _branch_record("2026-01-01", "main", 100),
        _branch_record("2026-01-02", "main", 100),
    ]
    out = _chart(records)

    assert len(_titles(_svg_block(out, "chart-branch-"))) == 2
    assert "(no branch)" not in out


def test_clicking_a_branch_band_or_chip_spotlights_its_colour() -> None:
    out = _chart(_two_days())

    assert "highlightBranch('group-color-1', event)" in out
    assert 'data-highlight-key="group-color-1"' in out
