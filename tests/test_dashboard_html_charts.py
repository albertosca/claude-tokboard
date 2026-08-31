"""Covers dashboard_html.py's two chart renderers: render_trend_chart (the
module's largest function) and render_project_totals_chart.

This is the highest-risk module in the coverage initiative: every geometry
bug this project has shipped lived in these two functions (a shared-scale
regression that starved one tab's bars, coordinates that went negative and
overflowed the chart, an 8px bar-width floor a later width bump made
unreachable). Every numeric constant and formula asserted below was derived
by RUNNING the function and reading its actual output, never by inferring
from source - see the task brief's rule 1, and see the module comments at
the cited line numbers for the incidents that produced each guard.

Assertions parse the SVG structurally (attribute-order-independent regexes)
and check invariants - element counts, coordinate bounds, class membership -
rather than pinning byte-exact markup, so a cosmetic markup change (a new
attribute, reordered attributes) does not fail this suite. See
test_dashboard_html_rows.py's history: an over-precise regex anchored on a
literal attribute sequence broke on a harmless attribute addition.
"""

import itertools
import re
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import dashboard_html

# ---------------------------------------------------------------------------
# Structural SVG parsing helpers - attribute-order independent, so a cosmetic
# markup change (new attribute, reordered attributes) never fails a test
# that isn't actually about that attribute (lesson C in the task brief).
# ---------------------------------------------------------------------------


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'{name}="([^"]*)"', attrs)
    return m.group(1) if m else None


def _num_attr(attrs: str, name: str) -> float | None:
    val = _attr(attrs, name)
    return float(val) if val is not None else None


def _parse_elements(fragment: str, tag: str) -> list[dict[str, Any]]:
    """Every <tag ...> in fragment, as a dict of its attributes (numeric
    where the value parses as a float, raw string otherwise)."""
    elements = []
    for m in re.finditer(rf"<{tag}\b([^>]*)>", fragment):
        attrs = m.group(1)
        parsed: dict[str, Any] = {}
        for name in ("x", "y", "x1", "y1", "x2", "y2", "width", "height"):
            val = _num_attr(attrs, name)
            if val is not None:
                parsed[name] = val
        cls = _attr(attrs, "class")
        if cls is not None:
            parsed["class"] = cls
        elements.append(parsed)
    return elements


def _svg_block(out: str, id_prefix: str) -> str:
    """Extract one full <svg id="{id_prefix}...">...</svg> block. Trend
    charts render two svgs (pane/tokens) per period; id_prefix picks one."""
    m = re.search(
        rf'(<svg viewBox="0 0 [\d.]+ [\d.]+" class="[^"]*" id="{re.escape(id_prefix)}[^"]*"'
        r"[^>]*>.*?</svg>)",
        out,
        re.S,
    )
    assert m, f"no <svg id={id_prefix!r}...> block found"
    return m.group(1)


def _viewbox(svg_fragment: str) -> tuple[float, float]:
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg_fragment)
    assert m, "no viewBox found"
    return float(m.group(1)), float(m.group(2))


def _assert_no_coordinate_escapes(svg_fragment: str) -> None:
    """The single most valuable invariant in this module: nothing drawn
    goes negative or spills past the declared canvas. This is exactly the
    shape of the two real bugs the module's own comments describe (a
    split-denominator regression and a since-fixed coordinate overflow)."""
    width, height = _viewbox(svg_fragment)
    for rect in _parse_elements(svg_fragment, "rect"):
        assert rect["x"] >= -0.01, rect
        assert rect["y"] >= -0.01, rect
        assert rect["width"] >= -0.01, rect
        assert rect["height"] >= -0.01, rect
        assert rect["x"] + rect["width"] <= width + 0.5, rect
        assert rect["y"] + rect["height"] <= height + 0.5, rect
    for line in _parse_elements(svg_fragment, "line"):
        assert -0.01 <= line["x1"] <= width + 0.5, line
        assert -0.01 <= line["x2"] <= width + 0.5, line
        assert -0.01 <= line["y1"] <= height + 0.5, line
        assert -0.01 <= line["y2"] <= height + 0.5, line
    for text in _parse_elements(svg_fragment, "text"):
        if "x" in text:
            assert -0.01 <= text["x"] <= width + 0.5, text
        if "y" in text:
            assert -0.01 <= text["y"] <= height + 0.5, text


