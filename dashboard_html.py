#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import metrics_core as metrics_core_module

from dashboard_html_assets import SCRIPT, STYLE


def format_table(records: list[dict[str, Any]]) -> str:
    header = (
        f"{'session_id':<12} {'tag':<10} {'branch':<12} {'dur(s)':>8} "
        f"{'in_tok':>8} {'out_tok':>8} {'turns':>6} {'tools':>6}"
    )
    lines = [header, "-" * len(header)]
    for record in records:
        session_id = (record.get("session_id") or "")[:12]
        tag = (record.get("tag") or "-")[:10]
        branch = (record.get("git_branch") or "-")[:12]
        duration = record.get("duration_seconds")
        duration_text = f"{duration:.0f}" if duration is not None else "-"
        lines.append(
            f"{session_id:<12} {tag:<10} {branch:<12} {duration_text:>8} "
            f"{record.get('input_tokens', 0):>8} {record.get('output_tokens', 0):>8} "
            f"{record.get('turn_count', 0):>6} {record.get('tool_call_count', 0):>6}"
        )
    return "\n".join(lines)


def format_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return "No sessions matched."
    session_count = len(records)
    total_input = sum(record.get("input_tokens", 0) for record in records)
    total_output = sum(record.get("output_tokens", 0) for record in records)
    total_turns = sum(record.get("turn_count", 0) for record in records)
    durations = [
        record["duration_seconds"]
        for record in records
        if record.get("duration_seconds") is not None
    ]
    avg_duration = sum(durations) / len(durations) if durations else 0
    return (
        f"\n{session_count} session(s) - avg duration {avg_duration:.0f}s, "
        f"total input tokens {total_input}, total output tokens {total_output}, "
        f"avg turns {total_turns / session_count:.1f}"
    )


def chip_attributes(highlight_key: str) -> str:
    """What makes a clickable legend chip a keyboard control (2026-10-05
    review): a button role, a place in the tab order, and the highlight key
    the page's script reads to keep aria-pressed in step with the
    selection (syncPressed in dashboard_html_assets.SCRIPT)."""
    return f'role="button" tabindex="0" data-highlight-key="{highlight_key}" '


SERIES_NAMES = {
    "series-1": "input",
    "series-2": "output",
    "series-3": "cache read",
    "series-4": "cache creation",
}


def render_composition_legend(totals: dict[str, int], *, lang: str) -> str:
    chips = []
    for css_class, name in SERIES_NAMES.items():
        chips.append(
            f'<span class="legend-chip" data-series="{css_class}" {chip_attributes(css_class)}'
            f"onclick=\"highlightTokenType('{css_class}', event)\">"
            f'<span class="legend-swatch {css_class}"></span>{name} · '
            f'<span class="legend-total" data-series="{css_class}">'
            f"{metrics_core_module.format_tokens(totals.get(css_class, 0))}</span></span>"
        )
    # Backlog, 2026-09-02: the flat 4-field total can be almost entirely
    # cache_read on a real heavy day, so it answers "how much text moved," not "how much
    # of my quota did this eat." Plain text, deliberately not wrapped in
    # its own tag around each number (unlike the chips above) - a `<b>`
    # wrapper here would add two more literal ">0<" substrings whenever
    # both quota numbers are 0, silently changing what
    # test_render_composition_legend_missing_series_defaults_to_zero's
    # `out.count(">0<") == 4` is actually counting.
    quota_input, quota_output = metrics_core_module.compute_quota_totals(totals)
    quota_text = metrics_core_module.t(
        "quota_line",
        lang,
        input=metrics_core_module.format_tokens(quota_input),
        output=metrics_core_module.format_tokens(quota_output),
    )
    quota_tooltip = html.escape(metrics_core_module.t("quota_tooltip", lang))
    quota_line = f'<div class="quota-line" title="{quota_tooltip}">{quota_text}</div>'
    return quota_line + '<div class="comp-legend">' + "".join(chips) + "</div>"


def render_floor_total(record: dict[str, Any], css_class: str, *, lang: str) -> str:
    """A record's token total as a span - prefixed with "≥" and carrying the
    catalog note as its tooltip when the record's output is only a floor
    (incomplete_usage_messages), plain otherwise so unflagged rows are
    unchanged."""
    total = metrics_core_module.format_tokens(metrics_core_module.record_token_total(record))
    incomplete = metrics_core_module.incomplete_usage_messages(record)
    if not incomplete:
        return f'<span class="{css_class}">{total}</span>'
    note = html.escape(
        metrics_core_module.tn(
            "floor_note", incomplete, lang, n=metrics_core_module.format_tokens(incomplete)
        ),
        quote=True,
    )
    return f'<span class="{css_class}" title="{note}">≥{total}</span>'


def render_composition_bar(record: dict[str, Any]) -> str:
    # The output segment's figure gets the same "≥" as the row's total when
    # it is a floor; the explanation lives on the total, once per row.
    floor = "≥" if metrics_core_module.incomplete_usage_messages(record) else ""
    segments = [
        ("series-1", record.get("input_tokens", 0) or 0),
        ("series-2", record.get("output_tokens", 0) or 0),
        ("series-3", record.get("cache_read_tokens", 0) or 0),
        ("series-4", record.get("cache_creation_tokens", 0) or 0),
    ]
    total = sum(value for _, value in segments)
    if total <= 0:
        return '<div class="comp-bar comp-bar-empty"></div>'
    parts = []
    for css_class, value in segments:
        if value <= 0:
            continue
        pct = value / total * 100
        name = SERIES_NAMES[css_class]
        prefix = floor if css_class == "series-2" else ""
        parts.append(
            f'<span class="comp-seg {css_class}" style="width:{pct:.2f}%" '
            f'title="{name} · {prefix}{metrics_core_module.format_tokens(value)} ({pct:.0f}%)" '
            f"onclick=\"highlightTokenType('{css_class}', event)\"></span>"
        )
    return f'<div class="comp-bar">{"".join(parts)}</div>'


def render_session_row(
    record: dict[str, Any],
    is_active: bool,
    group_colors: dict[str | None, str],
    group_index: int,
    subagent_count: int = 0,
    show_tmux: bool = True,
    period_ids: tuple[str, ...] = ("tudo",),
    *,
    controls_panel: bool = True,
    lang: str,
) -> str:
    session_id = record.get("session_id") or ""
    tag = html.escape(record.get("tag") or "-")
    branch = html.escape(record.get("git_branch") or "-")
    tmux_window = html.escape(record.get("tmux_window") or "-") if show_tmux else None
    # Display-layer safeguard on top of compress_to_label's own truncation
    # (2026-07-21) - covers any already-cached label from before that fix
    # existed, so a stale huge cached label can't break this row's layout
    # either, without needing to hunt down and purge every bad cache entry.
    raw_task_label = record.get("task_label") or "-"
    if len(raw_task_label) > 80:
        raw_task_label = raw_task_label[:80].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    task_label = html.escape(raw_task_label)
    date_text = metrics_core_module.format_date(record.get("timestamp_start"), lang=lang)
    duration_text = metrics_core_module.format_duration(record.get("duration_seconds"))
    active_class = " active" if is_active else ""
    group_class = group_colors.get(record.get("project_path"), "group-color-none")
    # Compact "has subagents" indicator (2026-07-21) - a count badge here
    # answers "does this session have subagents worth checking" without
    # opening every single row's detail panel to find out.
    subagent_badge = f"🤖{subagent_count}" if subagent_count else ""
    # Prefixed with a terminal icon and always carrying the full value in
    # `title` (2026-09-04) - this is the tmux WINDOW name, not a second
    # project name, and read next to the project group on its left it was
    # being mistaken for a contradictory one (e.g. a "my-api" session
    # showing "scratch" here). The icon plus the muted/italic styling from
    # the CSS both point at "this is terminal context", not identity.
    # The icon only points at something when there is a window name; a row
    # without one keeps the cell (the columns still line up) with a bare dash.
    tmux_glyph = "⧉ " if record.get("tmux_window") else ""
    tmux_span = (
        f'<span class="tmux-window" title="{tmux_window}">{tmux_glyph}{tmux_window}</span>'
        if show_tmux
        else ""
    )
    # Row visibility is class-driven ONLY (2026-08-13). This row used to be
    # born with an inline style="display:none" that toggleGroup then
    # overwrote with an inline display:flex. An inline declaration beats any
    # non-!important author rule, so the period filter's .period-hidden rule
    # could never win: an expanded row stayed on screen under every period.
    # Collapse state and period membership are both plain classes now, so the
    # two mechanisms cannot contradict each other - the row shows only when
    # it is neither collapsed nor outside the active period.
    # The panel a row controls is built on first open (2026-10-24), so
    # aria-controls is emitted only while its target is in the page: a
    # reference to a missing id is an axe failure on every row.
    controls = f'aria-controls="detail-{session_id}" ' if controls_panel else ""
    return (
        f'<div class="session-row {group_class}{active_class} collapsed" id="row-{session_id}" '
        f'data-group="{group_index}" '
        f'data-periods="{" ".join(period_ids)}" '
        f'role="button" tabindex="0" {controls}'
        f"onclick=\"showSession('{session_id}')\">"
        f'<span class="sid">{session_id[:12]}</span>'
        f'<span class="date-col">{date_text}</span>'
        # The numbers come before the free-text columns (2026-10-05 review):
        # at 1440px the list is ~890px wide and a full row ~1130px, so the
        # columns placed last started past the visible edge.
        f"{render_floor_total(record, 'tokens-total', lang=lang)}"
        f"{render_composition_bar(record)}"
        f'<span class="duration-col">{duration_text}</span>'
        f'<span class="subagent-col">{subagent_badge}</span>'
        f"{tmux_span}"
        f'<span class="label-col">{task_label}</span>'
        f'<span class="tag-col">{tag}</span>'
        f'<span class="branch-col" title="{branch}">{branch}</span>'
        f"</div>"
    )


