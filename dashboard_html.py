#!/usr/bin/env python3
import argparse
import html
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import metrics_core as mr

from dashboard_html_assets import SCRIPT, STYLE


def format_table(records: list[dict[str, Any]]) -> str:
    header = (
        f"{'session_id':<12} {'tag':<10} {'branch':<12} {'dur(s)':>8} "
        f"{'in_tok':>8} {'out_tok':>8} {'turns':>6} {'tools':>6}"
    )
    lines = [header, "-" * len(header)]
    for r in records:
        session_id = (r.get("session_id") or "")[:12]
        tag = (r.get("tag") or "-")[:10]
        branch = (r.get("git_branch") or "-")[:12]
        duration = r.get("duration_seconds")
        duration_str = f"{duration:.0f}" if duration is not None else "-"
        lines.append(
            f"{session_id:<12} {tag:<10} {branch:<12} {duration_str:>8} "
            f"{r.get('input_tokens', 0):>8} {r.get('output_tokens', 0):>8} "
            f"{r.get('turn_count', 0):>6} {r.get('tool_call_count', 0):>6}"
        )
    return "\n".join(lines)


def format_summary(records: list[dict[str, Any]]) -> str:
    if not records:
        return "No sessions matched."
    n = len(records)
    total_input = sum(r.get("input_tokens", 0) for r in records)
    total_output = sum(r.get("output_tokens", 0) for r in records)
    total_turns = sum(r.get("turn_count", 0) for r in records)
    durations = [r["duration_seconds"] for r in records if r.get("duration_seconds") is not None]
    avg_duration = sum(durations) / len(durations) if durations else 0
    return (
        f"\n{n} session(s) - avg duration {avg_duration:.0f}s, "
        f"total input tokens {total_input}, total output tokens {total_output}, "
        f"avg turns {total_turns / n:.1f}"
    )


SERIES_NAMES = {
    "series-1": "input",
    "series-2": "output",
    "series-3": "cache read",
    "series-4": "cache creation",
}


def render_composition_legend(totals: dict[str, int]) -> str:
    chips = []
    for css_class, name in SERIES_NAMES.items():
        chips.append(
            f'<span class="legend-chip" data-series="{css_class}" '
            f"onclick=\"highlightTokenType('{css_class}', event)\">"
            f'<span class="legend-swatch {css_class}"></span>{name} · '
            f'<span class="legend-total" data-series="{css_class}">'
            f"{mr.format_tokens(totals.get(css_class, 0))}</span></span>"
        )
    return '<div class="comp-legend">' + "".join(chips) + "</div>"