# ---------------------------------------------------------------------------
# Record builders
# ---------------------------------------------------------------------------


def _year_records(
    n: int,
    start_year: int = 2000,
    input_tokens: int = 100,
    output_tokens: int = 100,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    project: str | None = "/proj/a",
) -> list[dict[str, Any]]:
    """n records, one per distinct calendar year starting at start_year.
    A span of years forces aggregate_by_day's granularity choice to "year"
    (any span over ~3 years), which buckets one-record-per-year, giving
    exact control over the bucket count n independent of any 45-day/
    365-day granularity ceiling - the cleanest way to reach arbitrary n."""
    records = []
    for i in range(n):
        year = start_year + i
        records.append(
            {
                "session_id": f"s{i}",
                "project_path": project,
                "timestamp_start": f"{year:04d}-01-01T10:00:00Z",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read_tokens,
                "cache_creation_tokens": cache_creation_tokens,
            }
        )
    return records


def _day_record(
    day: str,
    i: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    project: str | None = "/proj/a",
) -> dict[str, Any]:
    return {
        "session_id": f"s-{day}-{i}",
        "project_path": project,
        "timestamp_start": f"{day}T10:00:00Z",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
    }


def _project_record(
    project: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    i: int = 0,
) -> dict[str, Any]:
    return {
        "session_id": f"s-{project}-{i}",
        "project_path": project,
        "timestamp_start": "2026-08-01T10:00:00Z",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
    }


# Geometry constants pinned from render_trend_chart's source (lines
# 300-306): confirmed stable across every n probed (2..20000) by running
# the function and reading the emitted viewBox/style width back out.
PADDING_LEFT = 45
PADDING_RIGHT = 10
PADDING_TOP = 10
PLOT_HEIGHT = 120  # height(150) - padding_top(10) - padding_bottom(20)
MIN_BAR_WIDTH = 8


def _expected_candidate_width(n: int) -> int:
    return max(1200, 40 + n * 48)


def _expected_step(n: int) -> float:
    candidate_width = _expected_candidate_width(n)
    plot_width = candidate_width - PADDING_LEFT - PADDING_RIGHT
    return plot_width / n


# ===========================================================================
# render_trend_chart
# ===========================================================================


def test_render_trend_chart_early_return_boundary_pinned_from_both_sides():
    # n < 2 -> "" (mutation target #1: n < 1 would let a single bucket
    # through). Pinned from both sides per the brief.
    one_record = [_day_record("2026-08-01", input_tokens=10)]
    assert dashboard_html.render_trend_chart(one_record, {}, "p1") == ""

    two_records = [
        _day_record("2026-08-01", input_tokens=10),
        _day_record("2026-08-02", input_tokens=10),
    ]
    out = dashboard_html.render_trend_chart(two_records, {}, "p1")
    assert out != ""
    assert "<svg" in out


def test_render_trend_chart_no_records_also_returns_empty_string():
    # Zero buckets is the other side of "< 2", reached via a different
    # route (no timestamps to aggregate) than the one-record case above.
    assert dashboard_html.render_trend_chart([], {}, "p1") == ""


def test_render_trend_chart_one_rect_per_bucket_in_each_mode():
    # One project, only input_tokens set (output/cache left at 0) so each
    # day contributes exactly one positive segment in BOTH the "por
    # projeto" and "por tipo de token" modes - this isolates "one mark per
    # bucket" from the (separately tested) multi-segment stacking behavior.
    n = 5
    records = _year_records(n, output_tokens=0, cache_read_tokens=0, cache_creation_tokens=0)
    out = dashboard_html.render_trend_chart(records, {"/proj/a": "group-color-1"}, "p1")
    pane_svg = _svg_block(out, "chart-pane-")
    tokens_svg = _svg_block(out, "chart-tokens-")
    pane_rects = [r for r in _parse_elements(pane_svg, "rect") if "chart-bar" in r.get("class", "")]
    tokens_rects = [
        r for r in _parse_elements(tokens_svg, "rect") if "chart-bar" in r.get("class", "")
    ]
    assert len(pane_rects) == n
    assert len(tokens_rects) == n