def render_group_header(
    group_index: int,
    group: dict[str, Any],
    period_windows: dict[str, tuple[datetime, datetime] | None],
    *,
    lang: str,
) -> str:
    # Period-aware since 2026-08-13. The header used to state one all-time
    # "N sessões · X tokens" that no period ever re-scoped, so under "ontem"
    # every project header stayed on screen quoting the whole history. Two
    # things follow from that: the counts are pre-rendered once per period
    # (one .period-block span each, swapped by the same selectPeriod call
    # that swaps the charts), and the header carries data-periods so it can
    # be hidden outright when no session of its own belongs to the active
    # period - a header reading "0 sessões" would be worse than no header.
    project_path = group["project"]
    label = metrics_core_module.project_display_label(project_path, lang=lang)
    title_attribute = f' title="{html.escape(project_path)}"' if project_path else ""
    member_periods = []
    counts = []
    for period in metrics_core_module.PERIODS:
        scoped = metrics_core_module.filter_sessions_by_window(
            group["records"], period_windows[period.id]
        )
        if scoped:
            member_periods.append(period.id)
        total_tokens = metrics_core_module.format_tokens(
            sum(metrics_core_module.record_token_total(record) for record in scoped)
        )
        hidden_attribute = "" if period.id == metrics_core_module.DEFAULT_PERIOD_ID else " hidden"
        session_count = metrics_core_module.tn(
            "sessions", len(scoped), lang, n=metrics_core_module.format_tokens(len(scoped))
        )
        counts.append(
            f'<span class="group-count period-block" data-period="{period.id}"{hidden_attribute}>'
            f"({session_count} · {total_tokens} tokens)</span>"
        )
    return (
        f'<div class="group-header collapsed {group["class"]}" '
        f'id="group-{group_index}"{title_attribute} '
        f'data-periods="{" ".join(member_periods)}" '
        'role="button" tabindex="0" aria-expanded="false" '
        f'onclick="toggleGroup({group_index})">'
        '<span class="group-icon">▸</span>'
        f"<span>{html.escape(label)} {''.join(counts)}</span>"
        "</div>"
    )


def render_session_header(show_tmux: bool = True, *, lang: str) -> str:
    tmux_span = (
        f'<span class="tmux-window">{metrics_core_module.t("col.tmux", lang)}</span>'
        if show_tmux
        else ""
    )
    return (
        '<div class="session-row session-header">'
        '<span class="sid">id</span>'
        f'<span class="date-col">{metrics_core_module.t("col.date", lang)}</span>'
        '<span class="tokens-total">tokens</span>'
        f'<span class="comp-header">{metrics_core_module.t("col.composition", lang)}</span>'
        f'<span class="duration-col">{metrics_core_module.t("col.duration", lang)}</span>'
        '<span class="subagent-col">🤖</span>'
        f"{tmux_span}"
        f'<span class="label-col">{metrics_core_module.t("col.task", lang)}</span>'
        '<span class="tag-col">tag</span>'
        '<span class="branch-col">branch</span>'
        "</div>"
    )


def render_detail_panel(
    session_id: str,
    subagents: list[dict[str, Any]],
    is_active: bool,
    period_ids: tuple[str, ...] = ("tudo",),
    *,
    lang: str,
) -> str:
    open_class = " open" if is_active else ""
    title = ""
    subagents = [
        subagent for subagent in subagents if metrics_core_module.record_token_total(subagent) > 0
    ]
    if not subagents:
        body = f'<p class="empty">{metrics_core_module.t("detail.no_subagents", lang)}</p>'
    else:
        ordered = sorted(
            subagents, key=lambda subagent: subagent.get("timestamp_start") or "", reverse=True
        )
        title = (
            f'<h3 class="subagent-title">'
            f"{metrics_core_module.t('detail.subagents_title', lang, n=len(ordered))}</h3>"
        )
        header = (
            '<div class="subagent-row subagent-header">'
            f"<span>{metrics_core_module.t('col.date', lang)}</span>"
            f"<span>{metrics_core_module.t('col.task', lang)}</span>"
            "<span>tokens</span>"
            f"<span>tools</span><span>{metrics_core_module.t('col.duration', lang)}</span>"
            "</div>"
        )
        rows = [header]
        for row_index, subagent in enumerate(ordered):
            duration_text = metrics_core_module.format_duration(subagent.get("duration_seconds"))
            agent_type = html.escape(subagent.get("agent_type") or "-")
            agent_label = subagent.get("agent_label")
            if agent_label:
                task_text = f"{agent_type} — {html.escape(agent_label)}"
            else:
                task_text = f"{agent_type} {metrics_core_module.t('no_transcript', lang)}"
            date_text = metrics_core_module.format_date(subagent.get("timestamp_start"), lang=lang)
            # A composition bar instead of the old "{in}/{out} tok" text
            # (2026-07-21) - the slash format was ambiguous about which
            # number was which, and this also gives subagent rows the
            # same click-to-filter behavior session rows already had via
            # highlightTokenType, sharing the exact legend/highlight
            # wiring instead of needing its own.
            comp_bar = render_composition_bar(subagent)
            # Numeric total alongside the bar (2026-07-22: "coloca o
            # número de tokens gastos") - the bar alone showed the
            # composition/proportions but never the actual amount,
            # unlike the session table's tokens column which has both.
            subagent_total_tokens = render_floor_total(subagent, "floor-total", lang=lang)
            zebra_class = " subagent-row-odd" if row_index % 2 == 1 else ""
            rows.append(
                f'<div class="subagent-row{zebra_class}">'
                f"<span>{date_text}</span>"
                f"<span>{task_text}</span>"
                f'<span class="subagent-tokens-cell">{subagent_total_tokens} {comp_bar}</span>'
                f"<span>{subagent.get('tool_call_count', 0)} tools</span>"
                f"<span>{duration_text}</span>"
                "</div>"
            )
        body = "\n".join(rows)
    # Period-aware like the row it belongs to (2026-08-13): carries the same
    # data-periods attribute as the session row, swept by the same
    # selectPeriod() loop, so a panel left open for a session that falls
    # outside the newly-selected period disappears with its row instead of
    # floating on screen describing a session nobody can see anymore.
    # Open/closed state moved from an inline style to the "open" class
    # (previously the only visibility mechanism here, always inert to
    # class rules) specifically so it can compose with .period-hidden
    # instead of an inline style unconditionally beating it - the exact
    # inline-vs-class fight render_session_row was fixed to avoid.
    return (
        f'<div class="detail-panel{open_class}" id="detail-{session_id}" '
        f'data-periods="{" ".join(period_ids)}">'
        f'<button class="detail-close" onclick="closeDetail(\'{session_id}\')" '
        f'aria-label="{html.escape(metrics_core_module.t("detail.close", lang), quote=True)}">'
        "×</button>"  # noqa: RUF001 - multiplication sign in shipped HTML, not a confusable identifier
        f"<h2>{session_id}</h2>{title}{body}</div>"
    )


