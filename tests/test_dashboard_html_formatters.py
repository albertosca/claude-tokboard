"""Covers dashboard_html.py's small formatter functions: format_table,
format_summary, render_composition_legend, render_composition_bar,
render_session_header, compute_project_series_totals, build_group_colors.

Every expected value below was derived by running the function under test
(not inferred from reading it) - see the task brief's rule 1. Assertions
pin structure and invariants (row order, column set, order stability,
proportional widths, return shapes) rather than byte-exact markup, so an
innocent refactor of e.g. whitespace doesn't fail the suite.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import metrics_core as mr

import dashboard_html

# --------------------------------------------------------------------------
# format_table
# --------------------------------------------------------------------------


def test_format_table_header_plus_one_row_per_record_in_input_order():
    records = [
        {"session_id": "first-session", "input_tokens": 1},
        {"session_id": "second-session", "input_tokens": 2},
        {"session_id": "third-session", "input_tokens": 3},
    ]
    lines = dashboard_html.format_table(records).split("\n")
    # header + separator + one line per record, in the same order
    assert len(lines) == 2 + len(records)
    # Each session_id is 13-14 chars; [:12] is an intentional pin (not
    # incidental - see test_format_table_truncates_long_fields_to_column_widths
    # below for the truncation bound itself), so the full 12-char prefix is
    # asserted here rather than a shorter startswith().
    assert lines[2].startswith("first-sessio")
    assert lines[3].startswith("second-sessi")
    assert lines[4].startswith("third-sessio")


def test_format_table_truncates_long_fields_to_column_widths():
    # A slice bound (session_id[:12], tag[:10], branch[:12]) has no branch
    # to cover, so 100% branch coverage cannot protect it - this test pins
    # the exact truncated length for all three fields directly.
    record = {
        "session_id": "abcdefghijklmnopqrstuvwxyz",  # 26 chars
        "tag": "verylongtagname",  # 15 chars
        "git_branch": "feature/very-long-branch-name",  # 30 chars
    }
    fields = dashboard_html.format_table([record]).split("\n")[2].split()
    assert fields == ["abcdefghijkl", "verylongta", "feature/very", "-", "0", "0", "0", "0"]


def test_format_table_column_set_is_pinned():
    header = dashboard_html.format_table([]).split("\n")[0]
    for column in (
        "session_id",
        "tag",
        "branch",
        "dur(s)",
        "in_tok",
        "out_tok",
        "turns",
        "tools",
    ):
        assert column in header, f"column {column!r} silently dropped from header"


def test_format_table_missing_optional_fields_renders_without_raising():
    # A record with none of the optional keys present at all.
    out = dashboard_html.format_table([{}])
    lines = out.split("\n")
    assert len(lines) == 3
    row = lines[2]
    # session_id defaults to "" (no token); tag/branch/duration default to
    # "-"; the four numeric columns default to 0.
    assert row.split() == ["-", "-", "-", "0", "0", "0", "0"]


def test_format_table_duration_zero_renders_as_zero_not_dash():
    # duration_seconds=0 is falsy but not None - the guard is `is not None`,
    # not truthiness. Pins that distinction (sweep mutation target).
    row = dashboard_html.format_table([{"duration_seconds": 0}]).split("\n")[2]
    fields = row.split()
    # fields (no session_id token: empty string collapses under split()):
    # tag(-) branch(-) dur(s) in out turns tools
    assert fields[2] == "0"


# --------------------------------------------------------------------------
# format_summary
# --------------------------------------------------------------------------


def test_format_summary_zero_records():
    assert dashboard_html.format_summary([]) == "No sessions matched."


def test_format_summary_aggregates_are_correct_for_a_known_record_set():
    # Hand-computed, not re-derived from the function's own arithmetic:
    # n=3, total_input=100+200+0=300, total_output=50+150+0=200,
    # durations=[60,120] -> avg=90, total_turns=2+4+0=6, avg_turns=6/3=2.0
    records = [
        {"duration_seconds": 60, "input_tokens": 100, "output_tokens": 50, "turn_count": 2},
        {"duration_seconds": 120, "input_tokens": 200, "output_tokens": 150, "turn_count": 4},
        {"input_tokens": 0, "output_tokens": 0},
    ]
    assert dashboard_html.format_summary(records) == (
        "\n3 session(s) - avg duration 90s, total input tokens 300, "
        "total output tokens 200, avg turns 2.0"
    )


def test_format_summary_single_record_averages_equal_that_records_own_values():
    # n == 1 was the one missing input class (review M3), and it is an
    # ordinary real one - "one session matched" is what every narrow filter
    # produces. Both averages divide by a count, so they are the arithmetic
    # that only n == 1 can discriminate: replacing `total_turns / n` with
    # `/ max(n, 2)` (and the same for the duration mean) changes nothing at
    # n = 0, 2 or 3 and everything here. With a single record both averages
    # must reproduce that record's own numbers exactly - 45s and 5.0, not
    # halves of them.
    records = [
        {"duration_seconds": 45, "input_tokens": 7, "output_tokens": 3, "turn_count": 5},
    ]
    assert dashboard_html.format_summary(records) == (
        "\n1 session(s) - avg duration 45s, total input tokens 7, "
        "total output tokens 3, avg turns 5.0"
    )


def test_format_summary_avg_duration_is_zero_when_no_record_has_a_duration():
    # Targets the `if durations else 0` guard directly (named mutation 3):
    # records is non-empty, but none carry duration_seconds.
    records = [{"input_tokens": 1}, {"input_tokens": 2}]
    assert dashboard_html.format_summary(records) == (
        "\n2 session(s) - avg duration 0s, total input tokens 3, "
        "total output tokens 0, avg turns 0.0"
    )


# --------------------------------------------------------------------------
# render_composition_legend
# --------------------------------------------------------------------------


def test_render_composition_legend_one_chip_per_series_with_real_totals_in_stable_order():
    totals = {"series-1": 100, "series-2": 2500, "series-3": 0, "series-4": 1_000_000_000}
    out = dashboard_html.render_composition_legend(totals, lang="pt")
    found = re.findall(r'data-series="(series-\d)"', out)
    # each series appears exactly twice: once on the chip span, once on the total span
    assert found == [
        "series-1",
        "series-1",
        "series-2",
        "series-2",
        "series-3",
        "series-3",
        "series-4",
        "series-4",
    ]
    for expected_total in totals.values():
        assert mr.format_tokens(expected_total) in out


def test_render_composition_legend_missing_series_defaults_to_zero():
    out = dashboard_html.render_composition_legend({}, lang="pt")
    assert out.count(">0<") == 4  # all four chips report a total of 0
    # The quota line's own two zeros are plain text, not tag-wrapped like
    # the chips' totals - if they were, this count would silently become 6
    # (see the module's own comment at render_composition_legend).
    assert "Quota (aprox.): 0 entrada · 0 saída" in out


def test_render_composition_legend_quota_line_sums_input_and_cache_creation_only():
    # Backlog, 2026-09-02: input (series-1) and cache_creation (series-4)
    # count toward the "entrada" figure; cache_read (series-3) must be
    # invisible to it entirely; output (series-2) is its own number.
    totals = {"series-1": 100, "series-2": 20, "series-3": 999_999_999, "series-4": 5}
    out = dashboard_html.render_composition_legend(totals, lang="pt")
    assert "Quota (aprox.): 105 entrada · 20 saída" in out


# --------------------------------------------------------------------------
# render_composition_bar
# --------------------------------------------------------------------------


def test_render_composition_bar_widths_are_proportional_and_sum_to_100():
    record = {
        "input_tokens": 30,
        "output_tokens": 30,
        "cache_read_tokens": 30,
        "cache_creation_tokens": 10,
    }
    out = dashboard_html.render_composition_bar(record)
    widths = [float(width_text) for width_text in re.findall(r"width:([\d.]+)%", out)]
    assert len(widths) == 4
    assert sum(widths) == 100.0
    assert widths == [30.0, 30.0, 30.0, 10.0]


def test_render_composition_bar_all_zero_tokens_does_not_divide_by_zero():
    # Explicit-zero and missing-key records both reach `total <= 0` through
    # the same "or 0" default and behave identically - kept as one test
    # (not two near-duplicates) since neither adds a code path the other
    # doesn't already exercise.
    for record in ({"input_tokens": 0, "output_tokens": 0}, {}):
        assert (
            dashboard_html.render_composition_bar(record)
            == '<div class="comp-bar comp-bar-empty"></div>'
        )


def test_render_composition_bar_zero_value_segment_is_skipped():
    # Only input and cache_creation are non-zero; output/cache_read are 0
    # and must not produce a segment (the `if value <= 0: continue` guard).
    record = {
        "input_tokens": 5,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 5,
    }
    out = dashboard_html.render_composition_bar(record)
    assert "series-1" in out
    assert "series-4" in out
    assert "series-2" not in out
    assert "series-3" not in out


def test_render_composition_bar_each_segment_carries_its_own_css_class():
    record = {"input_tokens": 1, "output_tokens": 2}
    out = dashboard_html.render_composition_bar(record)
    assert '<span class="comp-seg series-1"' in out
    assert '<span class="comp-seg series-2"' in out


# --------------------------------------------------------------------------
# render_session_header
# --------------------------------------------------------------------------


def test_render_session_header_show_tmux_true_includes_tmux_column():
    out = dashboard_html.render_session_header(show_tmux=True, lang="pt")
    assert '<span class="tmux-window">aba</span>' in out


def test_render_session_header_show_tmux_false_omits_tmux_column():
    out = dashboard_html.render_session_header(show_tmux=False, lang="pt")
    assert '<span class="tmux-window">aba</span>' not in out


def test_render_session_header_default_matches_show_tmux_true():
    # Pins the default argument itself - a flipped default (named
    # mutation 1) makes the no-arg call diverge from the explicit-True call.
    assert dashboard_html.render_session_header(lang="pt") == dashboard_html.render_session_header(
        show_tmux=True, lang="pt"
    )


def test_render_session_header_columns_appear_in_a_pinned_order():
    # Sweep survivor (2026-08-31): swapping the tag-col/branch-col spans'
    # order passed the full suite - presence/absence checks don't pin
    # relative order. re.findall walks the string left to right, so this
    # pins the column sequence directly.
    out = dashboard_html.render_session_header(show_tmux=True, lang="pt")
    columns = re.findall(r'class="([a-z-]+col|sid)"', out)
    assert columns == [
        "sid",
        "subagent-col",
        "date-col",
        "label-col",
        "tag-col",
        "branch-col",
        "duration-col",
    ]


# --------------------------------------------------------------------------
# compute_project_series_totals
# --------------------------------------------------------------------------


def test_compute_project_series_totals_return_shape_is_pinned():
    records: list[dict[str, Any]] = [
        {
            "project_path": "/a",
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 1,
            "cache_creation_tokens": 2,
        },
        {"project_path": "/a", "input_tokens": 3, "output_tokens": 0},
        {"project_path": None, "output_tokens": 7},
    ]
    per_project, grand_totals = mr.compute_project_series_totals(records)
    assert per_project == {
        "/a": {"series-1": 13, "series-2": 5, "series-3": 1, "series-4": 2},
        None: {"series-1": 0, "series-2": 7, "series-3": 0, "series-4": 0},
    }
    assert grand_totals == {"/a": 21, None: 7}


def test_compute_project_series_totals_explicit_none_value_does_not_raise():
    # `r.get("input_tokens", 0) or 0` guards against a key that is PRESENT
    # but explicitly None, not just a missing key.
    records = [{"project_path": "/x", "input_tokens": None, "output_tokens": 5}]
    per_project, grand_totals = mr.compute_project_series_totals(records)
    assert per_project == {"/x": {"series-1": 0, "series-2": 5, "series-3": 0, "series-4": 0}}
    assert grand_totals == {"/x": 5}


def test_compute_project_series_totals_all_four_fields_guard_explicit_none():
    # Sweep survivor (2026-08-31): a test covering ONLY input_tokens=None
    # left the identical "or 0" guard on cache_read_tokens/
    # cache_creation_tokens/output_tokens unexercised - removing any of
    # those three guards passed the full suite. Each of the four fields
    # gets its own explicit-None record so every guard is independently
    # provable.
    records = [
        {"project_path": "/x", "input_tokens": None},
        {"project_path": "/x", "output_tokens": None},
        {"project_path": "/x", "cache_read_tokens": None},
        {"project_path": "/x", "cache_creation_tokens": None},
    ]
    per_project, grand_totals = mr.compute_project_series_totals(records)
    assert per_project == {"/x": {"series-1": 0, "series-2": 0, "series-3": 0, "series-4": 0}}
    assert grand_totals == {"/x": 0}


# --------------------------------------------------------------------------
# build_group_colors
# --------------------------------------------------------------------------


def test_build_group_colors_ranks_by_the_daily_feed_the_chart_draws():
    # 2026-10-05: the eight colour slots go to the biggest spenders, measured
    # on the feed the trend chart actually draws. Snapshots re-count the
    # same tokens, so they colour paths but do not rank them.
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    recent = "2026-10-01T10:00:00+00:00"
    chart_records = [
        {"project_path": "/a", "timestamp_start": recent, "input_tokens": 1_000_000},
        {"project_path": "/b", "timestamp_start": recent, "input_tokens": 1},
    ]
    daily_chart_records = [
        {"project_path": "/a", "timestamp_start": recent, "input_tokens": 10},
        {"project_path": "/b", "timestamp_start": recent, "input_tokens": 20},
    ]
    colors = dashboard_html.build_group_colors(chart_records, daily_chart_records, now=now)
    assert colors == {"/a": "group-color-2", "/b": "group-color-1"}


def test_build_group_colors_project_reachable_only_via_daily_feed_still_gets_a_color():
    # The 2026-08-10 regression scenario: a project_path that never appears
    # in chart_records (a worktree/subagent path visible only in the raw
    # per-day feed) must still receive a real colour, not fall back to
    # "group-color-none".
    chart_records = [{"project_path": "/a"}]
    daily_chart_records = [{"project_path": "/a"}, {"project_path": "/only-in-daily"}]
    colors = dashboard_html.build_group_colors(chart_records, daily_chart_records)
    assert colors["/only-in-daily"] != "group-color-none"
    assert colors == {"/a": "group-color-1", "/only-in-daily": "group-color-2"}