def test_render_trend_chart_no_coordinate_escapes_canvas_for_cache_heavy_days():
    # THE most valuable test in this module (per the brief): feed a record
    # shape where cache tokens dominate input+output by ~100x - the real
    # shape that once produced negative/overflowing coordinates when one
    # rendering mode's denominator didn't include cache tokens - and assert
    # every rect/line/text coordinate stays inside the declared viewBox.
    records = [
        _day_record(
            "2026-01-01", input_tokens=50, output_tokens=50, cache_read_tokens=100 * (50 + 50)
        ),
        _day_record(
            "2026-01-02", input_tokens=5, output_tokens=5, cache_creation_tokens=100 * (5 + 5)
        ),
        _day_record(
            "2026-01-03", input_tokens=1000, output_tokens=1000, cache_read_tokens=100 * 2000
        ),
        _day_record("2026-01-04", input_tokens=1, output_tokens=1),  # no cache at all this day
    ]
    out = dashboard_html.render_trend_chart(records, {"/proj/a": "group-color-1"}, "p1")
    _assert_no_coordinate_escapes(_svg_block(out, "chart-pane-"))
    _assert_no_coordinate_escapes(_svg_block(out, "chart-tokens-"))


def test_render_trend_chart_shared_denominator_pane_and_tokens_modes_agree_on_bucket_height():
    # The two tabs decompose the SAME per-day total by two different axes
    # (by project, by token type) and must therefore agree on how "tall"
    # each day's stack is - this is the property the module's own comment
    # (lines 297-306) documents as deliberate. Day A (10000 tokens, almost
    # entirely cache) sets max_pane/max_tokens; Day B (1000 tokens, also
    # mostly cache) should read as exactly 10% of full height in BOTH
    # modes. A split denominator (mutation #3) would make the "por tipo"
    # stack's top edge diverge from "por projeto"'s for the same day.
    records = [
        _day_record("2026-01-01", input_tokens=100, output_tokens=100, cache_read_tokens=9800),
        _day_record("2026-01-02", input_tokens=50, output_tokens=50, cache_creation_tokens=900),
    ]
    out = dashboard_html.render_trend_chart(records, {"/proj/a": "group-color-1"}, "p1")
    pane_svg = _svg_block(out, "chart-pane-")
    tokens_svg = _svg_block(out, "chart-tokens-")

    def top_edges_by_x(svg_fragment: str) -> dict[float, float]:
        # Bars sharing the same x are the same day's stack (project mode:
        # one rect per day; token mode: up to 4 stacked rects per day, all
        # sharing that day's x). The topmost edge is the smallest y.
        edges: dict[float, float] = {}
        for rect in _parse_elements(svg_fragment, "rect"):
            if "chart-bar" not in rect.get("class", ""):
                continue
            x = round(rect["x"], 1)
            edges[x] = min(edges.get(x, rect["y"]), rect["y"])
        return edges

    pane_edges = top_edges_by_x(pane_svg)
    tokens_edges = top_edges_by_x(tokens_svg)
    assert len(pane_edges) == 2
    assert set(pane_edges) == set(tokens_edges)
    for x, pane_y in pane_edges.items():
        assert tokens_edges[x] == pane_y, (
            f"day at x={x}: pane top={pane_y} tokens top={tokens_edges[x]} - denominators diverged"
        )
    # Sanity: day A (full height) and day B (10% height) really do differ,
    # ruling out a vacuous pass where every day happens to look identical.
    assert len(set(pane_edges.values())) == 2