def render_composition_bar(record: dict[str, Any]) -> str:
    segments = [
        ("series-1", record.get("input_tokens", 0) or 0),
        ("series-2", record.get("output_tokens", 0) or 0),
        ("series-3", record.get("cache_read_tokens", 0) or 0),
        ("series-4", record.get("cache_creation_tokens", 0) or 0),
    ]
    total = sum(v for _, v in segments)
    if total <= 0:
        return '<div class="comp-bar comp-bar-empty"></div>'
    parts = []
    for css_class, value in segments:
        if value <= 0:
            continue
        pct = value / total * 100
        name = SERIES_NAMES[css_class]
        parts.append(
            f'<span class="comp-seg {css_class}" style="width:{pct:.2f}%" '
            f'title="{name} · {mr.format_tokens(value)} ({pct:.0f}%)" '
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
    date_str = mr.format_date(record.get("timestamp_start"))
    duration_str = mr.format_duration(record.get("duration_seconds"))
    total_tokens = mr.format_tokens(mr.record_token_total(record))
    active_class = " active" if is_active else ""
    group_class = group_colors.get(record.get("project_path"), "group-color-none")
    # Compact "has subagents" indicator (2026-07-21) - a count badge here
    # answers "does this session have subagents worth checking" without
    # opening every single row's detail panel to find out.
    subagent_badge = f"🤖{subagent_count}" if subagent_count else ""
    tmux_span = f'<span class="tmux-window">{tmux_window}</span>' if show_tmux else ""
    # Row visibility is class-driven ONLY (2026-08-13). This row used to be
    # born with an inline style="display:none" that toggleGroup then
    # overwrote with an inline display:flex. An inline declaration beats any
    # non-!important author rule, so the period filter's .period-hidden rule
    # could never win: an expanded row stayed on screen under every period.
    # Collapse state and period membership are both plain classes now, so the
    # two mechanisms cannot contradict each other - the row shows only when
    # it is neither collapsed nor outside the active period.
    return (
        f'<div class="session-row {group_class}{active_class} collapsed" id="row-{session_id}" '
        f'data-group="{group_index}" '
        f'data-periods="{" ".join(period_ids)}" '
        f"onclick=\"showSession('{session_id}')\">"
        f'<span class="sid">{session_id[:12]}</span>'
        f'<span class="subagent-col">{subagent_badge}</span>'
        f'<span class="date-col">{date_str}</span>'
        f"{tmux_span}"
        f'<span class="label-col">{task_label}</span>'
        f'<span class="tag-col">{tag}</span><span class="branch-col">{branch}</span>'
        f'<span class="tokens-total">{total_tokens}</span>'
        f"{render_composition_bar(record)}"
        f'<span class="duration-col">{duration_str}</span>'
        f"</div>"
    )


def render_group_header(
    group_index: int,
    group: dict[str, Any],
    period_windows: dict[str, tuple[datetime, datetime] | None],
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
    label = mr.project_display_label(project_path)
    title_attr = f' title="{html.escape(project_path)}"' if project_path else ""
    member_periods = []
    counts = []
    for period in mr.PERIODS:
        scoped = mr.filter_sessions_by_window(group["records"], period_windows[period.id])
        if scoped:
            member_periods.append(period.id)
        total_tokens = mr.format_tokens(sum(mr.record_token_total(r) for r in scoped))
        hidden_attr = "" if period.id == mr.DEFAULT_PERIOD_ID else " hidden"
        counts.append(
            f'<span class="group-count period-block" data-period="{period.id}"{hidden_attr}>'
            f"({len(scoped)} sessões · {total_tokens} tokens)</span>"
        )
    return (
        f'<div class="group-header collapsed {group["class"]}" '
        f'id="group-{group_index}"{title_attr} '
        f'data-periods="{" ".join(member_periods)}" '
        f'onclick="toggleGroup({group_index})">'
        '<span class="group-icon">▸</span>'
        f"<span>{html.escape(label)} {''.join(counts)}</span>"
        "</div>"
    )


def render_session_header(show_tmux: bool = True) -> str:
    tmux_span = '<span class="tmux-window">aba</span>' if show_tmux else ""
    return (
        '<div class="session-row session-header">'
        '<span class="sid">id</span>'
        '<span class="subagent-col">🤖</span>'
        '<span class="date-col">data</span>'
        f"{tmux_span}"
        '<span class="label-col">tarefa</span>'
        '<span class="tag-col">tag</span>'
        '<span class="branch-col">branch</span>'
        '<span class="tokens-total">tokens</span>'
        '<span class="comp-header">composição</span>'
        '<span class="duration-col">duração</span>'
        "</div>"
    )


def render_detail_panel(
    session_id: str,
    subagents: list[dict[str, Any]],
    is_active: bool,
    period_ids: tuple[str, ...] = ("tudo",),
) -> str:
    open_class = " open" if is_active else ""
    title = ""
    subagents = [s for s in subagents if mr.record_token_total(s) > 0]
    if not subagents:
        body = '<p class="empty">Nenhum subagente nesta sessão.</p>'
    else:
        ordered = sorted(subagents, key=lambda s: s.get("timestamp_start") or "", reverse=True)
        title = f'<h4 class="subagent-title">Subagentes despachados ({len(ordered)})</h4>'
        header = (
            '<div class="subagent-row subagent-header">'
            "<span>data</span><span>tarefa</span><span>tokens</span>"
            "<span>tools</span><span>duração</span>"
            "</div>"
        )
        rows = [header]
        for i, subagent in enumerate(ordered):
            duration_str = mr.format_duration(subagent.get("duration_seconds"))
            agent_type = html.escape(subagent.get("agent_type") or "-")
            agent_label = subagent.get("agent_label")
            if agent_label:
                task_text = f"{agent_type} — {html.escape(agent_label)}"
            else:
                task_text = f"{agent_type} (sem transcript salvo)"
            date_str = mr.format_date(subagent.get("timestamp_start"))
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
            subagent_total_tokens = mr.format_tokens(mr.record_token_total(subagent))
            zebra_class = " subagent-row-odd" if i % 2 == 1 else ""
            rows.append(
                f'<div class="subagent-row{zebra_class}">'
                f"<span>{date_str}</span>"
                f"<span>{task_text}</span>"
                f'<span class="subagent-tokens-cell">{subagent_total_tokens} {comp_bar}</span>'
                f"<span>{subagent.get('tool_call_count', 0)} tools</span>"
                f"<span>{duration_str}</span>"
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
        'aria-label="Fechar">×</button>'  # noqa: RUF001 - multiplication sign in shipped HTML, not a confusable identifier
        f"<h3>{session_id}</h3>{title}{body}</div>"
    )


def render_trend_chart(
    records: list[dict[str, Any]], group_colors: dict[str | None, str], period_id: str
) -> str:
    granularity, days = mr.aggregate_by_day(records)
    parse_fmt = mr.GRANULARITY_CONFIG[granularity]["parse_fmt"]
    tick_fmt = mr.GRANULARITY_CONFIG[granularity]["tick_fmt"]
    n = len(days)
    if n < 2:
        return ""

    day_totals = [sum(mr.record_token_total(r) for r in day["records"]) for day in days]
    # The two tabs now decompose the SAME quantity - total tokens - one
    # split by project, the other by token type, so they deliberately
    # share one scale: a full-height bar means the same thing on both,
    # and switching tabs re-slices a day rather than rescaling it.
    #
    # This replaces an earlier split-denominator fix. That one was right
    # about its symptom (cache-heavy days starving the "por projeto"
    # bars) but wrong about the cause: "por projeto" was summing only
    # input+output, so it wasn't a scale problem at all - the mode was
    # measuring a different, ~300x smaller quantity than its own axis
    # labels and tooltips claimed (2026-08-10).
    max_pane = max(day_totals) or 1
    max_tokens = max_pane

    height = 150
    padding_left = 45
    padding_right = 10
    padding_top = 10
    padding_bottom = 20
    min_bar_width = 8
    bar_gap = 2

    # Bars represent DAYS now, not sessions, so n is naturally much
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
    candidate_width = max(1200, 40 + n * 48)
    plot_width = candidate_width - padding_left - padding_right
    step = plot_width / n
    bar_width = step * 0.6
    # Confirmed dead (PROJECT-LOG 2026-07-18, round 4): removing the
    # 1000px width cap made bar_width mathematically unreachable below
    # ~28.4px for any n - a deliberate decision with Alberto to keep this
    # fallback anyway rather than delete it. Re-verified by exhaustive
    # sweep during coverage-phase3c Task 3 (2026-08-31): n swept 1..20000
    # (both algebraically and by calling this function directly), global
    # minimum bar_width ~28.44px at n=25, never within reach of the floor.
    if bar_width < min_bar_width:  # pragma: no cover - unreachable, see comment above
        step = min_bar_width + bar_gap
        bar_width = min_bar_width
        plot_width = step * n
        width = plot_width + padding_left + padding_right
        scrollable = True
    else:
        width = candidate_width
        scrollable = False

    plot_height = height - padding_top - padding_bottom

    def y_for(value: float, max_value: float) -> float:
        return padding_top + plot_height - (value / max_value) * plot_height

    def build_y_axis(max_value: float) -> str:
        parts = []
        for frac in (0, 0.25, 0.5, 0.75, 1.0):
            value = max_value * frac
            y = y_for(value, max_value)
            parts.append(
                f'<line x1="{padding_left}" y1="{y:.1f}" x2="{width - padding_right}" '
                f'y2="{y:.1f}" class="chart-gridline" />'
                f'<text x="4" y="{y + 3:.1f}" class="chart-axis-label">'
                f"{mr.format_tokens(value)}</text>"
            )
        return "".join(parts)

    x_axis = []
    tick_count = min(5, n)
    for t in range(tick_count):
        idx = round(t * (n - 1) / (tick_count - 1)) if tick_count > 1 else 0
        x = padding_left + idx * step + bar_width / 2
        bucket_dt = datetime.strptime(days[idx]["day"], parse_fmt)
        tick_date = bucket_dt.strftime(tick_fmt)
        x_axis.append(
            f'<text x="{x:.1f}" y="{height - 4}" class="chart-axis-label" '
            f'text-anchor="middle">{tick_date}</text>'
        )
    x_axis_html = "".join(x_axis)

    # Highlight band for the last 7 buckets (2026-07-21: was "last 7
    # calendar days" specifically, but with hour/month/year granularity
    # now possible too, "last 7 of whatever unit is on screen" is the
    # only version of this that still makes sense across all of them).
    highlight_html = ""
    if n > 7:
        highlight_start_idx = n - 7
        hx = padding_left + highlight_start_idx * step
        hwidth = 7 * step
        highlight_html = (
            f'<rect x="{hx:.1f}" y="{padding_top}" width="{hwidth:.1f}" height="{plot_height}" '
            'class="chart-highlight-band" />'
        )

    def bars_for_pane() -> str:
        bars = []
        for i, day in enumerate(days):
            x = padding_left + i * step + (step - bar_width) / 2
            by_project: dict[str | None, int] = {}
            project_order: list[str | None] = []
            for r in day["records"]:
                project = r.get("project_path")
                if project not in by_project:
                    by_project[project] = 0
                    project_order.append(project)
                by_project[project] += mr.record_token_total(r)
            y_cursor: float = padding_top + plot_height
            for project in project_order:
                value = by_project[project]
                if value <= 0:
                    continue
                seg_height = (value / max_pane) * plot_height if max_pane else 0
                y_cursor -= seg_height
                group_class = group_colors.get(project, "group-color-none")
                project_label = mr.project_display_label(project)
                bars.append(
                    f'<rect x="{x:.1f}" y="{y_cursor:.1f}" '
                    f'width="{bar_width:.1f}" height="{seg_height:.1f}" '
                    f'class="chart-bar {group_class}" '
                    f"onclick=\"highlightProject('{group_class}', event)\">"
                    f"<title>{html.escape(project_label)} · {day['day']} - "
                    f"{mr.format_tokens(value)} tokens</title></rect>"
                )
        return "".join(bars)

    def bars_for_tokens() -> str:
        bars = []
        for i, day in enumerate(days):
            x = padding_left + i * step + (step - bar_width) / 2
            segments: list[tuple[str, int]] = [
                ("series-1", sum(r.get("input_tokens", 0) or 0 for r in day["records"])),
                ("series-2", sum(r.get("output_tokens", 0) or 0 for r in day["records"])),
                ("series-3", sum(r.get("cache_read_tokens", 0) or 0 for r in day["records"])),
                ("series-4", sum(r.get("cache_creation_tokens", 0) or 0 for r in day["records"])),
            ]
            y_cursor: float = padding_top + plot_height
            for css_class, value in segments:
                if value <= 0:
                    continue
                seg_height = (value / max_tokens) * plot_height if max_tokens else 0
                y_cursor -= seg_height
                type_name = SERIES_NAMES[css_class]
                bars.append(
                    f'<rect x="{x:.1f}" y="{y_cursor:.1f}" '
                    f'width="{bar_width:.1f}" height="{seg_height:.1f}" '
                    f'class="chart-bar {css_class}" '
                    f"onclick=\"highlightTokenType('{css_class}', event)\">"
                    f"<title>{type_name} · {day['day']} - "
                    f"{mr.format_tokens(value)} tokens</title></rect>"
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
    pane_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" '
        f'id="chart-pane-{period_id}" preserveAspectRatio="none" '
        f'style="width:{width}px">'
        f"{highlight_html}{build_y_axis(max_pane)}{x_axis_html}{bars_for_pane()}</svg>"
    )
    tokens_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" '
        f'id="chart-tokens-{period_id}" preserveAspectRatio="none" '
        f'style="width:{width}px">'
        f"{highlight_html}{build_y_axis(max_tokens)}{x_axis_html}{bars_for_tokens()}</svg>"
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
    for day in days:
        for r in day["records"]:
            project = r.get("project_path")
            key = mr.color_group_key(project)
            pane_totals[key] = pane_totals.get(key, 0) + mr.record_token_total(r)
            pane_first_path.setdefault(key, project)
    pane_projects = [
        pane_first_path[key]
        for key in sorted(pane_totals, key=lambda k: pane_totals[k], reverse=True)
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
        parent = mr.color_group_key(project)
        raw_name = html.escape(Path((parent or "").rstrip("/")).name or "sem projeto")
        label = html.escape(mr.project_display_label(parent))
        chip_text = raw_name if raw_name == label else f"{raw_name}:{label}"
        pane_legend_chips.append(
            f'<span class="legend-chip" '
            f"onclick=\"highlightProject('{group_class}', event)\">"
            f'<span class="legend-swatch {group_class}"></span>{chip_text}</span>'
        )
    pane_legend = "".join(pane_legend_chips)
    pane_legend_html = f'<div class="project-chart-legend">{pane_legend}</div>'

    return (
        '<div class="trend-container">'
        '<div class="trend-mode-tabs">'
        f'<span class="tab-btn active" id="trend-btn-pane-{period_id}" '
        f"onclick=\"showTrendMode('pane', '{period_id}')\">Por projeto</span>"
        f'<span class="tab-btn" id="trend-btn-tokens-{period_id}" '
        f"onclick=\"showTrendMode('tokens', '{period_id}')\">Por tipo de token</span>"
        "</div>"
        f'<div class="trend-pane active" id="trend-pane-pane-{period_id}">'
        f'<div class="trend-scroll" data-floor-hit="{str(scrollable).lower()}">{pane_svg}</div>'
        # Fixed-height, internally-scrollable slot (2026-07-21) - "por
        # projeto" can have many legend chips wrapping to several lines
        # while "por tipo de token" only ever has 4; without a fixed
        # height here the sticky trend-container's total height changed
        # between the two modes, pushing the session table up/down
        # ("pulando") every time you switched.
        f'<div class="trend-legend-slot">{pane_legend_html}</div>'
        "</div>"
        f'<div class="trend-pane" id="trend-pane-tokens-{period_id}">'
        f'<div class="trend-scroll" data-floor-hit="{str(scrollable).lower()}">{tokens_svg}</div>'
        '<div class="trend-legend-slot"></div>'
        "</div>"
        "</div>"
    )


def compute_project_series_totals(
    records: list[dict[str, Any]],
) -> tuple[dict[str | None, dict[str, int]], dict[str | None, int]]:
    per_project: dict[str | None, dict[str, int]] = {}
    for r in records:
        project = r.get("project_path")
        bucket = per_project.setdefault(
            project, {"series-1": 0, "series-2": 0, "series-3": 0, "series-4": 0}
        )
        bucket["series-1"] += r.get("input_tokens", 0) or 0
        bucket["series-2"] += r.get("output_tokens", 0) or 0
        bucket["series-3"] += r.get("cache_read_tokens", 0) or 0
        bucket["series-4"] += r.get("cache_creation_tokens", 0) or 0
    grand_totals = {project: sum(series.values()) for project, series in per_project.items()}
    return per_project, grand_totals


def render_project_totals_chart(
    records: list[dict[str, Any]],
    project_first_seen: dict[str, str],
    project_last_seen: dict[str, str],
    open_projects: set[str],
    period_id: str,
) -> str:
    ordered_projects, _totals, _max_total = mr.compute_project_totals(records, project_last_seen)
    # Segmented by token type (2026-07-20) instead of one flat bar, so the
    # composition legend can highlight/filter these bars the same way it
    # already does the trend chart's "por tipo de token" mode - same
    # series-N classes, same highlightTokenType handler, same CSS.
    series_by_project, totals = compute_project_series_totals(records)
    # Omit zero-token projects (2026-07-20) - a project section with
    # nothing to show is just dead weight in this list.
    ordered_projects = [p for p in ordered_projects if totals.get(p, 0) > 0]
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
    max_total = max(totals.values()) or 1

    # row_height must clear label_baseline(=row start) + bar(9..23) + the
    # NEXT row's label ascent, or that next label overlaps up into this
    # row's bar - 24 left only 1px between a bar's bottom (y+23) and the
    # next label's baseline (y+24), and a ~10px font's ascent (~7-8px)
    # ate straight into the bar above it. 34 leaves real clearance.
    row_height = 34
    bar_max_width = 300
    label_x = 4
    # 620, not 460 (2026-07-21) - the total text ("<total> · há N dias ·
    # <filtered value>") got clipped at the SVG's right edge once the
    # "grand total + filtered value together" feedback made that string
    # longer; 460-310=150 user units wasn't enough room for it.
    width = 620

    def render_rows(projects: list[str | None]) -> tuple[list[str], int]:
        rows = []
        y = 14
        for project in projects:
            total = totals[project]
            label = mr.project_display_label(project)
            title_attr = f' title="{html.escape(project)}"' if project else ""
            is_open = project in open_projects
            # Bar color now carries token-type (to match the legend), so
            # "open now" moved from bar color to a small text marker next
            # to the label instead of being lost. An SVG <title> element
            # explains what the marker means to readers who might think it
            # is scoped to the period filter.
            open_marker = " <title>aberto agora</title>●" if is_open else ""
            days_running = mr.project_days_running(project_first_seen, project)
            seg_rects = []
            x_cursor: float = label_x
            for css_class, value in series_by_project.get(project, {}).items():
                if value <= 0:
                    continue
                seg_width = (value / max_total) * bar_max_width if max_total else 0
                name = SERIES_NAMES[css_class]
                seg_rects.append(
                    f'<rect x="{x_cursor:.1f}" y="{y + 9}" width="{seg_width:.1f}" height="14" '
                    f'class="chart-bar {css_class}" '
                    f"onclick=\"highlightTokenType('{css_class}', event)\">"
                    f"<title>{html.escape(label)} · {name} · "
                    f"{mr.format_tokens(value)}</title></rect>"
                )
                x_cursor += seg_width
            # Total label carries the grand total (always visible, 2026-07-21
            # feedback: the colored per-type number alone wasn't enough,
            # the total needs to stay visible alongside it) plus one
            # separator+tspan per series, hidden by CSS unless
            # body[data-highlight] selects that series - clicking a
            # legend chip appends " · <that type>" next to the still-visible
            # grand total instead of replacing it. The per-series tspans
            # are italic and tinted that series' color so it reads as
            # "the filtered slice," not a second real total.
            series_tspans = "".join(
                f'<tspan class="total-only {css_class}"> · '
                f"{mr.format_tokens(series_by_project.get(project, {}).get(css_class, 0))}</tspan>"
                for css_class in ("series-1", "series-2", "series-3", "series-4")
            )
            total_text = (
                f'<text x="{label_x + bar_max_width + 6}" y="{y + 20}" '
                'class="chart-axis-label proj-total">'
                f'<tspan class="total-grand">{mr.format_tokens(total)}{days_running}</tspan>'
                f"{series_tspans}"
                "</text>"
            )
            rows.append(
                f'<text x="{label_x}" y="{y}" class="project-chart-label"{title_attr}>'
                f"{html.escape(label)}{open_marker}</text>"
                f'<rect x="{label_x}" y="{y + 9}" width="{bar_max_width}" '
                'height="14" class="project-chart-track" />'
                f"{''.join(seg_rects)}"
                f"{total_text}"
            )
            y += row_height
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
        more_html = (
            f'<div class="project-chart-toggle" id="project-chart-toggle-{period_id}" '
            f"onclick=\"toggleProjectsMore('{period_id}')\">"
            f"+ {len(hidden_projects)} projetos históricos</div>"
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
    legend = render_composition_legend(overall_totals) + (
        '<div class="project-chart-legend"><span class="legend-chip">'
        "● sessão aberta agora</span></div>"
    )
    return (
        '<div class="project-chart-container">'
        '<h3 class="project-chart-title">Total de tokens por projeto</h3>'
        '<p class="project-chart-sort-note">ordenado por atividade mais recente primeiro</p>'
        f"{legend}"
        f"{visible_svg}"
        f"{more_html}"
        "</div>"
    )


def build_group_colors(
    chart_records: list[dict[str, Any]], daily_chart_records: list[dict[str, Any]]
) -> dict[str | None, str]:
    # The trend chart renders daily_chart_records (raw, pre-dedup snapshots
    # expanded into per-day deltas) while colours and the legend are built
    # from chart_records (one deduped representative per session). Any
    # project_path reachable only through the raw feed - a worktree, a
    # directory a session cd'ed out of before its final snapshot, a
    # subagent's own path - was therefore missing from the colour map and
    # fell back to "group-color-none", so clicking that bar highlighted
    # nothing in the legend (regression introduced by bd2e899; measured
    # 2026-08-10 at 38 of 67 rendered paths uncoloured on real data).
    #
    # chart_records goes FIRST so every colour slot already assigned keeps
    # the exact number it had - assign_group_colors skips a project_path it
    # has seen, so appending the daily feed can only ever ADD the missing
    # ones. Worktrees still collapse onto their parent's colour, because
    # the slot is chosen by color_group_key rather than the raw path.
    return mr.assign_group_colors(list(chart_records) + list(daily_chart_records))


def render_backfill_note() -> str:
    text = mr.describe_backfill_state(mr.load_backfill_state())
    return f'<span class="backfill-note">{html.escape(text)}</span>'


def render_period_filter() -> str:
    chips = []
    for period in mr.PERIODS:
        active = " active" if period.id == mr.DEFAULT_PERIOD_ID else ""
        chips.append(
            f'<button type="button" class="period-chip{active}" '
            f'data-period="{period.id}" onclick="selectPeriod(\'{period.id}\')">'
            f"{html.escape(period.label)}</button>"
        )
    return f'<div class="period-filter">{"".join(chips)}{render_backfill_note()}</div>'


def render_html(
    records: list[dict[str, Any]],
    subagents_by_session: dict[Any, list[dict[str, Any]]],
    chart_records: list[dict[str, Any]],
    daily_chart_records: list[dict[str, Any]],
    period_windows: dict[str, tuple[datetime, datetime] | None],
    project_first_seen: dict[str, str],
    project_last_seen: dict[str, str],
    open_projects: set[str],
) -> str:
    group_colors = build_group_colors(chart_records, daily_chart_records)

    def periods_for(record: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            period.id
            for period in mr.PERIODS
            if mr.filter_sessions_by_window([record], period_windows[period.id])
        )

    groups = mr.build_project_groups(records, group_colors)
    has_tmux_data = any(r.get("tmux_window") for r in records)

    # Alberto: omit zero-token sections entirely (2026-07-20) - a
    # session that never recorded any input/output tokens (aborted
    # before the first turn, etc.) adds a row with nothing useful in
    # it. Filter before deciding whether to render a group header at
    # all, so a group whose every session was zero-token doesn't leave
    # a header with no rows under it.
    visible_groups = []
    for group_index, group in enumerate(groups):
        visible_records = [r for r in group["records"] if mr.record_token_total(r) > 0]
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
        for r in group["records"]:
            label = r.get("task_label")
            if label:
                label_counts[label] = label_counts.get(label, 0) + 1
    label_seen: dict[str, int] = {}

    rows_html = []
    panels_html = []
    for group_index, group in visible_groups:
        rows_html.append(render_group_header(group_index, group, period_windows))
        for record in group["records"]:
            session_id = record.get("session_id") or ""
            label = record.get("task_label")
            if label and label_counts.get(label, 0) > 1:
                label_seen[label] = label_seen.get(label, 0) + 1
                record = {**record, "task_label": f"{label} ({label_seen[label]})"}
            session_subagents = subagents_by_session.get(session_id, [])
            visible_subagent_count = sum(
                1 for s in session_subagents if mr.record_token_total(s) > 0
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
                )
            )
            panels_html.append(
                render_detail_panel(
                    session_id, session_subagents, False, period_ids=periods_for(record)
                )
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
    for period in mr.PERIODS:
        window = period_windows[period.id]
        scoped_chart = mr.filter_sessions_by_window(chart_records, window)
        scoped_daily = mr.filter_deltas_by_window(daily_chart_records, window)
        # A real boolean HTML attribute, not a class token - `hidden` has
        # to sit outside `class="..."` or neither the .period-block[hidden]
        # CSS rule nor the DOM's own `.hidden` IDL property (which
        # selectPeriod's JS reads/writes) ever sees it, and every period's
        # chart stays stacked visible on first load instead of only
        # "tudo" (caught by executing this markup in jsdom, not by reading
        # it - the earlier `class="period-block hidden"` form parses fine
        # as HTML, it just isn't the `hidden` attribute).
        hidden_attr = "" if period.id == mr.DEFAULT_PERIOD_ID else " hidden"
        trend_blocks.append(
            f'<div class="period-block" data-period="{period.id}"{hidden_attr}>'
            + render_trend_chart(scoped_daily, group_colors, period.id)
            + "</div>"
        )
        panorama_blocks.append(
            f'<div class="period-block" data-period="{period.id}"{hidden_attr}>'
            + render_project_totals_chart(
                scoped_chart, project_first_seen, project_last_seen, open_projects, period.id
            )
            + "</div>"
        )
        legend_blocks.append(
            f'<div class="period-block" data-period="{period.id}"{hidden_attr}>'
            + render_composition_legend(
                mr.compute_token_type_totals(mr.filter_sessions_by_window(records, window))
            )
            + "</div>"
        )
    trend_blocks_html = "".join(trend_blocks)
    panorama_blocks_html = "".join(panorama_blocks)
    legend_blocks_html = "".join(legend_blocks)

    tabs_html = (
        '<div class="tab-bar">'
        '<span class="tab-btn active" id="tab-btn-sessions" '
        "onclick=\"showTab('sessions')\">Sessões</span>"
        '<span class="tab-btn" id="tab-btn-panorama" '
        "onclick=\"showTab('panorama')\">Panorama</span>"
        "</div>"
    )
    sessions_tab_html = (
        '<div class="tab-pane active" id="tab-sessions">'
        '<div class="main-row">'
        f'<div class="session-list">{legend_blocks_html}'
        f"{render_session_header(has_tmux_data)}{''.join(rows_html)}</div>"
        f'<div class="detail-container">{"".join(panels_html)}</div>'
        "</div>"
        "</div>"
    )
    panorama_tab_html = f'<div class="tab-pane" id="tab-panorama">{panorama_blocks_html}</div>'
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
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Claude Code Metrics Dashboard</title>{STYLE}</head>"
        f'<body data-highlight="{default_highlight}">'
        f"{render_period_filter()}"
        f"{trend_blocks_html}"
        f"{tabs_html}"
        f"{sessions_tab_html}"
        f"{panorama_tab_html}"
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
    args = parser.parse_args()

    records = mr.load_records(mr.METRICS_FILE)
    # Captured before dedupe_sessions discards every snapshot but one per
    # session_id - expand_records_to_daily_deltas needs the FULL raw
    # history to correctly spread a long-running session's token growth
    # across the days it actually happened (2026-08-06 fix). Normalized
    # and tag/project-filtered independently so it stays consistent with
    # the main `records` pipeline below, without depending on dedup ever
    # having run.
    raw_session_records = mr.normalize_project_paths(list(records))
    raw_session_records = mr.filter_records(raw_session_records, tag=args.tag, project=args.project)
    records = mr.dedupe_sessions(records)
    records = mr.normalize_project_paths(records)
    records = mr.filter_records(records, tag=args.tag, project=args.project)
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
    effective_days = args.days

    # The chart always covers the full history (2026-07-21: "por que não
    # cobrir mais datas?") - only the session-list table is bounded by
    # --days/-n. chart_records is all_time_records (already has
    # --tag/--project applied, just not the days window), independent of
    # whatever the table's window/limit end up being below.
    chart_records = all_time_records
    records = mr.filter_by_days(records, effective_days)
    # The --html session-list table is unbounded by -n (2026-07-21:
    # "sem limite (mostra as 2401)") - only the terminal-table mode still
    # defaults to the last 20, unchanged.
    if not args.html:
        records = records[-args.limit :]

    if args.html:
        raw_subagent_records = mr.load_records(mr.SUBAGENTS_FILE)
        subagent_records = mr.dedupe_subagents(raw_subagent_records)
        # See expand_records_to_daily_deltas's docstring: raw (pre-dedup)
        # session + subagent snapshots, delta-attributed to the day each
        # was actually recorded - used ONLY by the trend chart below, so
        # every other view (legend, project rankings, per-row totals)
        # keeps using the regular deduped/folded records untouched.
        daily_chart_records = mr.expand_records_to_daily_deltas(
            raw_session_records
        ) + mr.expand_records_to_daily_deltas(raw_subagent_records)

        # One clock reading for the whole render. Calling now() per
        # surface could straddle midnight and put two panels in
        # different days.
        render_now = datetime.now().astimezone()
        period_windows = {
            period.id: mr.resolve_period(period.id, render_now) for period in mr.PERIODS
        }
        agent_label_cache = mr.load_agent_label_cache()
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
        label_budget = mr.LabelBudget(None if args.label_budget < 0 else args.label_budget)
        agent_keys = list(unique_agents.keys())
        label_by_agent_id = {}
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {
                executor.submit(
                    mr.get_agent_label, unique_agents[k], agent_label_cache, label_budget
                ): k
                for k in agent_keys
            }
            for i, future in enumerate(as_completed(futures), 1):
                k = futures[future]
                label_by_agent_id[k] = future.result()
                if i % SAVE_EVERY == 0:
                    mr.save_agent_label_cache(agent_label_cache)
        for subagent in subagent_records:
            subagent["agent_label"] = label_by_agent_id.get(subagent.get("agent_id"))
        mr.save_agent_label_cache(agent_label_cache)
        subagents_by_session = mr.group_subagents_by_session(subagent_records)
        # Mutates the same dict objects all_time_records/chart_records/records
        # already share (none of the load/dedupe/filter steps above copy the
        # records, only filter the list) - so every aggregate view downstream
        # picks up subagent tokens automatically, without touching each one
        # individually. See fold_subagent_tokens_into_sessions's own
        # docstring for why this was missing (2026-08-05).
        mr.fold_subagent_tokens_into_sessions(all_time_records, subagent_records)

        label_cache = mr.load_label_cache()
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
                executor.submit(mr.get_task_label, unique_sessions[k], label_cache, label_budget): k
                for k in session_keys
            }
            for i, future in enumerate(as_completed(futures), 1):
                k = futures[future]
                label_by_session_id[k] = future.result()
                if i % SAVE_EVERY == 0:
                    mr.save_label_cache(label_cache)
        for record in records:
            record["task_label"] = label_by_session_id.get(record.get("session_id"))
        mr.save_label_cache(label_cache)
        project_first_seen = mr.compute_project_first_seen(all_time_records)
        project_last_seen = mr.compute_project_last_seen(all_time_records)
        open_projects = mr.get_open_projects()
        html_output = render_html(
            records,
            subagents_by_session,
            chart_records,
            daily_chart_records,
            period_windows,
            project_first_seen,
            project_last_seen,
            open_projects,
        )
        output_path = Path("~/.claude/metrics/dashboard.html").expanduser()
        with output_path.open("w") as f:
            f.write(html_output)
        print(output_path)
        return

    subagent_records = mr.dedupe_subagents(mr.load_records(mr.SUBAGENTS_FILE))
    mr.fold_subagent_tokens_into_sessions(records, subagent_records)
    print(format_table(records))
    print(format_summary(records))


if __name__ == "__main__":  # pragma: no cover - true only when run as a script, not under pytest
    main()