def render_detail_source(panels: dict[str, str]) -> str:
    """The panels that carry subagents, pre-rendered, as one inert JSON block.

    Measured 2026-10-24 on the full history: 3,775 panels were 49% of the
    page's 116,462 elements and the axe collector timed out walking them.
    The 166 that hold subagents cost most of that; the page script builds
    one from this block on first open. `</` and `<!--` are escaped so a
    label cannot end the block early. No panels, no block.
    """
    if not panels:
        return ""
    payload = json.dumps(panels, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\u0021--")
    return f'<script type="application/json" id="detail-source">{payload}</script>'


def render_empty_panel_template(*, lang: str) -> str:
    """The one panel every session without subagents is cloned from."""
    panel = render_detail_panel("", [], False, lang=lang)
    return f'<template id="detail-empty-template">{panel}</template>'


# The class of every project without one of the eight colour slots
# (metrics_core.assign_group_colors); drawn as one "others" segment.
OTHERS_CLASS = "group-color-other"
# The group-color-N classes the stylesheet defines (dashboard_html_assets.py).
MODEL_COLOR_SLOTS = 30


def model_color_css_class(slot: metrics_core_module.ModelColorSlot) -> str:
    """The chart-bar/legend-swatch class for one model's colour slot.

    A slot past the palette (is_overflow) never renders as the plain
    "group-color-N" class its colour's original owner uses: the two would
    then share a data-highlight token, and clicking either one would light
    up both, despite being otherwise unrelated. model-overflow-N is its own
    class, styled to the SAME fill (dashboard_html_assets.py mirrors every
    group-color-N rule for it) but with its own spotlight rule, so overflow
    models are visually grouped with their colour's owner yet stay
    independently clickable.

    Every model this chart draws IS in `slots` - it is either the model_order
    passed to assign_model_slots directly (render_trend_chart's own
    fallback) or a subset of the full-history model set assign_model_slots
    was called with in render_html (a window can only narrow which records
    are visible, never introduce a model absent from the unfiltered
    history) - so callers index `slots` directly rather than calling this
    with an Optional and a "no slot at all" fallback that could never run.
    The genuinely unattributed "no model" band is a separate, always-known
    class (see unattributed_class in render_trend_chart), never routed
    through here.
    """
    prefix = "model-overflow" if slot.is_overflow else "group-color"
    return f"{prefix}-{slot.palette_index + 1}"


def render_trend_chart(
    records: list[dict[str, Any]],
    group_colors: dict[str | None, str],
    period_id: str,
    now: datetime | None = None,
    *,
    lang: str,
    model_slots: dict[str, metrics_core_module.ModelColorSlot] | None = None,
) -> str:
    granularity, days = metrics_core_module.aggregate_by_day(records)
    parse_format = metrics_core_module.GRANULARITY_CONFIG[granularity]["parse_fmt"]
    day_count = len(days)
    if day_count < 2:
        return ""

    # The last bucket is always a fraction of a full one while it's still
    # running (backlog, 2026-09-02: "the newest bar always reads as a
    # decline") - flagged here, once, rather than separately in each of
    # bars_for_pane/bars_for_tokens/the x-axis loop below.
    partial_fraction = metrics_core_module.partial_bucket_fraction(
        granularity, days[-1]["day"], now or datetime.now().astimezone()
    )

    day_totals = [
        sum(metrics_core_module.record_token_total(record) for record in day["records"])
        for day in days
    ]
    # The two tabs now decompose the SAME quantity - total tokens - one
    # split by project, the other by token type, so they deliberately
    # share one scale: a full-height bar means the same thing on both,
    # and switching tabs re-slices a day rather than rescaling it.
    #
    # This replaces an earlier split-denominator fix. That one was right
    # about its symptom (cache-heavy days starving the "por projeto"
    # bars) but wrong about the cause: "por projeto" was summing only
    # input+output, so it wasn't a scale problem at all - the mode was
    # measuring a different quantity, orders of magnitude smaller than its own axis
    # labels and tooltips claimed (2026-08-10).
    # The scale is the axis's top tick, not the busiest bucket: the y labels
    # sit on round values (nice_axis_ticks), so a bar must be measured
    # against the same top or it would disagree with its own gridlines.
    axis_ticks = metrics_core_module.nice_axis_ticks(max(day_totals))
    maximum_pane = axis_ticks[-1]
    maximum_tokens = maximum_pane

    height = 150
    padding_left = 45
    padding_right = 10
    padding_top = 10
    padding_bottom = 20
    minimum_bar_width = 8
    bar_gap = 2

    # Bars represent DAYS now, not sessions, so day_count is naturally much
    # smaller/bounded than the old per-session count ever was — but a
    # long --days/--all history can still have many distinct days. Try
    # the normal clamp-based width first; if that would squeeze bars
    # below the 8px floor (dataviz's minimum mark size), size the chart
    # to fit every bar at exactly the floor width instead and let it
    # scroll horizontally — this function has already shipped two real
    # geometry bugs from letting bar width shrink toward zero, so this
    # floor is a hard rule, not a suggestion.
    # Panorama is now this chart's own tab (it no longer shares the page
    # with the session table), so there's meaningfully more room. Bumps the
    # per-day step (12px -> 16px, ~33% wider bars in the common case) and
    # drops the old 1000px ceiling entirely, matching "sem teto fixo." The
    # 8px floor / scrollable-fallback logic below this point is UNCHANGED —
    # this only changes how generously wide the non-floor-hit case renders.
    # 3x wider by default (2026-07-21 feedback, after item 1 made width a
    # fixed pixel value instead of stretchy) - per-day step 16px -> 48px,
    # floor 400 -> 1200, so days read as more spread out; .trend-scroll
    # handles anything past the visible container via horizontal scroll.
    candidate_width = max(1200, 40 + day_count * 48)
    plot_width = candidate_width - padding_left - padding_right
    step = plot_width / day_count
    bar_width = step * 0.6
    # Confirmed dead (PROJECT-LOG 2026-07-18, round 4): removing the
    # 1000px width cap made bar_width mathematically unreachable below
    # ~28.4px for any day_count - a deliberate decision with Alberto to keep this
    # fallback anyway rather than delete it. Re-verified by exhaustive
    # sweep during coverage-phase3c Task 3 (2026-08-31): day_count swept 1..20000
    # (both algebraically and by calling this function directly), global
    # minimum bar_width ~28.44px at day_count=25, never within reach of the floor.
    if bar_width < minimum_bar_width:  # pragma: no cover - unreachable, see comment above
        step = minimum_bar_width + bar_gap
        bar_width = minimum_bar_width
        plot_width = step * day_count
        width = plot_width + padding_left + padding_right
        scrollable = True
    else:
        width = candidate_width
        scrollable = False

    plot_height = height - padding_top - padding_bottom

    def y_for(value: float, maximum_value: float) -> float:
        return padding_top + plot_height - (value / maximum_value) * plot_height

    def build_y_axis(maximum_value: float) -> str:
        parts = []
        for value in axis_ticks:
            vertical_position = y_for(value, maximum_value)
            parts.append(
                f'<line x1="{padding_left}" y1="{vertical_position:.1f}" '
                f'x2="{width - padding_right}" y2="{vertical_position:.1f}" '
                'class="chart-gridline" />'
                f'<text x="4" y="{vertical_position + 3:.1f}" class="chart-axis-label">'
                f"{metrics_core_module.format_tokens(value)}</text>"
            )
        return "".join(parts)

    # Tooltips name a bucket the way the x axis does ("S41/26", "Aug 5"),
    # never by its raw key ("2026-W41-1") - 2026-10-05 review.
    bucket_labels = [
        metrics_core_module.tick_label(
            granularity, datetime.strptime(day["day"], parse_format), lang
        )
        for day in days
    ]

    tick_labels = []
    tick_count = min(5, day_count)
    for tick in range(tick_count):
        day_index = round(tick * (day_count - 1) / (tick_count - 1)) if tick_count > 1 else 0
        tick_position = padding_left + day_index * step + bar_width / 2
        bucket_dt = datetime.strptime(days[day_index]["day"], parse_format)
        tick_date = metrics_core_module.tick_label(granularity, bucket_dt, lang)
        if day_index == day_count - 1 and partial_fraction is not None:
            tick_date += f" · {round(partial_fraction * 100)}%"
        tick_labels.append(
            f'<text x="{tick_position:.1f}" y="{height - 4}" class="chart-axis-label" '
            f'text-anchor="middle">{tick_date}</text>'
        )
    tick_labels_html = "".join(tick_labels)

    # Highlight band for the last 7 buckets (2026-07-21: was "last 7
    # calendar days" specifically, but with hour/month/year granularity
    # now possible too, "last 7 of whatever unit is on screen" is the
    # only version of this that still makes sense across all of them).
    highlight_html = ""
    band_note_html = ""
    if day_count > 7:
        highlight_start_index = day_count - 7
        highlight_left = padding_left + highlight_start_index * step
        hwidth = 7 * step
        # The band explains itself (2026-10-05 review: nothing on the page
        # said what it meant) - as a tooltip on the band and a caption.
        band_note = html.escape(metrics_core_module.t("chart.band_note", lang))
        highlight_html = (
            f'<rect x="{highlight_left:.1f}" y="{padding_top}" '
            f'width="{hwidth:.1f}" height="{plot_height}" '
            f'class="chart-highlight-band"><title>{band_note}</title></rect>'
        )
        band_note_html = f'<p class="chart-band-note">{band_note}</p>'

    # Tooltip suffix and hatch overlay for the last bucket while it's
    # still partial - `hatch_url` is None for a complete last bucket, so
    # every bars_for_* segment's `if hatch_url and day_index == day_count - 1` guard below
    # is skipped entirely and nothing about a complete chart changes.
    partial_note = (
        metrics_core_module.t("partial", lang, pct=round(partial_fraction * 100))
        if partial_fraction is not None
        else ""
    )

    def hatch_overlay(
        hatch_id: str,
        horizontal_position: float,
        vertical_position: float,
        rect_width: float,
        rect_height: float,
    ) -> str:
        return (
            f'<rect x="{horizontal_position:.1f}" y="{vertical_position:.1f}" '
            f'width="{rect_width:.1f}" height="{rect_height:.1f}" '
            f'class="chart-bar-partial" fill="url(#{hatch_id})" pointer-events="none" />'
        )

    def pane_bucket(project: str | None) -> str | None:
        """The segment a project's tokens go to: its own path, or the one
        "others" bucket. The bucket's key is the class name, which no real
        project path can collide with - paths are absolute."""
        return OTHERS_CLASS if group_colors.get(project) == OTHERS_CLASS else project

    def bars_for_pane() -> str:
        bars = []
        hatch_id = f"chart-hatch-pane-{period_id}"
        for day_index, day in enumerate(days):
            bar_left = padding_left + day_index * step + (step - bar_width) / 2
            # Projects without a colour slot share one "others" segment:
            # drawn apart, each would be a gray block nobody can tell from
            # its neighbour (2026-10-05 review).
            by_project: dict[str | None, int] = {}
            project_order: list[str | None] = []
            for record in day["records"]:
                project = pane_bucket(record.get("project_path"))
                if project not in by_project:
                    by_project[project] = 0
                    project_order.append(project)
                by_project[project] += metrics_core_module.record_token_total(record)
            vertical_cursor: float = padding_top + plot_height
            for project in project_order:
                value = by_project[project]
                if value <= 0:
                    continue
                seg_height = (value / maximum_pane) * plot_height if maximum_pane else 0
                vertical_cursor -= seg_height
                group_class = group_colors.get(project, "group-color-none")
                if project == OTHERS_CLASS:
                    group_class = OTHERS_CLASS
                    project_label = metrics_core_module.t("others", lang)
                else:
                    project_label = metrics_core_module.project_display_label(project, lang=lang)
                note = partial_note if day_index == day_count - 1 else ""
                bars.append(
                    f'<rect x="{bar_left:.1f}" y="{vertical_cursor:.1f}" '
                    f'width="{bar_width:.1f}" height="{seg_height:.1f}" '
                    f'class="chart-bar {group_class}" '
                    f"onclick=\"highlightProject('{group_class}', event)\">"
                    f"<title>{html.escape(project_label)} · {bucket_labels[day_index]} - "
                    f"{metrics_core_module.format_tokens(value)} tokens{note}</title></rect>"
                )
                if day_index == day_count - 1 and partial_fraction is not None:
                    bars.append(
                        hatch_overlay(hatch_id, bar_left, vertical_cursor, bar_width, seg_height)
                    )
        return "".join(bars)

    def bars_for_tokens() -> str:
        bars = []
        hatch_id = f"chart-hatch-tokens-{period_id}"
        for day_index, day in enumerate(days):
            bar_left = padding_left + day_index * step + (step - bar_width) / 2
            segments: list[tuple[str, int]] = [
                ("series-1", sum(record.get("input_tokens", 0) or 0 for record in day["records"])),
                ("series-2", sum(record.get("output_tokens", 0) or 0 for record in day["records"])),
                (
                    "series-3",
                    sum(record.get("cache_read_tokens", 0) or 0 for record in day["records"]),
                ),
                (
                    "series-4",
                    sum(record.get("cache_creation_tokens", 0) or 0 for record in day["records"]),
                ),
            ]
            vertical_cursor: float = padding_top + plot_height
            for css_class, value in segments:
                if value <= 0:
                    continue
                seg_height = (value / maximum_tokens) * plot_height if maximum_tokens else 0
                vertical_cursor -= seg_height
                type_name = SERIES_NAMES[css_class]
                note = partial_note if day_index == day_count - 1 else ""
                bars.append(
                    f'<rect x="{bar_left:.1f}" y="{vertical_cursor:.1f}" '
                    f'width="{bar_width:.1f}" height="{seg_height:.1f}" '
                    f'class="chart-bar {css_class}" '
                    f"onclick=\"highlightTokenType('{css_class}', event)\">"
                    f"<title>{type_name} · {bucket_labels[day_index]} - "
                    f"{metrics_core_module.format_tokens(value)} tokens{note}</title></rect>"
                )
                if day_index == day_count - 1 and partial_fraction is not None:
                    bars.append(
                        hatch_overlay(hatch_id, bar_left, vertical_cursor, bar_width, seg_height)
                    )
        return "".join(bars)

    # Every model seen anywhere in the window gets the same colour in every
    # bucket, so a band means one thing across the whole chart instead of
    # shifting with each day's cast. Ordered by spend over the window, same
    # reasoning as the "por projeto" legend above.
    model_window_totals: dict[str, int] = {}
    for day in days:
        for model, amount in metrics_core_module.compute_model_totals(day["records"])[0].items():
            model_window_totals[model] = model_window_totals.get(model, 0) + amount
    model_order = sorted(
        model_window_totals, key=lambda model: model_window_totals[model], reverse=True
    )
    # The bands join the SAME group-color-N vocabulary the "por projeto"
    # bars use, rather than getting one of their own. That is what makes a
    # band clickable for free: the highlight is a set of class names on
    # <body>, and the CSS spotlight rules already exist for these classes.
    # The precedent is documented in the stylesheet itself - series-N and
    # group-color-N already share one data-highlight attribute "since only
    # one trend-mode pane is ever visible at once" - and showTrendMode now
    # clears the highlight when the pane changes, so a model's slot can
    # never be left lighting up a project that happens to share it.
    #
    # The slot is the model's own, not its rank in this period (backlog
    # 2026-09-24 #6): ranked, a model changed colour between periods
    # whenever another one outspent it. render_html settles the slots once,
    # over the whole history, so every period's chart agrees; a chart
    # rendered on its own settles them over its own records. A model past
    # the last slot reuses an earlier model's colour rather than the
    # unattributed one - see model_color_css_class and
    # ModelColorSlot.is_overflow - so it stays visually distinct from the
    # "no model" remainder band and independently clickable.
    slots = (
        model_slots
        if model_slots is not None
        else metrics_core_module.assign_model_slots(model_order, MODEL_COLOR_SLOTS)
    )
    unattributed_class = "group-color-none"
    model_class = {model: model_color_css_class(slots[model]) for model in model_order}

    def bars_for_model() -> str:
        bars = []
        hatch_id = f"chart-hatch-model-{period_id}"
        for day_index, day in enumerate(days):
            bar_left = padding_left + day_index * step + (step - bar_width) / 2
            by_model, unattributed = metrics_core_module.compute_model_totals(day["records"])
            # The remainder sits LAST (on top) so the models stay adjacent
            # to one another, and wears the "no project" grey: both mean
            # "everything this chart cannot name".
            segments: list[tuple[str, str, int]] = [
                (
                    metrics_core_module.model_display_label(model),
                    model_class[model],
                    by_model.get(model, 0),
                )
                for model in model_order
            ]
            segments.append(
                (metrics_core_module.t("no_model", lang), unattributed_class, unattributed)
            )
            vertical_cursor: float = padding_top + plot_height
            for label, css_class, value in segments:
                if value <= 0:
                    continue
                seg_height = (value / maximum_pane) * plot_height if maximum_pane else 0
                vertical_cursor -= seg_height
                note = partial_note if day_index == day_count - 1 else ""
                bars.append(
                    f'<rect x="{bar_left:.1f}" y="{vertical_cursor:.1f}" '
                    f'width="{bar_width:.1f}" height="{seg_height:.1f}" '
                    f'class="chart-bar {css_class}" '
                    f"onclick=\"highlightModel('{css_class}', event)\">"
                    f"<title>{html.escape(label)} · {bucket_labels[day_index]} - "
                    f"{metrics_core_module.format_tokens(value)} tokens{note}</title></rect>"
                )
                if day_index == day_count - 1 and partial_fraction is not None:
                    bars.append(
                        hatch_overlay(hatch_id, bar_left, vertical_cursor, bar_width, seg_height)
                    )
        return "".join(bars)

    # width is fixed in CSS pixels (not 100%/responsive): with
    # preserveAspectRatio="none" a stretchy width forces the browser to
    # scale the viewBox's x-axis to fill the container, which distorts
    # every bar and label horizontally (they render at their real size
    # only when 1 viewBox unit == 1 CSS pixel). .trend-scroll's
    # overflow-x:auto gives the "long axis" instead, by scrolling past
    # this fixed width rather than squishing/stretching it.
    # Every id below is suffixed with period_id - render_trend_chart is
    # now called once per period (see the trend_blocks loop in
    # render_html), and a fixed id would repeat once per period,
    # leaving getElementById always resolving to the FIRST period's
    # element regardless of which one is actually visible. Duplicate ids
    # are also invalid HTML on their own.
    # Diagonal hatch, referenced from bars_for_pane/bars_for_tokens above,
    # to draw the partial last bucket as visibly "in progress" rather than
    # a flat, seemingly-final color like every complete bucket beside it.
    # Only emitted when there IS a partial bucket - an unreferenced
    # <pattern> is harmless, but every other render_trend_chart call site
    # (the ~15 existing tests, plus any period whose last bucket has
    # already fully elapsed) stays byte-identical to before this feature.
    def hatch_defs(hatch_id: str) -> str:
        if partial_fraction is None:
            return ""
        return (
            f'<defs><pattern id="{hatch_id}" width="6" height="6" '
            'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            '<rect x="0" y="0" width="6" height="6" class="chart-hatch-bg" />'
            '<line x1="0" y1="0" x2="0" y2="6" class="chart-hatch-line" />'
            "</pattern></defs>"
        )

    pane_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" '
        f'id="chart-pane-{period_id}" preserveAspectRatio="none" '
        f'role="img" aria-label="{html.escape(metrics_core_module.t("chart_title.pane", lang))}" '
        f'style="width:{width}px">'
        f"{hatch_defs(f'chart-hatch-pane-{period_id}')}"
        f"{highlight_html}{build_y_axis(maximum_pane)}{tick_labels_html}{bars_for_pane()}</svg>"
    )
    tokens_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" '
        f'id="chart-tokens-{period_id}" preserveAspectRatio="none" '
        f'role="img" aria-label="{html.escape(metrics_core_module.t("chart_title.tokens", lang))}" '
        f'style="width:{width}px">'
        f"{hatch_defs(f'chart-hatch-tokens-{period_id}')}"
        f"{highlight_html}{build_y_axis(maximum_tokens)}{tick_labels_html}{bars_for_tokens()}</svg>"
    )
    model_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" '
        f'id="chart-model-{period_id}" preserveAspectRatio="none" '
        f'role="img" aria-label="{html.escape(metrics_core_module.t("chart_title.model", lang))}" '
        f'style="width:{width}px">'
        f"{hatch_defs(f'chart-hatch-model-{period_id}')}"
        f"{highlight_html}{build_y_axis(maximum_pane)}{tick_labels_html}{bars_for_model()}</svg>"
    )

    # Always scrollable now, not just in the floor-hit (scrollable=True)
    # case: with the width cap removed (an earlier round), a long real
    # history can make the chart genuinely very wide — the outer
    # .trend-scroll container (CSS, always overflow-x:auto) keeps the page
    # itself from breaking, and the auto-scroll-to-end script (below, in
    # render_html) shows the most recent days by default rather than the
    # oldest. data-floor-hit is kept purely as a debugging/testing signal
    # for whether the 8px floor engaged.
    #
    # Round 5 removed the toggle because a hidden chart's meaning wasn't
    # discoverable without clicking first - but with both rendered at
    # full width unconditionally, each one only gets the fixed-width
    # treatment from item 1 at the cost of showing both, all the time,
    # taking up sticky-header space. Reintroduced here (2026-07-20) as
    # explicitly labeled tabs ("por projeto" / "por tipo de token"),
    # which fixes the discoverability complaint without giving up the
    # single-chart-at-a-time layout.
    # Legend for "por projeto"'s bar colors (2026-07-21) - those colors
    # were only ever explained by the session-list rows' matching left
    # border, which isn't visible from up here. Built from the projects
    # actually present in this chart's own data, in first-seen order,
    # deduped by color_group_key so a worktree collapses into its
    # parent's single entry instead of getting its own near-duplicate
    # (they already share a color, per assign_group_colors above).
    # Ordered by spend within this period, not by first appearance -
    # same reasoning as compute_project_totals. Kept keyed by
    # color_group_key so a worktree stays merged into its parent's single
    # entry, and the first raw path seen for a group is what the chip
    # links to, exactly as before.
    pane_totals: dict[str | None, int] = {}
    pane_first_path: dict[str | None, Any] = {}
    has_others = False
    for day in days:
        for record in day["records"]:
            project = record.get("project_path")
            if pane_bucket(project) == OTHERS_CLASS:
                has_others = has_others or metrics_core_module.record_token_total(record) > 0
                continue
            key = metrics_core_module.color_group_key(project)
            pane_totals[key] = pane_totals.get(key, 0) + metrics_core_module.record_token_total(
                record
            )
            pane_first_path.setdefault(key, project)
    pane_projects = [
        pane_first_path[key]
        for key in sorted(pane_totals, key=lambda sort_key: pane_totals[sort_key], reverse=True)
    ]
    pane_legend_chips = []
    for project in pane_projects:
        group_class = group_colors.get(project, "group-color-none")
        # Labeled by the PARENT project (color_group_key), not the
        # specific worktree first seen (2026-07-21 fix: a merged entry
        # could pick up a random worktree's own name as if it were the
        # whole group's name - e.g. "some-worktree-name:my-project /
        # some-worktree-name" - wrong name AND redundant). Parent path
        # -> plain basename, so raw_name and label naturally match and
        # the ":" is dropped.
        parent = metrics_core_module.color_group_key(project)
        raw_name = html.escape(
            Path((parent or "").rstrip("/")).name or metrics_core_module.t("no_project", lang)
        )
        label = html.escape(metrics_core_module.project_display_label(parent, lang=lang))
        chip_text = raw_name if raw_name == label else f"{raw_name}:{label}"
        pane_legend_chips.append(
            f'<span class="legend-chip" {chip_attributes(group_class)}'
            f"onclick=\"highlightProject('{group_class}', event)\">"
            f'<span class="legend-swatch {group_class}"></span>{chip_text}</span>'
        )
    # "Others" is listed last whatever its size: it is the remainder of the
    # legend, not a project competing for a place in it.
    if has_others:
        pane_legend_chips.append(
            f'<span class="legend-chip" {chip_attributes(OTHERS_CLASS)}'
            f"onclick=\"highlightProject('{OTHERS_CLASS}', event)\">"
            f'<span class="legend-swatch {OTHERS_CLASS}"></span>'
            f"{metrics_core_module.t('others', lang)}</span>"
        )
    pane_legend = "".join(pane_legend_chips)
    pane_legend_html = f'<div class="project-chart-legend">{pane_legend}</div>'
    # Chip and band carry the same class and call the same function, so
    # clicking either one spotlights the same model.
    model_legend_chips = [
        f'<span class="legend-chip" {chip_attributes(model_class[model])}'
        f"onclick=\"highlightModel('{model_class[model]}', event)\">"
        f'<span class="legend-swatch {model_class[model]}"></span>'
        f"{html.escape(metrics_core_module.model_display_label(model))}</span>"
        for model in model_order
    ]
    model_legend_chips.append(
        f'<span class="legend-chip" {chip_attributes(unattributed_class)}'
        f"onclick=\"highlightModel('{unattributed_class}', event)\">"
        f'<span class="legend-swatch {unattributed_class}"></span>'
        f"{metrics_core_module.t('no_model', lang)}</span>"
    )
    model_legend_html = f'<div class="project-chart-legend">{"".join(model_legend_chips)}</div>'

    return (
        '<div class="trend-container">'
        '<div class="trend-mode-tabs" role="tablist">'
        f'<span class="tab-btn active" id="trend-btn-pane-{period_id}" '
        'role="tab" tabindex="0" aria-selected="true" '
        f'aria-controls="trend-pane-pane-{period_id}" '
        f"onclick=\"showTrendMode('pane', '{period_id}')\">"
        f"{metrics_core_module.t('mode.pane', lang)}</span>"
        f'<span class="tab-btn" id="trend-btn-tokens-{period_id}" '
        'role="tab" tabindex="-1" aria-selected="false" '
        f'aria-controls="trend-pane-tokens-{period_id}" '
        f"onclick=\"showTrendMode('tokens', '{period_id}')\">"
        f"{metrics_core_module.t('mode.tokens', lang)}</span>"
        f'<span class="tab-btn" id="trend-btn-model-{period_id}" '
        'role="tab" tabindex="-1" aria-selected="false" '
        f'aria-controls="trend-pane-model-{period_id}" '
        f"onclick=\"showTrendMode('model', '{period_id}')\">"
        f"{metrics_core_module.t('mode.model', lang)}</span>"
        "</div>"
        f'<div class="trend-pane active" id="trend-pane-pane-{period_id}" role="tabpanel" '
        f'aria-labelledby="trend-btn-pane-{period_id}">'
        f'<div class="trend-scroll" data-floor-hit="{str(scrollable).lower()}">{pane_svg}</div>'
        # Fixed-height, internally-scrollable slot (2026-07-21) - "por
        # projeto" can have many legend chips wrapping to several lines
        # while "por tipo de token" only ever has 4; without a fixed
        # height here the sticky trend-container's total height changed
        # between the two modes, pushing the session table up/down
        # ("pulando") every time you switched.
        f'<div class="trend-legend-slot">{pane_legend_html}</div>'
        "</div>"
        f'<div class="trend-pane" id="trend-pane-tokens-{period_id}" role="tabpanel" '
        f'aria-labelledby="trend-btn-tokens-{period_id}">'
        f'<div class="trend-scroll" data-floor-hit="{str(scrollable).lower()}">{tokens_svg}</div>'
        '<div class="trend-legend-slot"></div>'
        "</div>"
        f'<div class="trend-pane" id="trend-pane-model-{period_id}" role="tabpanel" '
        f'aria-labelledby="trend-btn-model-{period_id}">'
        f'<div class="trend-scroll" data-floor-hit="{str(scrollable).lower()}">{model_svg}</div>'
        f'<div class="trend-legend-slot">{model_legend_html}</div>'
        "</div>"
        f"{band_note_html}"
        "</div>"
    )