def test_render_trend_chart_bar_width_floor_flag_false_and_widths_pinned_for_reachable_n():
    # The floor mechanism (source, lines 338-346): bar_width = step * 0.6;
    # if bar_width < min_bar_width(8), the chart re-widens every bar to
    # exactly 8px and sets data-floor-hit="true". Swept computationally and
    # empirically (n from 1 to 20000) this floor's TRUE side is
    # unreachable given the current candidate_width formula - see this
    # module's report for the full derivation. The floor's minimum
    # achievable bar_width across every n is ~28.4px (at n=25), nowhere
    # near the 8px floor. That is itself the historical bug the task
    # brief names ("an 8px minimum bar width that a later change made
    # mathematically unreachable, turning its fallback into dead code") -
    # confirmed still present, not fixed, in this source. This test pins
    # the (only reachable) false side and its widths for a small and a
    # large n; the mutation check below proves this guard still matters.
    for n in (5, 300):
        records = _year_records(n)
        out = dashboard_html.render_trend_chart(records, {"/proj/a": "group-color-1"}, "p1")
        floor_hit_matches = re.findall(r'data-floor-hit="(true|false)"', out)
        assert floor_hit_matches == ["false", "false"]  # one per trend-pane (pane, tokens)
        expected_bar_width = _expected_step(n) * 0.6
        pane_svg = _svg_block(out, "chart-pane-")
        rects = [r for r in _parse_elements(pane_svg, "rect") if "chart-bar" in r.get("class", "")]
        assert rects
        for rect in rects:
            assert rect["width"] >= MIN_BAR_WIDTH
            assert rect["width"] == pytest.approx(expected_bar_width, abs=0.15)


def test_render_trend_chart_bar_width_floor_condition_direction_is_load_bearing():
    # Names the guard directly (brief's rule 2: every guard whose comment
    # cites a dated incident gets its own test). Because bar_width is
    # always well above 8 for any reachable n (previous test), inverting
    # `<` to `>` would make the floor engage for EVERY reachable n instead
    # of never - i.e. this flips data-floor-hit from constantly "false" to
    # constantly "true". A single representative n is enough to prove the
    # direction matters; the mutation-testing step (Step 3) confirms this
    # by actually applying the flip to the source.
    records = _year_records(12)
    out = dashboard_html.render_trend_chart(records, {"/proj/a": "group-color-1"}, "p1")
    assert 'data-floor-hit="false"' in out
    assert 'data-floor-hit="true"' not in out


def test_render_trend_chart_candidate_width_formula_pinned_at_small_and_large_n():
    small_out = dashboard_html.render_trend_chart(_year_records(2), {"/proj/a": "x"}, "p1")
    large_out = dashboard_html.render_trend_chart(_year_records(100), {"/proj/a": "x"}, "p1")
    small_match = re.search(r'style="width:(\d+)px"', small_out)
    large_match = re.search(r'style="width:(\d+)px"', large_out)
    assert small_match
    assert large_match
    small_width = int(small_match.group(1))
    large_width = int(large_match.group(1))
    assert small_width == _expected_candidate_width(2) == 1200
    assert large_width == _expected_candidate_width(100) == 4840


def test_render_trend_chart_tick_count_pinned_at_n_equals_3_and_n_equals_20():
    out_3 = dashboard_html.render_trend_chart(_year_records(3), {"/proj/a": "x"}, "p1")
    out_20 = dashboard_html.render_trend_chart(_year_records(20), {"/proj/a": "x"}, "p1")
    pane_svg_3 = _svg_block(out_3, "chart-pane-")
    pane_svg_20 = _svg_block(out_20, "chart-pane-")
    ticks_3 = re.findall(r'<text\b[^>]*text-anchor="middle"[^>]*>', pane_svg_3)
    ticks_20 = re.findall(r'<text\b[^>]*text-anchor="middle"[^>]*>', pane_svg_20)
    assert len(ticks_3) == 3  # min(5, 3)
    assert len(ticks_20) == 5  # min(5, 20)


def test_render_trend_chart_highlight_band_boundary_pinned_at_n_equals_7_and_8():
    out_7 = dashboard_html.render_trend_chart(_year_records(7), {"/proj/a": "x"}, "p1")
    out_8 = dashboard_html.render_trend_chart(_year_records(8), {"/proj/a": "x"}, "p1")
    assert "chart-highlight-band" not in out_7
    assert "chart-highlight-band" in out_8
    # Pin its geometry too: highlight_start_idx = n - 7 = 1, hx = padding_left
    # + 1*step, hwidth = 7*step.
    step_8 = _expected_step(8)
    expected_hx = PADDING_LEFT + 1 * step_8
    expected_hwidth = 7 * step_8
    band = _parse_elements(_svg_block(out_8, "chart-pane-"), "rect")
    highlight = next(r for r in band if r.get("class") == "chart-highlight-band")
    assert highlight["x"] == pytest.approx(expected_hx, abs=0.15)
    assert highlight["width"] == pytest.approx(expected_hwidth, abs=0.15)


def test_render_trend_chart_max_pane_or_1_guard_survives_all_zero_token_days():
    # Mutation target #4: max_pane = max(day_totals) or 1. Two buckets (so
    # the n<2 early return doesn't intervene) with every record at zero
    # tokens: day_totals = [0, 0], max([0, 0]) is falsy (0), so without
    # "or 1" the y-axis math (value / max_value inside build_y_axis, called
    # with frac=0 giving value=0 too) divides 0/0 and raises
    # ZeroDivisionError. Confirmed by running: dropping the guard raises
    # immediately on the first y-axis gridline.
    records = [
        _day_record("2026-08-01", input_tokens=0, output_tokens=0),
        _day_record("2026-08-02", input_tokens=0, output_tokens=0),
    ]
    out = dashboard_html.render_trend_chart(records, {"/proj/a": "group-color-1"}, "p1")
    assert out != ""
    assert "<svg" in out
    # No bars are drawn (every segment value is 0, filtered by `if value <=
    # 0: continue`) but the axis/grid still renders without raising.
    assert "chart-bar" not in out


def test_render_trend_chart_legend_dedupes_worktrees_into_parent_and_escapes_label():
    # Exercises the pane_legend construction (color_group_key dedup,
    # worktree->parent basename, html.escape) that a single-project test
    # never touches.
    records = [
        _day_record(
            "2026-08-01",
            i=0,
            input_tokens=100,
            project="/home/dev/my<proj>/.claude/worktrees/feature-x",
        ),
        _day_record("2026-08-02", i=1, input_tokens=50, project="/home/dev/my<proj>"),
    ]
    group_colors: dict[str | None, str] = {
        "/home/dev/my<proj>/.claude/worktrees/feature-x": "group-color-2",
        "/home/dev/my<proj>": "group-color-2",
    }
    out = dashboard_html.render_trend_chart(records, group_colors, "p1")
    assert "&lt;proj&gt;" in out
    assert "<proj>" not in out
    # Both records share color_group_key "/home/dev/my<proj>", so they
    # dedupe into exactly one legend chip, not two.
    assert out.count("legend-chip") == 1


# --- five additional mutations, chosen by scanning this function's own
# guards/boundaries/orderings beyond the six named in the brief (Step 4) ---
# See the task report for the full sweep methodology and every survivor.


def test_render_trend_chart_legend_orders_projects_by_spend_descending():
    # Sweep candidate: `sorted(pane_totals, key=..., reverse=True)` for the
    # legend chip order. Two projects on two different days (so n=2, past
    # the early-return) with clearly different totals; the higher-spend
    # one's chip must render first.
    records = [
        _day_record("2026-08-01", i=0, input_tokens=10, project="/proj/small"),
        _day_record("2026-08-02", i=1, input_tokens=1000, project="/proj/big"),
    ]
    group_colors: dict[str | None, str] = {
        "/proj/small": "group-color-1",
        "/proj/big": "group-color-2",
    }
    out = dashboard_html.render_trend_chart(records, group_colors, "p1")
    legend_html = out[out.index("project-chart-legend") :]
    assert legend_html.index("group-color-2") < legend_html.index("group-color-1")