def render_project_totals_chart(
    records: list[dict[str, Any]],
    project_first_seen: dict[str, str],
    project_last_seen: dict[str, str],
    open_projects: set[str],
    period_id: str,
    *,
    lang: str,
) -> str:
    ordered_projects, _totals, _max_total = metrics_core_module.compute_project_totals(
        records, project_last_seen
    )
    # Segmented by token type (2026-07-20) instead of one flat bar, so the
    # composition legend can highlight/filter these bars the same way it
    # already does the trend chart's "por tipo de token" mode - same
    # series-N classes, same highlightTokenType handler, same CSS.
    series_by_project, totals = metrics_core_module.compute_project_series_totals(records)
    # Omit zero-token projects (2026-07-20) - a project section with
    # nothing to show is just dead weight in this list.
    ordered_projects = [project for project in ordered_projects if totals.get(project, 0) > 0]
    if not ordered_projects:
        return ""
    # The "or 1" fallback is unreachable: the early return just above
    # already excludes every all-zero/empty scenario (a project only
    # survives the filter above if its total is > 0), so by the time
    # max(totals.values()) runs at least one project is guaranteed
    # positive. Re-verified by running with all-zero and empty inputs
    # during coverage-phase3c Task 3 (2026-08-31) - both return "" before
    # reaching this line, never fall through to it with a zero max. Kept
    # as cheap insurance against a future refactor removing that early
    # return.
    #
    # No pragma here, deliberately: coverage.py measures statements and
    # control-flow arcs, never the operands of a boolean expression. The
    # assignment itself runs on every call and is covered by real tests,
    # so a no-cover pragma would not be excusing anything unreachable - it
    # would drop a live, tested line out of the denominator and blind the
    # phase-4 gate to it. (Spelling that pragma out literally here would
    # also be a bug: coverage.py matches its exclude regex against comment
    # text, so the words alone are enough to arm the directive.)
    maximum_total = max(totals.values()) or 1

    # row_height must clear label_baseline(=row start) + bar(9..23) + the
    # NEXT row's label ascent, or that next label overlaps up into this
    # row's bar - 24 left only 1px between a bar's bottom (y+23) and the
    # next label's baseline (y+24), and a ~10px font's ascent (~7-8px)
    # ate straight into the bar above it. 34 leaves real clearance.
    row_height = 34
    bar_maximum_width = 300
    label_offset = 4
    # 620, not 460 (2026-07-21) - the total text ("<total> · há N dias ·
    # <filtered value>") got clipped at the SVG's right edge once the
    # "grand total + filtered value together" feedback made that string
    # longer; 460-310=150 user units wasn't enough room for it.
    width = 620

    def render_rows(projects: list[str | None]) -> tuple[list[str], int]:
        rows = []
        row_top = 14
        for project in projects:
            total = totals[project]
            label = metrics_core_module.project_display_label(project, lang=lang)
            title_attribute = f' title="{html.escape(project)}"' if project else ""
            is_open = project in open_projects
            # Bar color now carries token-type (to match the legend), so
            # "open now" moved from bar color to a small text marker next
            # to the label instead of being lost. An SVG <title> element
            # explains what the marker means to readers who might think it
            # is scoped to the period filter.
            open_marker = (
                f" <title>{metrics_core_module.t('open_now', lang)}</title>●" if is_open else ""
            )
            days_running = metrics_core_module.project_days_running(
                project_first_seen, project, lang=lang
            )
            seg_rects = []
            segment_cursor: float = label_offset
            for css_class, value in series_by_project.get(project, {}).items():
                if value <= 0:
                    continue
                seg_width = (value / maximum_total) * bar_maximum_width if maximum_total else 0
                name = SERIES_NAMES[css_class]
                seg_rects.append(
                    f'<rect x="{segment_cursor:.1f}" y="{row_top + 9}" '
                    f'width="{seg_width:.1f}" height="14" '
                    f'class="chart-bar {css_class}" '
                    f"onclick=\"highlightTokenType('{css_class}', event)\">"
                    f"<title>{html.escape(label)} · {name} · "
                    f"{metrics_core_module.format_tokens(value)}</title></rect>"
                )
                segment_cursor += seg_width
            # Total label carries the grand total (always visible, 2026-07-21
            # feedback: the colored per-type number alone wasn't enough,
            # the total needs to stay visible alongside it) plus one
            # separator+tspan per series, hidden by CSS unless
            # body[data-highlight] selects that series - clicking a
            # legend chip appends " · <that type>" next to the still-visible
            # grand total instead of replacing it. The per-series tspans
            # are italic and tinted that series' color so it reads as
            # "the filtered slice," not a second real total.
            project_series = series_by_project.get(project, {})
            series_tspans = "".join(
                f'<tspan class="total-only {css_class}"> · '
                f"{metrics_core_module.format_tokens(project_series.get(css_class, 0))}</tspan>"
                for css_class in ("series-1", "series-2", "series-3", "series-4")
            )
            total_text = (
                f'<text x="{label_offset + bar_maximum_width + 6}" y="{row_top + 20}" '
                'class="chart-axis-label proj-total">'
                f'<tspan class="total-grand">'
                f"{metrics_core_module.format_tokens(total)}{days_running}</tspan>"
                f"{series_tspans}"
                "</text>"
            )
            rows.append(
                f'<text x="{label_offset}" y="{row_top}" '
                f'class="project-chart-label"{title_attribute}>'
                f"{html.escape(label)}{open_marker}</text>"
                f'<rect x="{label_offset}" y="{row_top + 9}" width="{bar_maximum_width}" '
                'height="14" class="project-chart-track" />'
                f"{''.join(seg_rects)}"
                f"{total_text}"
            )
            row_top += row_height
        height = len(projects) * row_height + 20
        return rows, height

    visible_projects = ordered_projects[:10]
    hidden_projects = ordered_projects[10:]

    visible_rows, visible_height = render_rows(visible_projects)
    visible_svg = (
        f'<svg viewBox="0 0 {width} {visible_height}" class="project-chart-svg">'
        f"{''.join(visible_rows)}</svg>"
    )

    more_html = ""
    if hidden_projects:
        hidden_rows, hidden_height = render_rows(hidden_projects)
        # Suffixed with period_id for the same reason as render_trend_chart's
        # ids above - this function is now called once per period too.
        hidden_svg = (
            f'<svg viewBox="0 0 {width} {hidden_height}" class="project-chart-svg" '
            f'id="project-chart-more-{period_id}" style="display:none">{"".join(hidden_rows)}</svg>'
        )
        past_projects_text = metrics_core_module.tn("past_projects", len(hidden_projects), lang)
        more_html = (
            f'<div class="project-chart-toggle" id="project-chart-toggle-{period_id}" '
            f"onclick=\"toggleProjectsMore('{period_id}')\">"
            f"{past_projects_text}</div>"
            f"{hidden_svg}"
        )

    # Bar color now maps to token type (see render_rows above), sharing
    # the exact same legend markup/classes/click handler as the Sessões
    # tab - clicking a chip highlights that type's segments here too via
    # the same body[data-highlight~=...] CSS, no separate wiring needed.
    overall_totals = {
        css_class: sum(series.get(css_class, 0) for series in series_by_project.values())
        for css_class in ("series-1", "series-2", "series-3", "series-4")
    }
    legend = render_composition_legend(overall_totals, lang=lang) + (
        '<div class="project-chart-legend"><span class="legend-chip">'
        f"{metrics_core_module.t('legend.open_session_html', lang)}</span></div>"
    )
    return (
        '<div class="project-chart-container">'
        f'<h2 class="project-chart-title">'
        f"{metrics_core_module.t('project_totals_title', lang)}</h2>"
        '<p class="project-chart-sort-note">'
        f"{metrics_core_module.t('project_totals_sort_note', lang)}</p>"
        f"{legend}"
        f"{visible_svg}"
        f"{more_html}"
        "</div>"
    )