def test_render_trend_chart_multiple_records_for_same_project_same_day_merge_into_one_segment():
    # Exercises the `if project not in by_project` branch's FALSE side
    # (project already seen this day -> accumulate onto the existing
    # total instead of starting a new project_order entry) - every other
    # test in this module uses at most one record per project per day.
    records = [
        _day_record("2026-08-01", i=0, input_tokens=10, project="/proj/a"),
        _day_record("2026-08-01", i=1, input_tokens=20, project="/proj/a"),
        _day_record("2026-08-02", i=0, input_tokens=5, project="/proj/a"),
    ]
    out = dashboard_html.render_trend_chart(records, {"/proj/a": "x"}, "p1")
    pane_svg = _svg_block(out, "chart-pane-")
    rects = [r for r in _parse_elements(pane_svg, "rect") if "chart-bar" in r.get("class", "")]
    # Still exactly one bar per day (the two same-day records merged into
    # a single project segment), and day 1's bar is full height (30
    # tokens == max_pane) while day 2's is 1/6 of that (5 tokens).
    assert len(rects) == 2
    day1 = max(rects, key=lambda r: r["height"])
    day2 = min(rects, key=lambda r: r["height"])
    assert day1["height"] == pytest.approx(PLOT_HEIGHT, abs=0.1)
    assert day2["height"] == pytest.approx(PLOT_HEIGHT * (5 / 30), abs=0.1)


def test_render_trend_chart_zero_value_segments_are_never_drawn():
    # Sweep candidate: `if value <= 0: continue` boundary in bars_for_tokens
    # (line 437). A record with one token type exactly zero must not
    # produce a zero-height rect for that series - only the three positive
    # series render.
    records = _year_records(
        3, input_tokens=100, output_tokens=0, cache_read_tokens=50, cache_creation_tokens=20
    )
    out = dashboard_html.render_trend_chart(records, {"/proj/a": "x"}, "p1")
    tokens_svg = _svg_block(out, "chart-tokens-")
    rects = [r for r in _parse_elements(tokens_svg, "rect") if "chart-bar" in r.get("class", "")]
    classes = {r["class"] for r in rects}
    assert "chart-bar series-2" not in classes  # output_tokens=0 everywhere
    assert classes == {"chart-bar series-1", "chart-bar series-3", "chart-bar series-4"}


def test_render_trend_chart_pane_mode_zero_value_project_segment_is_never_drawn():
    # The same `if value <= 0: continue` boundary, but bars_for_pane's own
    # copy of it (line 409, a separate guard from the one above - found by
    # the unnamed-mutation sweep: mutating THIS copy to `< 0` survived the
    # zero-value test above, which only exercises bars_for_tokens, and was
    # only caught incidentally by the unrelated all-zero-days test). A
    # project with zero tokens on one day (but positive on another, so it
    # isn't dropped from project_order entirely) must not get a zero-height
    # segment on the zero day.
    records = [
        _day_record("2026-08-01", i=0, input_tokens=0, project="/proj/a"),
        _day_record("2026-08-01", i=1, input_tokens=100, project="/proj/b"),
        _day_record("2026-08-02", i=0, input_tokens=50, project="/proj/a"),
    ]
    out = dashboard_html.render_trend_chart(
        records, {"/proj/a": "group-color-1", "/proj/b": "group-color-2"}, "p1"
    )
    pane_svg = _svg_block(out, "chart-pane-")
    rects = [r for r in _parse_elements(pane_svg, "rect") if "chart-bar" in r.get("class", "")]
    # Day 1 has two projects but /proj/a contributed 0 tokens that day, so
    # only /proj/b's segment should render for day 1 (1 rect); day 2 has
    # only /proj/a's positive segment (1 rect) - 2 total, not 3.
    assert len(rects) == 2
    assert {r["class"] for r in rects} == {"chart-bar group-color-2", "chart-bar group-color-1"}


# ===========================================================================
# render_project_totals_chart
# ===========================================================================


def test_render_project_totals_chart_orders_projects_by_spend_descending():
    # Names chosen so alphabetical order (aaa, mmm, zzz) is the exact
    # REVERSE of spend order - an alphabetical-by-accident pass is ruled
    # out, and so is input order (records are listed low-to-high spend).
    records = [
        _project_record("/proj/aaa_small", input_tokens=10),
        _project_record("/proj/zzz_big", input_tokens=1000),
        _project_record("/proj/mmm_mid", input_tokens=100),
    ]
    out = dashboard_html.render_project_totals_chart(records, {}, {}, set(), "p1")
    labels = re.findall(r'class="project-chart-label"[^>]*>([^<]*)</text>', out)
    assert labels == ["zzz_big", "mmm_mid", "aaa_small"]


def test_render_project_totals_chart_empty_and_all_zero_records_return_empty_string():
    # `max_total = max(totals.values()) or 1` (source line ~613): verified
    # by running that this fallback is UNREACHABLE in the current source -
    # the preceding `if not ordered_projects: return ""` already excludes
    # every all-zero/empty scenario before that line is ever reached (an
    # all-zero project's total fails `totals.get(p, 0) > 0` and gets
    # filtered out of ordered_projects first). This test pins the actually
    # observable behavior the brief's property describes (no
    # ZeroDivisionError for an all-zero record set) via the early return
    # that really provides it. A mutation deleting `or 1` would NOT be
    # caught by any legitimate-input test - see the task report.
    assert dashboard_html.render_project_totals_chart([], {}, {}, set(), "p1") == ""
    zero_records = [_project_record("/proj/a", input_tokens=0, output_tokens=0)]
    assert dashboard_html.render_project_totals_chart(zero_records, {}, {}, set(), "p1") == ""


def test_render_project_totals_chart_visible_slice_bound_pinned_at_ten_and_eleven():
    # The [:10]/[10:] slice bound (lines 681-682) - invisible to branch
    # coverage since a slice has no branch (task brief lesson A).
    ten_projects = [_project_record(f"/proj/p{i}", input_tokens=1000 - i) for i in range(10)]
    eleven_projects = [_project_record(f"/proj/p{i}", input_tokens=1000 - i) for i in range(11)]

    out_10 = dashboard_html.render_project_totals_chart(ten_projects, {}, {}, set(), "p1")
    out_11 = dashboard_html.render_project_totals_chart(eleven_projects, {}, {}, set(), "p1")

    labels_10 = re.findall(r'class="project-chart-label"[^>]*>([^<]*)</text>', out_10)
    assert len(labels_10) == 10
    assert "project-chart-toggle" not in out_10

    assert "project-chart-toggle" in out_11
    assert "+ 1 projetos históricos" in out_11
    # The 11th (lowest-spend) project appears only inside the hidden svg,
    # not among the visible rows.
    visible_11 = out_11.split('id="project-chart-more-p1"')[0]
    assert "p10" not in visible_11
    assert "p10" in out_11


def test_render_project_totals_chart_row_height_pins_svg_height_and_row_positions():
    # row_height = 34 (source comment: must clear label baseline + bar +
    # next row's label ascent - a 24px value once let labels overlap the
    # bar above them). Pinned via the whole-svg height formula
    # (n*row_height + 20) and each row's track y position.
    two_projects = [
        _project_record("/proj/a", input_tokens=100),
        _project_record("/proj/b", input_tokens=50),
    ]
    out = dashboard_html.render_project_totals_chart(two_projects, {}, {}, set(), "p1")
    viewbox_match = re.search(r'<svg viewBox="0 0 \d+ (\d+)"', out)
    assert viewbox_match
    viewbox_height = int(viewbox_match.group(1))
    assert viewbox_height == 2 * 34 + 20 == 88
    tracks = _parse_elements(out, "rect")
    track_ys = sorted(r["y"] for r in tracks if r.get("class") == "project-chart-track")
    assert track_ys == [14 + 9, 14 + 34 + 9] == [23, 57]