def build_group_colors(
    chart_records: list[dict[str, Any]],
    daily_chart_records: list[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str | None, str]:
    # The trend chart renders daily_chart_records (raw, pre-dedup snapshots
    # expanded into per-day deltas) while colours and the legend are built
    # from chart_records (one deduped representative per session). Any
    # project_path reachable only through the raw feed - a worktree, a
    # directory a session cd'ed out of before its final snapshot, a
    # subagent's own path - was therefore missing from the colour map and
    # fell back to "group-color-none", so clicking that bar highlighted
    # nothing in the legend (regression introduced by bd2e899; found
    # 2026-08-10 with over half of the rendered paths uncoloured).
    #
    # Both feeds are coloured, so a path reachable only through the deltas
    # still gets its class. Worktrees collapse onto their parent's colour,
    # because the slot is chosen by color_group_key rather than the raw path.
    # Spend is ranked over daily_chart_records alone, the feed the trend
    # chart draws: ranking both feeds would count every token twice, and a
    # path seen only in the deltas would rank at half its weight.
    return metrics_core_module.assign_group_colors(
        list(chart_records) + list(daily_chart_records),
        now=now,
        ranking_records=daily_chart_records,
    )


def render_backfill_note(*, lang: str) -> str:
    text = metrics_core_module.describe_backfill_state(
        metrics_core_module.load_backfill_state(), lang=lang
    )
    return f'<span class="backfill-note">{html.escape(text)}</span>'


def render_period_filter(*, lang: str) -> str:
    chips = []
    for period in metrics_core_module.PERIODS:
        is_default = period.id == metrics_core_module.DEFAULT_PERIOD_ID
        active = " active" if is_default else ""
        pressed = "true" if is_default else "false"
        chips.append(
            f'<button type="button" class="period-chip{active}" aria-pressed="{pressed}" '
            f'data-period="{period.id}" onclick="selectPeriod(\'{period.id}\')">'
            f"{html.escape(metrics_core_module.period_label(period.id, lang=lang))}</button>"
        )
    return f'<div class="period-filter">{"".join(chips)}{render_backfill_note(lang=lang)}</div>'


def render_weekly_meter(view: dict[str, Any]) -> str:
    """The weekly-limit panel. `view` is metrics_core.describe_weekly_meter's
    dict: the TUI renders the same one, so the numbers cannot differ."""
    heading = f'<h2 id="weekly-meter-heading">{html.escape(view["heading"])}</h2>'
    caveat = f'<p class="meter-caveat">{html.escape(view["caveat"])}</p>'
    opening = '<section class="weekly-meter" aria-labelledby="weekly-meter-heading">'
    if view["empty"]:
        status = f'<p class="meter-status">{html.escape(view["status"])}</p>'
        return f"{opening}{heading}{status}{caveat}</section>"
    band = ""
    if view["range_start_percent"] is not None:
        width = view["range_end_percent"] - view["range_start_percent"]
        band = (
            f'<div class="meter-range-band" style="left:{view["range_start_percent"]}%;'
            f'width:{width:.1f}%"></div>'
        )
    value_text = f"{view['value_text']}. {view['status']}"
    track = (
        f'<div class="meter-track" role="meter" '
        f'aria-label="{html.escape(view["name"], quote=True)}" '
        f'aria-valuemin="0" aria-valuemax="{view["scale_maximum"]}" '
        f'aria-valuenow="{view["value"]}" '
        f'aria-valuetext="{html.escape(value_text, quote=True)}">'
        f'<div class="meter-fill" style="width:{view["fill_percent"]}%"></div>{band}</div>'
    )
    range_line = f'<p class="meter-range">{html.escape(view["range"])}</p>' if view["range"] else ""
    lines = (
        f'<p class="meter-value"><b>{html.escape(view["value_text"])}</b> '
        f"{html.escape(view['tiers'])}</p>"
        f'<p class="meter-status meter-{view["state"]}">{html.escape(view["status"])}</p>'
        f"{range_line}"
        f'<p class="meter-five-hour">{html.escape(view["five_hour"])}</p>'
    )
    return f"{opening}{heading}{track}{lines}{caveat}{_render_meter_table(view)}</section>"


def _render_meter_table(view: dict[str, Any]) -> str:
    lang = view["lang"]
    columns = ("window", "accumulated", "tiers", "outcome")
    head = "".join(
        f'<th scope="col">{html.escape(metrics_core_module.t(f"meter.col.{column}", lang))}</th>'
        for column in columns
    )
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(row[column])}</td>" for column in columns) + "</tr>"
        for row in view["rows"]
    )
    caption = html.escape(metrics_core_module.t("meter.table_caption", lang))
    return (
        f'<table class="meter-table"><caption>{caption}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    )


def _render_table(caption: str, columns: list[tuple[str, str]], rows: list[dict[str, str]]) -> str:
    """A captioned table; `columns` is (header text, row key) pairs."""
    head = "".join(f'<th scope="col">{html.escape(header)}</th>' for header, _ in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(row[key])}</td>" for _, key in columns) + "</tr>"
        for row in rows
    )
    return (
        f'<div class="table-scroll" role="region" aria-label="{html.escape(caption)}" tabindex="0">'
        f'<table class="meter-table"><caption>{html.escape(caption)}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def render_cache_causes(view: dict[str, Any]) -> str:
    """Where the cache creation came from. `view` is
    metrics_core.describe_cache_causes's dict, which the TUI renders too."""
    if view["empty"]:
        return ""
    lang = view["lang"]

    def label(key: str) -> str:
        return str(metrics_core_module.t(f"causes.col.{key}", lang))

    alert = f'<p class="causes-alert">⚠ {html.escape(view["alert"])}</p>' if view["alert"] else ""
    jump = (
        f'<p class="causes-alert">⚠ {html.escape(view["first_jump"])}</p>'
        if view["first_jump"]
        else ""
    )
    windows = (
        ""
        if not view["windows"]
        else _render_table(
            str(metrics_core_module.t("causes.table_caption", lang)),
            [
                (label(key), key)
                for key in (
                    "window",
                    "total",
                    "cold_start",
                    "rewrite",
                    "incremental",
                    "after_idle",
                    "main_short",
                    "subagent_short",
                )
            ],
            view["windows"],
        )
    )
    sizes = _render_table(
        str(metrics_core_module.t("causes.sizes_caption", lang)),
        [
            (label("size"), "size"),
            (label("runs"), "runs"),
            (label("creation"), "creation"),
            (label("rewrite_share"), "rewrite"),
        ],
        view["sizes"],
    )
    first = (
        _render_table(
            str(metrics_core_module.t("causes.first_caption", lang)),
            [
                (label("version"), "version"),
                (label("conversations"), "conversations"),
                (label("median"), "median"),
            ],
            view["first_requests"],
        )
        if view["first_requests"]
        else ""
    )
    by_project = (
        _render_table(
            str(metrics_core_module.t("causes.by_project_caption", lang)),
            [
                (label("project"), "project"),
                (label("conversations"), "conversations"),
                (label("median"), "median"),
            ],
            view["first_by_project"],
        )
        if view["first_by_project"]
        else ""
    )
    idle = (
        _render_table(
            str(metrics_core_module.t("causes.idle_caption", lang)),
            [
                (label("session"), "session"),
                (label("idle_tokens"), "tokens"),
                (label("resumes"), "resumes"),
            ],
            view["idle_sessions"],
        )
        if view["idle_sessions"]
        else ""
    )
    return (
        '<section class="weekly-meter cache-causes" aria-labelledby="cache-causes-heading">'
        f'<h2 id="cache-causes-heading">{html.escape(view["heading"])}</h2>'
        f'<p class="meter-caveat">{html.escape(view["note"])}</p>'
        f"{alert}{windows}{sizes}{first}{by_project}{idle}{jump}</section>"
    )


def render_branches(view: dict[str, Any], period_id: str) -> str:
    """Tokens by branch inside each project. `view` is
    metrics_core.describe_branches's dict, which the TUI renders too; the
    heading id carries the period because the page holds one copy per period."""
    if view["empty"]:
        return ""
    lang = view["lang"]
    columns = [(label, key) for key, label in view["columns"].items()]
    tables = "".join(
        _render_table(
            str(metrics_core_module.t("branches.caption", lang, project=project["project"])),
            columns,
            project["rows"],
        )
        for project in view["projects"]
    )
    heading_id = f"branches-heading-{period_id}"
    return (
        f'<section class="weekly-meter branches" aria-labelledby="{heading_id}">'
        f'<h2 id="{heading_id}">{html.escape(view["heading"])}</h2>'
        f'<p class="meter-caveat">{html.escape(view["note"])}</p>'
        f"{tables}</section>"
    )


def render_html(
    records: list[dict[str, Any]],
    subagents_by_session: dict[Any, list[dict[str, Any]]],
    chart_records: list[dict[str, Any]],
    daily_chart_records: list[dict[str, Any]],
    period_windows: dict[str, tuple[datetime, datetime] | None],
    project_first_seen: dict[str, str],
    project_last_seen: dict[str, str],
    open_projects: set[str],
    now: datetime,
    *,
    lang: str,
    weekly_meter: dict[str, Any] | None = None,
) -> str:
    group_colors = build_group_colors(chart_records, daily_chart_records, now=now)
    # Over the whole history, not per period, so a model keeps its colour
    # in every period's chart - see metrics_core_module.assign_model_slots.
    # Ordered by first appearance, not compute_model_totals' dict order (not
    # guaranteed chronological across sessions - see
    # order_models_by_first_appearance) - that order is what makes a
    # model's slot permanent as the history grows.
    model_slots = metrics_core_module.assign_model_slots(
        metrics_core_module.order_models_by_first_appearance(daily_chart_records),
        MODEL_COLOR_SLOTS,
    )

    def periods_for(record: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            period.id
            for period in metrics_core_module.PERIODS
            if metrics_core_module.filter_sessions_by_window([record], period_windows[period.id])
        )

    groups = metrics_core_module.build_project_groups(records, group_colors)
    has_tmux_data = any(record.get("tmux_window") for record in records)

    # Alberto: omit zero-token sections entirely (2026-07-20) - a
    # session that never recorded any input/output tokens (aborted
    # before the first turn, etc.) adds a row with nothing useful in
    # it. Filter before deciding whether to render a group header at
    # all, so a group whose every session was zero-token doesn't leave
    # a header with no rows under it.
    visible_groups = []
    for group_index, group in enumerate(groups):
        visible_records = [
            record
            for record in group["records"]
            if metrics_core_module.record_token_total(record) > 0
        ]
        if visible_records:
            visible_groups.append((group_index, {**group, "records": visible_records}))

    # Distinguish sessions that got the SAME AI-generated task_label
    # (2026-07-21) - the labeler can independently produce an identical
    # short summary for two genuinely different sessions, and with
    # nothing else visible in that column they read as duplicates.
    # Numbering only kicks in for a label actually shared by 2+ distinct
    # session_ids, in first-seen order across the whole table.
    label_counts: dict[str, int] = {}
    for _, group in visible_groups:
        for record in group["records"]:
            label = record.get("task_label")
            if label:
                label_counts[label] = label_counts.get(label, 0) + 1
    label_seen: dict[str, int] = {}

    rows_html = []
    panel_sources: dict[str, str] = {}
    for group_index, group in visible_groups:
        rows_html.append(render_group_header(group_index, group, period_windows, lang=lang))
        for record in group["records"]:
            session_id = record.get("session_id") or ""
            label = record.get("task_label")
            if label and label_counts.get(label, 0) > 1:
                label_seen[label] = label_seen.get(label, 0) + 1
                record = {**record, "task_label": f"{label} ({label_seen[label]})"}
            session_subagents = subagents_by_session.get(session_id, [])
            visible_subagent_count = sum(
                1
                for subagent in session_subagents
                if metrics_core_module.record_token_total(subagent) > 0
            )
            rows_html.append(
                render_session_row(
                    record,
                    False,
                    group_colors,
                    group_index,
                    visible_subagent_count,
                    show_tmux=has_tmux_data,
                    period_ids=periods_for(record),
                    controls_panel=False,
                    lang=lang,
                )
            )
            # Only a session with subagents has a panel worth pre-rendering;
            # every other one is cloned from a shared template on first open.
            if visible_subagent_count:
                panel_sources[session_id] = render_detail_panel(
                    session_id,
                    session_subagents,
                    False,
                    period_ids=periods_for(record),
                    lang=lang,
                )

    # INDEPENDENT sets of period blocks, not one combined set - ruling from
    # fix-round-1 review. The trend chart stays at its original
    # always-visible sticky spot and the Panorama chart stays inside its
    # own tab pane, exactly as they were laid out before this phase; only
    # each one's own content is now scoped per period and swapped by
    # `hidden`. Merging them would have been a two-panel layout
    # restructure, which is explicitly a later phase, smuggled into a
    # filtering task.
    #
    # There are THREE such div sets now - the composition legend below
    # became the third - and each group header additionally carries one
    # `group-count period-block` span per period. So the page holds
    # 3 x 5 = 15 `.period-block` divs, plus one span per group per period:
    # measured on the test fixture's own generated page (2026-08-14), 15
    # divs and 35 spans over 7 groups. This comment used to read "10
    # `.period-block` elements total (5 + 5), not 5" - true when written,
    # wrong through both changes since and never updated, so trust the
    # shape stated here over any count you remember.
    #
    # Every set carries the same `data-period` values and the same
    # `.period-block` class, so selectPeriod()'s single
    # querySelectorAll('.period-block') toggles all of them together - the
    # surfaces still cannot disagree about the period.
    trend_blocks = []
    panorama_blocks = []
    # The composition legend is the Sessions tab's own total. It used to be
    # rendered once from the unfiltered records and left outside every
    # period block, so it reported the all-time total under every period -
    # the loudest possible way for two surfaces to disagree. It is small, so
    # one pre-rendered copy per period is the cheapest fix (2026-08-13).
    legend_blocks = []
    branch_blocks = []
    for period in metrics_core_module.PERIODS:
        window = period_windows[period.id]
        scoped_chart = metrics_core_module.filter_sessions_by_window(chart_records, window)
        scoped_daily = metrics_core_module.filter_deltas_by_window(daily_chart_records, window)
        # A real boolean HTML attribute, not a class token - `hidden` has
        # to sit outside `class="..."` or neither the .period-block[hidden]
        # CSS rule nor the DOM's own `.hidden` IDL property (which
        # selectPeriod's JS reads/writes) ever sees it, and every period's
        # chart stays stacked visible on first load instead of only
        # "tudo" (caught by executing this markup in jsdom, not by reading
        # it - the earlier `class="period-block hidden"` form parses fine
        # as HTML, it just isn't the `hidden` attribute).
        hidden_attribute = "" if period.id == metrics_core_module.DEFAULT_PERIOD_ID else " hidden"
        trend_blocks.append(
            f'<div class="period-block" data-period="{period.id}"{hidden_attribute}>'
            + render_trend_chart(
                scoped_daily, group_colors, period.id, now=now, lang=lang, model_slots=model_slots
            )
            + "</div>"
        )
        panorama_blocks.append(
            f'<div class="period-block" data-period="{period.id}"{hidden_attribute}>'
            + render_project_totals_chart(
                scoped_chart,
                project_first_seen,
                project_last_seen,
                open_projects,
                period.id,
                lang=lang,
            )
            + "</div>"
        )
        branches_html = render_branches(
            metrics_core_module.describe_branches(
                metrics_core_module.compute_branch_breakdown(
                    metrics_core_module.filter_sessions_by_window(records, window)
                ),
                lang,
            ),
            period.id,
        )
        if branches_html:
            branch_blocks.append(
                f'<div class="period-block" data-period="{period.id}"{hidden_attribute}>'
                f"{branches_html}</div>"
            )
        legend_blocks.append(
            f'<div class="period-block" data-period="{period.id}"{hidden_attribute}>'
            + render_composition_legend(
                metrics_core_module.compute_token_type_totals(
                    metrics_core_module.filter_sessions_by_window(records, window)
                ),
                lang=lang,
            )
            + "</div>"
        )
    trend_blocks_html = "".join(trend_blocks)
    panorama_blocks_html = "".join(panorama_blocks)
    legend_blocks_html = "".join(legend_blocks)

    tabs_html = (
        '<div class="tab-bar" role="tablist">'
        '<span class="tab-btn active" id="tab-btn-sessions" '
        'role="tab" tabindex="0" aria-selected="true" aria-controls="tab-sessions" '
        f"onclick=\"showTab('sessions')\">{metrics_core_module.t('tab.sessions', lang)}</span>"
        '<span class="tab-btn" id="tab-btn-panorama" '
        'role="tab" tabindex="-1" aria-selected="false" aria-controls="tab-panorama" '
        f"onclick=\"showTab('panorama')\">{metrics_core_module.t('tab.panorama', lang)}</span>"
        "</div>"
    )
    causes_view = (weekly_meter or {}).get("causes")
    sessions_tab_html = (
        '<div class="tab-pane active" id="tab-sessions" role="tabpanel" '
        'aria-labelledby="tab-btn-sessions">'
        f"{render_weekly_meter(weekly_meter) if weekly_meter else ''}"
        f"{render_cache_causes(causes_view) if causes_view else ''}"
        f"{''.join(branch_blocks)}"
        '<div class="main-row">'
        f'<div class="session-list">{legend_blocks_html}'
        f"{render_session_header(has_tmux_data, lang=lang)}{''.join(rows_html)}</div>"
        '<div class="detail-container">'
        f'<p class="detail-hint">{metrics_core_module.t("detail.hint", lang)}</p>'
        f"{render_detail_source(panel_sources)}{render_empty_panel_template(lang=lang)}</div>"
        "</div>"
        "</div>"
    )
    panorama_tab_html = (
        '<div class="tab-pane" id="tab-panorama" role="tabpanel" '
        f'aria-labelledby="tab-btn-panorama">{panorama_blocks_html}</div>'
    )
    # Opens with every token type AND every project color already
    # selected (2026-07-21: "tanto lá quanto na legenda... precisa
    # começar com todos selecionados") - the dashboard shows everything
    # unfiltered by default, so both legends should reflect that instead
    # of opening in an unhighlighted state that reads as "nothing
    # selected." Both are safe to combine in one data-highlight value:
    # series-N and group-color-N never touch the same elements, and only
    # one trend-mode pane is visible at a time anyway.
    default_highlight = " ".join(
        ["series-1", "series-2", "series-3", "series-4", *sorted(set(group_colors.values()))]
    )
    html_lang = "pt-BR" if lang == "pt" else "en"
    page_title = html.escape(metrics_core_module.t("page_title", lang))
    # "{name}" stays a placeholder: the page's script fills in the period.
    period_announcement = html.escape(
        metrics_core_module.t("notify.period", lang, name="{name}"), quote=True
    )
    return (
        f"<!DOCTYPE html><html lang=\"{html_lang}\"><head><meta charset='utf-8'>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{page_title}</title>{STYLE}</head>"
        f'<body data-highlight="{default_highlight}">'
        # A heading and a main region for screen readers, and a polite live
        # region selectPeriod fills, so a period change is announced
        # (2026-10-05 review). The period filter and the trend chart stay
        # outside <main>: their sticky positioning is keyed on being
        # children of <body> (see .period-block in the stylesheet).
        f'<h1 class="visually-hidden">{page_title}</h1>'
        f'<div class="visually-hidden" aria-live="polite" '
        f'data-template="{period_announcement}">'
        "</div>"
        f"{render_period_filter(lang=lang)}"
        f"{trend_blocks_html}"
        "<main>"
        f"{tabs_html}"
        f"{sessions_tab_html}"
        f"{panorama_tab_html}"
        "</main>"
        f"{SCRIPT}"
        "</body></html>"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Report on logged Claude Code session metrics.")
    parser.add_argument("--tag", default=None, help="Filter by session tag")
    parser.add_argument("--project", default=None, help="Filter by project path")
    parser.add_argument(
        "-n", "--limit", type=int, default=20, help="Number of most recent sessions to show"
    )
    parser.add_argument(
        "--label-budget",
        type=int,
        default=0,
        help=(
            "Max NEW labels (real claude -p calls) to generate in this run; "
            "already-cached labels are free and unlimited. Use 0 for none, "
            "a negative number for unlimited."
        ),
    )
    parser.add_argument(
        "--html", action="store_true", help="Generate an HTML dashboard instead of printing a table"
    )
    parser.add_argument(
        "--days", type=int, default=None, help="Only include sessions from the last N days"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Accepted for compatibility and now the default: the full "
            "history is shown unless --days is given"
        ),
    )
    arguments = parser.parse_args()

    records = metrics_core_module.load_records(metrics_core_module.METRICS_FILE)
    # Read once (backlog #11: this used to also call metrics_core_module.load_project_roots(),
    # a second read of the same file) and derive everything the config can
    # give from this one dict.
    config = metrics_core_module.load_config()
    # Declared in ~/.claude/metrics/config.json; empty when there is no
    # config, which leaves normalize_project_paths exactly as it was.
    project_roots = metrics_core_module.project_roots_from(config)
    # Config first, then the machine's locale, then English - see
    # resolve_language's own docstring. Resolved once, here, and threaded
    # to every renderer explicitly rather than re-read per call.
    lang = metrics_core_module.resolve_language(config, os.environ)
    # Captured before dedupe_sessions discards every snapshot but one per
    # session_id - expand_records_to_daily_deltas needs the FULL raw
    # history to correctly spread a long-running session's token growth
    # across the days it actually happened (2026-08-06 fix). Normalized
    # and tag/project-filtered independently so it stays consistent with
    # the main `records` pipeline below, without depending on dedup ever
    # having run.
    raw_session_records = metrics_core_module.normalize_project_paths(list(records), project_roots)
    raw_session_records = metrics_core_module.filter_records(
        raw_session_records, tag=arguments.tag, project=arguments.project
    )
    records = metrics_core_module.dedupe_sessions(records)
    records = metrics_core_module.normalize_project_paths(records, project_roots)
    records = metrics_core_module.filter_records(
        records, tag=arguments.tag, project=arguments.project
    )
    all_time_records = records

    # The period is the single authority on time (2026-08-13). The --html
    # dashboard used to also apply a default 60-day window here, on top of
    # the period filtering, using filter_by_days - which keys on
    # timestamp_start, the exact start-date rule this phase replaced with
    # span overlap. It happened to drop zero records on today's data, but a
    # cap that silently starts biting later is the same defect waiting: it
    # would make "tudo" quietly mean "tudo, up to 60 days". --days stays as
    # an explicit user override, and --all is still accepted (it now names
    # what already happens by default) so existing invocations keep working.
    effective_days = arguments.days

    # The chart always covers the full history (2026-07-21: "por que não
    # cobrir mais datas?") - only the session-list table is bounded by
    # --days/-n. chart_records is all_time_records (already has
    # --tag/--project applied, just not the days window), independent of
    # whatever the table's window/limit end up being below.
    chart_records = all_time_records
    records = metrics_core_module.filter_by_days(records, effective_days)
    # The --html session-list table is unbounded by -n (2026-07-21:
    # "sem limite (mostra todas)") - only the terminal-table mode still
    # defaults to the last 20, unchanged.
    if not arguments.html:
        records = records[-arguments.limit :]

    if arguments.html:
        # Subagent paths go through the same normalization as session paths
        # (2026-09-21). They did not, until a legend entry turned out to be a
        # subagent's literal working directory - subagent deltas feed the trend
        # chart below, so their raw cwds reached the legend unchanged. Run over
        # the subagent stream on its own rather than over a merged list, the
        # same way the raw/deduped session streams are normalized separately.
        raw_subagent_records = metrics_core_module.normalize_project_paths(
            metrics_core_module.load_records(metrics_core_module.SUBAGENTS_FILE), project_roots
        )
        subagent_records = metrics_core_module.dedupe_subagents(raw_subagent_records)
        # See expand_records_to_daily_deltas's docstring: raw (pre-dedup)
        # session + subagent snapshots, delta-attributed to the day each
        # was actually recorded - used ONLY by the trend chart below, so
        # every other view (legend, project rankings, per-row totals)
        # keeps using the regular deduped/folded records untouched.
        daily_chart_records = metrics_core_module.expand_records_to_daily_deltas(
            raw_session_records
        ) + metrics_core_module.expand_records_to_daily_deltas(raw_subagent_records)

        # One clock reading for the whole render. Calling now() per
        # surface could straddle midnight and put two panels in
        # different days.
        render_now = datetime.now().astimezone()
        period_windows = {
            period.id: metrics_core_module.resolve_period(period.id, render_now)
            for period in metrics_core_module.PERIODS
        }
        agent_label_cache = metrics_core_module.load_agent_label_cache()
        # Real data has duplicate agent_id/session_id records (the metrics
        # log has no dedup). Deduping to one representative per key before
        # fanning out to the thread pool guarantees each key is processed
        # by exactly one thread - without this, two threads racing on the
        # same key could interleave a real label with a failed one, and
        # negative-caching (get_agent_label/get_task_label above) would
        # then permanently lock in whichever thread wrote last, even if it
        # was the failure. Deduping also avoids redundant subprocess calls
        # for the same underlying record.
        #
        # The representative must be a SOURCE-BEARING duplicate when one
        # exists, not just the first one seen: real data has sessions where
        # the first-seen duplicate lacks internal_name/transcript_path
        # (found during final review) - picking it as representative would
        # generate no label, negative-cache the failure, and silently lose
        # a label another duplicate of the same session could have
        # produced. Falls back to first-seen only when no duplicate has a
        # usable source.
        #
        # DEAD BY DRIFT (found 2026-08-31, coverage-phase3c Task 4, confirmed
        # by mutation testing): the `existing is not None` arm below cannot
        # execute today. 77f49d5 (2026-07-18) added this source-bearing-
        # duplicate preference when subagent_records had NO dedup upstream -
        # correct at the time. 328d5d6 (2026-07-21), three days later, added
        # dedupe_subagents UPSTREAM of this exact loop, which already
        # guarantees one record per agent_id before it runs - so `existing`
        # is always None the one time each key is seen. Kept, not deleted:
        # the clause becomes necessary again the moment the upstream dedup
        # is removed or reordered, and deleting it now would plant that
        # regression back silently. See docs/superpowers/BACKLOG.md.
        #
        # `no branch`, NOT `no cover`: what is unreachable is the false arc
        # of this `if`, not the statement. `no cover` on an `if` line
        # excludes the whole statement including its body, which would drop
        # the always-executed `unique_agents[key] = subagent` out of the
        # denominator and hide it from the phase-4 100% gate forever.
        unique_agents: dict[Any, dict[str, Any]] = {}
        for subagent in subagent_records:
            key = subagent.get("agent_id")
            if key is None:
                continue
            existing = unique_agents.get(key)
            if existing is None or (  # pragma: no branch - unreachable, see comment above
                not existing.get("agent_transcript_path") and subagent.get("agent_transcript_path")
            ):
                unique_agents[key] = subagent
        # max_workers=1 (fully serial): each worker spawns a full
        # `claude -p ... --model haiku` CLI subprocess (a whole Node
        # process), not a lightweight thread task. Even 3 concurrent
        # subprocesses was still enough to spike load average past 25 and
        # keep growing swap on an 8GB machine during a cold-cache backfill
        # (see PROJECT-LOG 2026-07-20) - serial + `nice` in
        # compress_to_label trades throughput for never fighting the
        # machine, which is the explicit tradeoff wanted for a
        # once-per-history backfill. The cache is also saved every
        # SAVE_EVERY completions, not just at the end - a killed/interrupted
        # run keeps whatever it already labeled instead of repeating the
        # whole cold-cache scan next time.
        SAVE_EVERY = 10
        # One budget shared across BOTH the agent-label and session-label
        # passes below (2026-07-22: "restringir a algo como 100
        # rotulagens por seção") - a single run now generates at most
        # this many brand-new labels total, then stops calling the model
        # (already-cached labels are free/instant and don't count).
        label_budget = metrics_core_module.LabelBudget(
            None if arguments.label_budget < 0 else arguments.label_budget
        )
        agent_keys = list(unique_agents.keys())
        label_by_agent_id = {}
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {
                executor.submit(
                    metrics_core_module.get_agent_label,
                    unique_agents[agent_key],
                    agent_label_cache,
                    label_budget,
                    lang=lang,
                ): agent_key
                for agent_key in agent_keys
            }
            for completed_count, future in enumerate(as_completed(futures), 1):
                agent_key = futures[future]
                label_by_agent_id[agent_key] = future.result()
                if completed_count % SAVE_EVERY == 0:
                    metrics_core_module.save_agent_label_cache(agent_label_cache)
        for subagent in subagent_records:
            subagent["agent_label"] = label_by_agent_id.get(subagent.get("agent_id"))
        metrics_core_module.save_agent_label_cache(agent_label_cache)
        subagents_by_session = metrics_core_module.group_subagents_by_session(subagent_records)
        # Mutates the same dict objects all_time_records/chart_records/records
        # already share (none of the load/dedupe/filter steps above copy the
        # records, only filter the list) - so every aggregate view downstream
        # picks up subagent tokens automatically, without touching each one
        # individually. See fold_subagent_tokens_into_sessions's own
        # docstring for why this was missing (2026-08-05).
        metrics_core_module.fold_subagent_tokens_into_sessions(all_time_records, subagent_records)

        label_cache = metrics_core_module.load_label_cache()
        # Same source-bearing-duplicate preference, same dead-by-drift arm as
        # unique_agents above: 77f49d5 (2026-07-18) added it when `records`
        # had no dedup upstream; 328d5d6 (2026-07-21) added dedupe_sessions
        # upstream of this loop three days later, guaranteeing one record
        # per session_id before it runs - so `existing` is always None here
        # too. Kept for the same reason (a future upstream reorder would
        # need it back). `no branch` rather than `no cover` for the same
        # reason as above: the body assignment runs every iteration and
        # must stay measured. See docs/superpowers/BACKLOG.md.
        unique_sessions: dict[Any, dict[str, Any]] = {}
        for record in records:
            key = record.get("session_id")
            if key is None:
                continue
            existing = unique_sessions.get(key)
            if existing is None or (  # pragma: no branch - unreachable, see comment above
                not (existing.get("internal_name") or existing.get("transcript_path"))
                and (record.get("internal_name") or record.get("transcript_path"))
            ):
                unique_sessions[key] = record
        session_keys = list(unique_sessions.keys())
        label_by_session_id = {}
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {
                executor.submit(
                    metrics_core_module.get_task_label,
                    unique_sessions[session_key],
                    label_cache,
                    label_budget,
                    lang=lang,
                ): session_key
                for session_key in session_keys
            }
            for completed_count, future in enumerate(as_completed(futures), 1):
                session_key = futures[future]
                label_by_session_id[session_key] = future.result()
                if completed_count % SAVE_EVERY == 0:
                    metrics_core_module.save_label_cache(label_cache)
        for record in records:
            record["task_label"] = label_by_session_id.get(record.get("session_id"))
        metrics_core_module.save_label_cache(label_cache)
        project_first_seen = metrics_core_module.compute_project_first_seen(all_time_records)
        project_last_seen = metrics_core_module.compute_project_last_seen(all_time_records)
        open_projects = metrics_core_module.get_open_projects()
        html_output = render_html(
            records,
            subagents_by_session,
            chart_records,
            daily_chart_records,
            period_windows,
            project_first_seen,
            project_last_seen,
            open_projects,
            render_now,
            lang=lang,
            weekly_meter=metrics_core_module.load_weekly_meter_view(
                Path(metrics_core_module.METRICS_FILE).parent, lang, render_now
            ),
        )
        output_path = Path("~/.claude/metrics/dashboard.html").expanduser()
        with output_path.open("w") as output_file:
            output_file.write(html_output)
        print(output_path)
        return

    subagent_records = metrics_core_module.dedupe_subagents(
        metrics_core_module.normalize_project_paths(
            metrics_core_module.load_records(metrics_core_module.SUBAGENTS_FILE), project_roots
        )
    )
    metrics_core_module.fold_subagent_tokens_into_sessions(records, subagent_records)
    print(format_table(records))
    print(format_summary(records))


if __name__ == "__main__":  # pragma: no cover - true only when run as a script, not under pytest
    main()