def test_render_project_totals_chart_segments_carry_token_type_class_and_stack_left_to_right():
    # Verified by running (correcting an inaccurate brief assumption):
    # render_project_totals_chart has no group_colors parameter at all -
    # its bars are segmented by TOKEN TYPE (series-1..4, matching the
    # trend chart's "por tipo de token" legend/handler), not by project
    # colour. This pins the real behaviour: one segment per positive
    # series, each carrying its series-N class, positioned left-to-right
    # in series order with contiguous x_cursor advancement.
    records = [
        _project_record(
            "/proj/a",
            input_tokens=10,
            output_tokens=20,
            cache_read_tokens=30,
            cache_creation_tokens=40,
        )
    ]
    out = dashboard_html.render_project_totals_chart(records, {}, {}, set(), "p1")
    segments = [
        r
        for r in _parse_elements(out, "rect")
        if re.match(r"chart-bar series-\d$", r.get("class", ""))
    ]
    assert [s["class"] for s in segments] == [
        "chart-bar series-1",
        "chart-bar series-2",
        "chart-bar series-3",
        "chart-bar series-4",
    ]
    # Contiguous: each segment starts exactly where the previous ended.
    for prev, cur in itertools.pairwise(segments):
        assert cur["x"] == pytest.approx(prev["x"] + prev["width"], abs=0.05)
    assert segments[0]["x"] == 4  # label_x
    total = 10 + 20 + 30 + 40
    for seg, value in zip(segments, (10, 20, 30, 40), strict=True):
        bar_max_width = 300
        assert seg["width"] == pytest.approx((value / total) * bar_max_width, abs=0.05)


def test_render_project_totals_chart_legend_totals_include_hidden_projects():
    # overall_totals sums series_by_project.values() - ALL projects, not
    # just the visible top 10. An 11th (hidden) project's tokens must
    # still be counted in the legend chip totals.
    projects = [_project_record(f"/proj/p{i}", input_tokens=100) for i in range(11)]
    out = dashboard_html.render_project_totals_chart(projects, {}, {}, set(), "p1")
    legend_total = re.search(r'data-series="series-1">([\d.,a-zA-Z]+)</span>', out)
    assert legend_total is not None
    # 11 projects * 100 input tokens = 1100, formatted via format_tokens ->
    # "1.1k" (verified: mr.format_tokens(1100) == "1.1k").
    assert legend_total.group(1) == "1.1k"


def test_render_project_totals_chart_open_marker_present_only_for_open_projects():
    records = [
        _project_record("/proj/open", input_tokens=10),
        _project_record("/proj/closed", input_tokens=10),
    ]
    out = dashboard_html.render_project_totals_chart(records, {}, {}, {"/proj/open"}, "p1")
    assert "aberto agora" in out
    assert out.count("aberto agora") == 1


def test_render_project_totals_chart_html_escapes_project_title_attribute():
    records = [_project_record("/proj/<script>", input_tokens=10)]
    out = dashboard_html.render_project_totals_chart(records, {}, {}, set(), "p1")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_render_project_totals_chart_days_running_note_appended_when_first_seen_known():
    records = [_project_record("/proj/a", input_tokens=10)]
    out_without = dashboard_html.render_project_totals_chart(records, {}, {}, set(), "p1")
    out_with = dashboard_html.render_project_totals_chart(
        records, {"/proj/a": "2020-01-01T00:00:00Z"}, {}, set(), "p1"
    )
    assert "1º uso há" not in out_without
    assert "1º uso há" in out_with


# --- three of the five sweep mutations land on render_project_totals_chart;
# these two properties are exercised by the tests already above (visible
# slice bound test, row height test) plus the highlight-band/legend-order/
# zero-value tests above for render_trend_chart. See the report for the
# full sweep list and results.
