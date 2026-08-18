#!/usr/bin/env python3
"""Terminal dashboard (TUI) for Claude Code session metrics.

Imports its data layer from lib/metrics_core (see that package for the
data model and docs/superpowers/specs/2026-07-18-terminal-dashboard-design.md
for the full design) - this file is presentation-layer only, exactly
like every HTML dashboard round was. Requires textual + textual-plotext
(see requirements-tui.txt) - run from the repo root using this plugin's
own .venv (see scripts/setup-tui-venv.sh to create it):
    python3 dashboard_tui.py
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Static, TabbedContent, TabPane, Tree
from textual_plotext import PlotextPlot


def import_metrics_report():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
    import metrics_core
    return metrics_core


def load_base_data(args, mr=None):
    """The expensive half of the load: everything that does NOT depend on
    the period.

    Loading the JSONL files, deduping snapshots, normalizing project paths,
    expanding daily deltas, resolving labels and folding subagent tokens all
    produce byte-identical results whatever period is selected - the period
    only decides which of those records survive a filter at the very end.
    So this runs ONCE, at startup, and `apply_period` re-windows its output
    as many times as the user cares to press "p".

    Splitting here is not a micro-optimization (2026-08-17). Before it,
    action_cycle_period called the whole of load_data synchronously on the
    event loop for every keypress, measured at 2.1-2.7s per press on a
    4545-session dataset - long enough that the tmux pane looked hung and
    had to be killed. See tests/test_tui_responsiveness.py for the
    regression test that keeps that work out of the keypress path.
    """
    mr = mr or import_metrics_report()

    # Dedupe + normalize the same way metrics-report.py's main() does
    # before ANYTHING else touches records (2026-07-21) - the Stop hook
    # appends a fresh cumulative snapshot every turn, so one session_id can
    # have dozens of duplicate rows with growing token counts (found up to
    # ~50x inflation upstream). normalize_project_paths then rolls a
    # subdirectory cwd up to its real project root and folds scratchpad/tmp
    # cwds into "sem projeto" - see that function's docstring in
    # metrics-report.py for the fan-out safeguard that keeps it from
    # collapsing unrelated projects into a shared container directory.
    records = mr.load_records(mr.METRICS_FILE)
    # Captured before dedupe_sessions discards every snapshot but one per
    # session_id - expand_records_to_daily_deltas needs the FULL raw
    # history to correctly spread a long-running session's token growth
    # across the days it actually happened (2026-08-06 fix), instead of
    # dumping it all onto the session's fixed start day.
    raw_session_records = mr.normalize_project_paths(list(records))
    raw_session_records = mr.filter_records(raw_session_records, tag=args.tag, project=args.project)
    records = mr.dedupe_sessions(records)
    records = mr.normalize_project_paths(records)
    records = mr.filter_records(records, tag=args.tag, project=args.project)
    all_time_records = records

    # The period is the single authority on time (2026-08-13). This used to
    # default to a 30-day window applied through filter_by_days BEFORE the
    # period filter ran - and filter_by_days keys on timestamp_start, the
    # exact start-date rule this phase replaced with span overlap. The
    # result was a dashboard that lied: "tudo" showed 3644 of 4530 sessions,
    # "mes" and "tudo" were byte-identical on four of five surfaces, and
    # TrendChart disagreed with the rest of its own screen because
    # daily_chart_records was captured before the cap. --days stays as an
    # explicit user override, and --all is still accepted (it now names what
    # already happens by default) so existing invocations keep working.
    effective_days = args.days

    records = mr.filter_by_days(records, effective_days)
    chart_records = records
    # No -n cap on the session list (2026-07-21, mirrors metrics-report.py's
    # --html branch): a Textual Tree already scrolls natively, so there's no
    # "table overflows the terminal" problem an artificial cap would be
    # solving here - only the plain terminal-table mode in metrics-report.py
    # still defaults to -n=20. --limit/-n is kept as a CLI flag for anyone
    # who wants to bound it explicitly, but it's no longer applied by default.
    if args.limit is not None:
        records = records[-args.limit:]

    # Parallelized labeling, same pattern as metrics-report.py's --html
    # branch. subagent_records is already deduped by agent_id above the
    # growing-snapshot problem dedupe_subagents fixes - preferring a
    # source-bearing duplicate as representative here is a separate, smaller
    # concern: two THREADS racing the same cache key could otherwise
    # interleave a real label with a failed one, and negative-caching would
    # then permanently lock in whichever thread wrote last.
    raw_subagent_records = mr.load_records(mr.SUBAGENTS_FILE)
    subagent_records = mr.dedupe_subagents(raw_subagent_records)
    # See expand_records_to_daily_deltas's docstring: used ONLY by the
    # trend chart, so every other view (composition legend, project
    # rankings, per-row totals) keeps using the regular deduped/folded
    # records untouched.
    daily_chart_records = mr.expand_records_to_daily_deltas(
        raw_session_records
    ) + mr.expand_records_to_daily_deltas(raw_subagent_records)
    agent_label_cache = mr.load_agent_label_cache()
    unique_agents = {}
    for subagent in subagent_records:
        key = subagent.get("agent_id")
        if key is None:
            continue
        existing = unique_agents.get(key)
        if existing is None:
            unique_agents[key] = subagent
        elif not existing.get("agent_transcript_path") and subagent.get("agent_transcript_path"):
            unique_agents[key] = subagent
    # max_workers=1 (serial), matching metrics-report.py's own fix
    # (2026-07-20/23): each worker spawns a full `claude -p` CLI
    # subprocess, not a lightweight thread task - 8 concurrent ones
    # spiked load average past 50 and froze the machine during a
    # cold-cache backfill. label_budget caps how many brand-new labels
    # (real API calls) this run generates before it stops (2026-07-23,
    # same reasoning as metrics-report.py's --label-budget: unbounded
    # backfills burn real quota and kept getting killed mid-run anyway).
    #
    # Kept rather than replaced by a plain loop, measured not assumed
    # (2026-08-17). With max_workers=1 the pool is pure overhead when every
    # lookup is a cache hit, and it is: 2.07s median startup with it against
    # 1.86s with a serial stand-in, over five runs on a 4545-session
    # dataset - 0.21s, one time, while the app is already starting. That
    # buys keeping the structure this comment documents, which is the thing
    # that makes "serial ON PURPOSE" visible to whoever next touches the
    # budget>0 path where the subprocesses are real. What actually costs
    # here is neither the pool nor the cache: 262 of 4545 sessions miss the
    # cache and, since --label-budget 0 deliberately declines to cache them
    # so a later run can pick them up, each load re-reads their transcripts
    # (~1.2s). That is unchanged by this split, and no longer on the
    # keypress path.
    label_budget = mr.LabelBudget(None if args.label_budget < 0 else args.label_budget)
    agent_keys = list(unique_agents.keys())
    with ThreadPoolExecutor(max_workers=1) as executor:
        agent_labels = list(
            executor.map(lambda k: mr.get_agent_label(unique_agents[k], agent_label_cache, label_budget), agent_keys)
        )
    label_by_agent_id = dict(zip(agent_keys, agent_labels))
    for subagent in subagent_records:
        subagent["agent_label"] = label_by_agent_id.get(subagent.get("agent_id"))
    mr.save_agent_label_cache(agent_label_cache)
    subagents_by_session = mr.group_subagents_by_session(subagent_records)
    # Mutates the same dict objects all_time_records/chart_records/records
    # already share (none of the load/dedupe/filter steps above copy the
    # records, only filter the list) - so every aggregate view downstream
    # (composition legend, trend chart, project totals) picks up subagent
    # tokens automatically. See fold_subagent_tokens_into_sessions's own
    # docstring for why this was missing (2026-08-05).
    mr.fold_subagent_tokens_into_sessions(all_time_records, subagent_records)

    label_cache = mr.load_label_cache()
    unique_sessions = {}
    for record in records:
        key = record.get("session_id")
        if key is None:
            continue
        existing = unique_sessions.get(key)
        if existing is None:
            unique_sessions[key] = record
        elif not (existing.get("internal_name") or existing.get("transcript_path")) and (
            record.get("internal_name") or record.get("transcript_path")
        ):
            unique_sessions[key] = record
    session_keys = list(unique_sessions.keys())
    with ThreadPoolExecutor(max_workers=1) as executor:
        task_labels = list(
            executor.map(lambda k: mr.get_task_label(unique_sessions[k], label_cache, label_budget), session_keys)
        )
    label_by_session_id = dict(zip(session_keys, task_labels))
    for record in records:
        record["task_label"] = label_by_session_id.get(record.get("session_id"))
    mr.save_label_cache(label_cache)

    project_first_seen = mr.compute_project_first_seen(all_time_records)
    project_last_seen = mr.compute_project_last_seen(all_time_records)
    open_projects = mr.get_open_projects()

    return {
        "records": records,
        "chart_records": chart_records,
        "daily_chart_records": daily_chart_records,
        "subagents_by_session": subagents_by_session,
        "project_first_seen": project_first_seen,
        "project_last_seen": project_last_seen,
        "open_projects": open_projects,
    }


def apply_period(base, period_id, mr, now=None):
    """The cheap half: narrows already-loaded data to one period's window.

    Pure list filtering over records that are already in memory, so this is
    milliseconds even on a dataset where load_base_data takes seconds. It
    is what every "p" press runs, and the only thing it is allowed to run.

    The three filters return NEW lists of the SAME record dicts, so nothing
    here mutates the base - re-windowing is repeatable in any order, any
    number of times. That matters beyond tidiness: the base's
    fold_subagent_tokens_into_sessions step mutates records in place and
    would double-count if it ever ran twice over the same dicts.

    `now` is a parameter for the same reason resolve_period takes one - so
    a test can pin the clock - but it defaults to the real clock read HERE,
    per press, rather than being captured once at startup: a TUI left open
    across midnight must resolve "ontem" against today, not against the day
    it was launched.
    """
    render_now = now or datetime.now().astimezone()
    window = mr.resolve_period(period_id, render_now)
    return {
        "records": mr.filter_sessions_by_window(base["records"], window),
        "chart_records": mr.filter_sessions_by_window(base["chart_records"], window),
        "daily_chart_records": mr.filter_deltas_by_window(base["daily_chart_records"], window),
        "subagents_by_session": base["subagents_by_session"],
        "project_first_seen": base["project_first_seen"],
        "project_last_seen": base["project_last_seen"],
        "open_projects": base["open_projects"],
        "period_id": period_id,
        # The period-independent source this view was derived from, carried
        # along so any holder of a view can re-window it without reloading.
        # Kept inside the dict rather than added as a load_data return value
        # or an app constructor argument so that every existing caller -
        # main(), the headless drivers under .superpowers/ - keeps working
        # unchanged while still getting the fast path.
        "base": base,
    }


def load_data(args, mr=None, period_id=None):
    """Loads and windows in one call - the original signature, unchanged.

    Kept for callers that just want data for one period (main(), the
    headless drivers, ad-hoc scripts). Anything that re-windows repeatedly
    must call load_base_data once and apply_period per period instead;
    calling this in a loop is precisely the defect it was split to fix.
    """
    mr = mr or import_metrics_report()
    period_id = period_id or mr.DEFAULT_PERIOD_ID
    return apply_period(load_base_data(args, mr=mr), period_id, mr)


def build_session_tree(records, subagents_by_session, mr):
    group_colors = mr.assign_group_colors(records)
    groups = mr.build_project_groups(records, group_colors)
    tree = Tree("Sessões")
    tree.show_root = False
    for group in groups:
        project_path = group["project"]
        # Omit zero-token sessions (2026-07-21, ported from
        # metrics-report.py's render_html) - a session that never recorded
        # any input/output tokens (aborted before the first turn, etc.) is
        # dead weight in the tree. A group whose every session is zero-token
        # is skipped entirely rather than left as an empty, expandable node.
        visible_records = [
            r for r in group["records"]
            if mr.record_token_total(r) > 0
        ]
        if not visible_records:
            continue
        label = mr.project_display_label(project_path)
        total_tokens = sum(mr.record_token_total(r) for r in visible_records)
        group_node = tree.root.add(
            f"{label} ({len(visible_records)} sessões · {mr.format_tokens(total_tokens)} tokens)",
            expand=False,
        )
        for record in visible_records:
            session_id = record.get("session_id") or ""
            date_str = mr.format_date(record.get("timestamp_start"))
            task_label = record.get("task_label") or "-"
            tokens = mr.format_tokens(mr.record_token_total(record))
            duration = record.get("duration_seconds")
            duration_str = f"{duration:.0f}s" if duration is not None else "-"
            # Subagent count badge (2026-07-21, ported from
            # metrics-report.py's render_session_row/subagent_badge) - lets
            # you tell which sessions dispatched subagents right in the
            # tree, without expanding every single one's detail panel.
            # Counts only subagents that actually recorded tokens, same
            # filter render_html applies to its own badge.
            session_subagents = subagents_by_session.get(session_id, [])
            visible_subagent_count = sum(
                1 for s in session_subagents
                if mr.record_token_total(s) > 0
            )
            subagent_badge = f" 🤖{visible_subagent_count}" if visible_subagent_count else ""
            group_node.add_leaf(
                f"{session_id[:12]:<14}{date_str:<14}{task_label[:28]:<30}{tokens:>8}   {duration_str:>7}{subagent_badge}",
                data={"session_id": session_id},
            )
    return tree


class SessionDetailPanel(VerticalScroll):
    def __init__(self, mr, **kwargs):
        super().__init__(**kwargs)
        self.mr = mr
        self._body = Static("")

    def compose(self):
        yield self._body

    def clear(self):
        self._body.update("")

    def show_session(self, session_id, subagents):
        lines = [f"[bold]{session_id}[/bold]", ""]
        if not subagents:
            lines.append("[dim]Nenhum subagente nesta sessão.[/dim]")
        else:
            lines.append(f"[bold]Subagentes despachados ({len(subagents)})[/bold]")
            lines.append(f"{'tarefa':<40}{'tokens':>12}{'tools':>8}{'duração':>10}")
            for i, subagent in enumerate(subagents):
                agent_type = subagent.get("agent_type") or "-"
                agent_label = subagent.get("agent_label") or "-"
                in_tok = self.mr.format_tokens(subagent.get("input_tokens", 0))
                out_tok = self.mr.format_tokens(subagent.get("output_tokens", 0))
                duration = subagent.get("duration_seconds")
                duration_str = f"{duration:.0f}s" if duration is not None else "-"
                task = f"{agent_type} — {agent_label}"[:38]
                style = "on #202020" if i % 2 == 1 else ""
                lines.append(
                    f"[{style}]{task:<40}{in_tok}/{out_tok:<8}{subagent.get('tool_call_count', 0):>8}{duration_str:>10}[/{style}]"
                    if style else
                    f"{task:<40}{in_tok}/{out_tok:<8}{subagent.get('tool_call_count', 0):>8}{duration_str:>10}"
                )
        self._body.update("\n".join(lines))


class CompositionLegend(Static):
    highlighted = reactive(None)

    class Highlight(Message):
        def __init__(self, series):
            self.series = series
            super().__init__()

    SERIES = [
        ("series-1", "input"),
        ("series-2", "output"),
        ("series-3", "cache read"),
        ("series-4", "cache creation"),
    ]

    def __init__(self, totals, mr, **kwargs):
        super().__init__(**kwargs)
        self.totals = totals
        self.mr = mr

    def render(self):
        chips = []
        for series, name in self.SERIES:
            total = self.totals.get(series, 0)
            formatted = self.mr.format_tokens(total)
            is_highlighted = self.highlighted is not None and series in self.highlighted
            style = "bold reverse" if is_highlighted else ""
            chip = f"[{style}]{name} · {formatted}[/{style}]" if style else f"{name} · {formatted}"
            chips.append(chip)
        return "   ".join(chips)

    def on_click(self, event):
        x = event.x
        chip_width = self.size.width // 4 if self.size.width else 0
        index = min(x // max(chip_width, 1), 3) if chip_width else 0
        series = self.SERIES[index][0]
        current = self.highlighted or set()
        if event.shift:
            self.highlighted = current | {series}
        elif event.ctrl:
            remaining = current - {series}
            self.highlighted = remaining or None
        else:
            self.highlighted = None if current == {series} else {series}
        self.post_message(self.Highlight(self.highlighted))


PANE_MAX_PROJECTS = 7
PANE_COLORS = ["green", "blue", "magenta", "orange", "cyan", "red", "yellow"]
PANE_OTHER_COLOR = "gray"
PANE_OTHER_KEY = "__outros__"


def compute_pane_projects(mr, chart_records):
    """Rank projects by total tokens in chart_records, grouped by color_group_key.

    Returns (top_keys, has_other): top_keys is the ordered list of the
    PANE_MAX_PROJECTS highest-token project keys; has_other is True when
    there are more distinct projects than that, meaning callers should
    add one more "outros" bucket for everything not in top_keys.
    """
    totals = {}
    for r in chart_records:
        key = mr.color_group_key(r.get("project_path"))
        totals[key] = totals.get(key, 0) + mr.record_token_total(r)
    ordered = sorted(totals.keys(), key=lambda k: totals[k], reverse=True)
    top_keys = ordered[:PANE_MAX_PROJECTS]
    has_other = len(ordered) > PANE_MAX_PROJECTS
    return top_keys, has_other


def pane_series_label(mr, key):
    return "outros" if key == PANE_OTHER_KEY else mr.project_display_label(key)


def floor_stacked_bottom_series(bottom_series, day_totals):
    """Work around a plotext stacked_bar rendering bug: when the FIRST
    (bottom) series in the stack is exactly 0 for a bar that otherwise has
    data, the visible segment above it renders one row too high, making the
    bar look like it floats above the zero line instead of touching it.
    Confirmed by isolating plotext's stacked_bar with a minimal repro during
    this fix - a nonzero floor on the bottom series alone (here, 1 token,
    negligible next to real per-day totals in the thousands-to-millions)
    eliminates the artifact. Only applied on days with real data (day_total
    > 0) - a genuinely empty day must stay at 0, not get a phantom bar.
    """
    return [1 if v == 0 and day_totals[i] > 0 else v for i, v in enumerate(bottom_series)]


class TrendChart(PlotextPlot):
    # PlotextPlot is not focusable by default (2026-08-18): it needs to be
    # so action_cycle_period has something inside the Panorama pane to land
    # focus on after "p", the same way Tree already anchors the Sessões
    # pane. It is the widget the user actually meant by "o gráfico".
    can_focus = True

    SERIES_KEYS = ["series-1", "series-2", "series-3", "series-4"]
    SERIES_COLORS = ["green", "blue", "magenta", "orange"]

    def __init__(self, mr, chart_records, **kwargs):
        super().__init__(**kwargs)
        self.mr = mr
        self.chart_records = chart_records
        self.mode = "pane"
        self.highlighted = None
        self.project_highlighted = None

    def on_mount(self):
        self.render_chart()

    def toggle_mode(self):
        self.mode = "tokens" if self.mode == "pane" else "pane"
        self.render_chart()

    def set_highlight(self, series):
        self.highlighted = series
        if self.mode == "tokens":
            self.render_chart()

    def set_project_highlight(self, projects):
        self.project_highlighted = projects
        if self.mode == "pane":
            self.render_chart()

    def render_chart(self):
        # aggregate_by_day returns (granularity, days) since
        # metrics-report.py's adaptive-granularity feature (2026-07-21) -
        # this file wasn't updated when that landed, so it was treating
        # the whole tuple as the days list. Bucket keys are also
        # granularity-specific now ("YYYY-MM-DDTHH" for hour, "YYYY-MM"
        # for month, "YYYY" for year, not always "YYYY-MM-DD"), so the
        # tick label must use that granularity's own parse/tick format
        # instead of a hardcoded [5:] MM-DD slice.
        granularity, days = self.mr.aggregate_by_day(self.chart_records)
        self.plt.clear_data()
        if len(days) < 2:
            self.refresh()
            return
        parse_fmt = self.mr.GRANULARITY_CONFIG[granularity]["parse_fmt"]
        tick_fmt = self.mr.GRANULARITY_CONFIG[granularity]["tick_fmt"]
        labels = [datetime.strptime(d["day"], parse_fmt).strftime(tick_fmt) for d in days]
        if self.mode == "pane":
            top_keys, has_other = compute_pane_projects(self.mr, self.chart_records)
            series_keys = list(top_keys) + ([PANE_OTHER_KEY] if has_other else [])
            self.pane_series_keys = series_keys
            series_data = {k: [] for k in series_keys}
            for d in days:
                day_totals = {k: 0 for k in series_keys}
                for r in d["records"]:
                    r_key = self.mr.color_group_key(r.get("project_path"))
                    bucket = r_key if r_key in top_keys else PANE_OTHER_KEY
                    day_totals[bucket] += self.mr.record_token_total(r)
                for k in series_keys:
                    series_data[k].append(day_totals[k])
            series_lists = [series_data[k] for k in series_keys]
            day_totals_list = [sum(series_data[k][i] for k in series_keys) for i in range(len(days))]
            if series_lists:
                series_lists[0] = floor_stacked_bottom_series(series_lists[0], day_totals_list)
            series_labels = [pane_series_label(self.mr, k) for k in series_keys]
            palette = [
                PANE_COLORS[i] if i < len(PANE_COLORS) else PANE_OTHER_COLOR
                for i in range(len(series_keys))
            ]
            if self.project_highlighted:
                color = [
                    palette[i] if key in self.project_highlighted else "gray"
                    for i, key in enumerate(series_keys)
                ]
            else:
                color = palette
            self.plt.stacked_bar(labels, series_lists, labels=series_labels, color=color)
            self.plt.title("Tokens por dia (por projeto)")
            max_total = max((sum(series_data[k][i] for k in series_keys) for i in range(len(days))), default=0)
        else:
            input_totals = [sum(r.get("input_tokens", 0) or 0 for r in d["records"]) for d in days]
            output_totals = [sum(r.get("output_tokens", 0) or 0 for r in d["records"]) for d in days]
            cache_read_totals = [sum(r.get("cache_read_tokens", 0) or 0 for r in d["records"]) for d in days]
            cache_creation_totals = [sum(r.get("cache_creation_tokens", 0) or 0 for r in d["records"]) for d in days]
            day_totals_list = [
                input_totals[i] + output_totals[i] + cache_read_totals[i] + cache_creation_totals[i]
                for i in range(len(days))
            ]
            input_totals = floor_stacked_bottom_series(input_totals, day_totals_list)
            if self.highlighted:
                color = [
                    self.SERIES_COLORS[i] if key in self.highlighted else "gray"
                    for i, key in enumerate(self.SERIES_KEYS)
                ]
            else:
                color = self.SERIES_COLORS
            self.plt.stacked_bar(
                labels,
                [input_totals, output_totals, cache_read_totals, cache_creation_totals],
                labels=["input", "output", "cache read", "cache creation"],
                color=color,
            )
            self.plt.title("Tokens por dia (por tipo de token)")
            max_total = max(
                (
                    input_totals[i] + output_totals[i] + cache_read_totals[i] + cache_creation_totals[i]
                    for i in range(len(days))
                ),
                default=0,
            )
        # plotext's default y-axis ticks are raw, unformatted floats (e.g.
        # "1591684.7") - unreadable next to token counts everywhere else in
        # this dashboard already using k/M/B/T suffixes. Overriding with
        # explicit ticks at even fractions of the tallest bar keeps the
        # scale meaningful while reusing format_tokens for the labels.
        tick_values = [max_total * frac for frac in (0, 0.25, 0.5, 0.75, 1.0)]
        tick_labels = [self.mr.format_tokens(v) for v in tick_values]
        self.plt.yticks(tick_values, tick_labels)
        self.refresh()


class ProjectLegend(Static):
    highlighted = reactive(None)

    class Highlight(Message):
        def __init__(self, projects):
            self.projects = projects
            super().__init__()

    def __init__(self, mr, chart_records, **kwargs):
        super().__init__(**kwargs)
        self.mr = mr
        self.chart_records = chart_records

    def _series_keys(self):
        top_keys, has_other = compute_pane_projects(self.mr, self.chart_records)
        return list(top_keys) + ([PANE_OTHER_KEY] if has_other else [])

    def render(self):
        series_keys = self._series_keys()
        if not series_keys:
            return "[dim]Sem dados.[/dim]"
        chips = []
        for key in series_keys:
            label = pane_series_label(self.mr, key)
            is_highlighted = self.highlighted is not None and key in self.highlighted
            style = "bold reverse" if is_highlighted else ""
            chip = f"[{style}]{label}[/{style}]" if style else label
            chips.append(chip)
        return "   ".join(chips)

    def on_click(self, event):
        series_keys = self._series_keys()
        if not series_keys:
            return
        chip_width = self.size.width // len(series_keys) if self.size.width else 0
        index = min(event.x // max(chip_width, 1), len(series_keys) - 1) if chip_width else 0
        key = series_keys[index]
        current = self.highlighted or set()
        if event.shift:
            self.highlighted = current | {key}
        elif event.ctrl:
            remaining = current - {key}
            self.highlighted = remaining or None
        else:
            self.highlighted = None if current == {key} else {key}
        self.post_message(self.Highlight(self.highlighted))


class ProjectRankingList(Static):
    expanded = reactive(False)

    def __init__(self, mr, chart_records, project_first_seen, project_last_seen, open_projects, **kwargs):
        super().__init__(**kwargs)
        self.mr = mr
        self.chart_records = chart_records
        self.project_first_seen = project_first_seen
        self.project_last_seen = project_last_seen
        self.open_projects = open_projects

    def toggle_more(self):
        self.expanded = not self.expanded

    def render(self):
        ordered, totals, max_total = self.mr.compute_project_totals(self.chart_records, self.project_last_seen)
        # Omit zero-token projects (2026-07-21, ported from
        # metrics-report.py's render_project_totals_chart) - a project with
        # nothing to show is just dead weight in this ranking.
        ordered = [p for p in ordered if totals.get(p, 0) > 0]
        if not ordered:
            return "[dim]Sem dados.[/dim]"

        max_total = max_total or 1
        visible = ordered[:10]
        hidden = ordered[10:]
        shown = visible + (hidden if self.expanded else [])

        lines = ["[bold]Total de tokens por projeto[/bold]", ""]
        for project in shown:
            total = totals[project]
            label = self.mr.project_display_label(project)
            bar_len = int((total / max_total) * 30)
            is_open = project in self.open_projects
            bar_char = "█" if is_open else "▓"
            days_running = self.mr.project_days_running(self.project_first_seen, project)
            lines.append(f"{label[:24]:<26}{bar_char * bar_len:<32}{self.mr.format_tokens(total)}{days_running}")

        if hidden:
            toggle_label = "▾ menos" if self.expanded else f"▸ + {len(hidden)} projetos históricos"
            lines.append("")
            lines.append(f"[dim]{toggle_label}[/dim]")

        lines.append("")
        lines.append("[dim]█ sessão aberta agora   ▓ histórico[/dim]")
        return "\n".join(lines)


def build_arg_parser():
    parser = argparse.ArgumentParser(description="Terminal (TUI) dashboard for Claude Code session metrics.")
    parser.add_argument("--tag", default=None, help="Filter by session tag")
    parser.add_argument("--project", default=None, help="Filter by project path")
    # No default cap (2026-07-21): the Tree widget scrolls natively, so unlike
    # metrics-report.py's plain terminal-table mode there's no overflow
    # problem a default -n=20 would be solving here. Pass -n explicitly to
    # bound it anyway.
    parser.add_argument("-n", "--limit", type=int, default=None, help="Number of most recent sessions to show (unbounded by default)")
    parser.add_argument("--days", type=int, default=None, help="Only include sessions from the last N days")
    parser.add_argument("--all", action="store_true", help="Accepted for compatibility and now the default: the full history is shown unless --days is given")
    parser.add_argument(
        "--label-budget", type=int, default=0,
        help="Max NEW labels (real claude -p calls) to generate this run; already-cached labels are free and unlimited. Use 0 for none, a negative number for unlimited.",
    )
    return parser


class MetricsTUIApp(App):
    CSS = """
    Horizontal { height: 1fr; }
    Tree { width: 62%; }
    SessionDetailPanel { width: 1fr; border-left: solid $panel; padding: 1; }
    .backfill-note { color: $text-muted; height: auto; padding: 0 1; }
    """
    BINDINGS = [
        ("q", "quit", "Sair"),
        ("t", "toggle_chart_mode", "Alterna modo do gráfico"),
        ("m", "toggle_more_projects", "Mais/menos projetos"),
        ("g", "toggle_tab", "Gráficos/Sessões"),
        ("p", "cycle_period", "período"),
    ]

    # `args` is required, not an args=None default (2026-08-13): the app
    # hands it straight to load_data on every period cycle, so a None here
    # does not fail at construction - it raises AttributeError deep inside
    # load_data the first time someone presses "p".
    def __init__(self, mr, data, args, **kwargs):
        super().__init__(**kwargs)
        self.mr = mr
        self.data = data
        self.args = args
        # Read eagerly, not lazily inside action_cycle_period, for the same
        # reason `args` is required above: a data dict built by hand without
        # a "base" should fail loudly at construction, where the mistake is,
        # instead of raising on the first "p" press in front of the user.
        self.base = data["base"]
        # Which TabPane is active, tracked independently of TabbedContent
        # itself (2026-08-18). compose() builds a brand-new TabbedContent
        # every time action_cycle_period recomposes the widget tree, and a
        # fresh TabbedContent() with no `initial=` always opens on its first
        # pane - so without this, cycling the period while on "Panorama"
        # silently bounced the user back to "Sessões". Reproduced headlessly
        # before this field existed: g then p left tabbed.active as
        # "tab-sessions", not "tab-panorama". Default matches TabbedContent's
        # own default (first pane) and on_mount's unconditional Tree focus.
        self.active_tab_id = "tab-sessions"

    def compose(self) -> ComposeResult:
        yield Static(
            self.mr.describe_backfill_state(self.mr.load_backfill_state()),
            classes="backfill-note",
        )
        with TabbedContent(initial=self.active_tab_id):
            with TabPane("Sessões", id="tab-sessions"):
                with Horizontal():
                    yield build_session_tree(self.data["records"], self.data["subagents_by_session"], self.mr)
                    yield SessionDetailPanel(self.mr)
            with TabPane("Panorama", id="tab-panorama"):
                totals = self.mr.compute_token_type_totals(self.data["records"])
                yield CompositionLegend(totals, self.mr)
                yield ProjectLegend(self.mr, self.data["chart_records"])
                yield TrendChart(self.mr, self.data["daily_chart_records"])
                yield ProjectRankingList(
                    self.mr,
                    self.data["chart_records"],
                    self.data["project_first_seen"],
                    self.data["project_last_seen"],
                    self.data["open_projects"],
                )
        yield Footer()

    def on_mount(self):
        self.query_one(Tree).focus()
        self._sync_legend_visibility()

    def _sync_legend_visibility(self):
        mode = self.query_one(TrendChart).mode
        self.query_one(CompositionLegend).display = mode == "tokens"
        self.query_one(ProjectLegend).display = mode == "pane"

    def on_tree_node_selected(self, event):
        session_data = event.node.data
        if not session_data:
            return
        session_id = session_data["session_id"]
        subagents = self.data["subagents_by_session"].get(session_id, [])
        self.query_one(SessionDetailPanel).show_session(session_id, subagents)

    def action_toggle_chart_mode(self):
        self.query_one(TrendChart).toggle_mode()
        self._sync_legend_visibility()

    def action_toggle_more_projects(self):
        self.query_one(ProjectRankingList).toggle_more()

    def action_toggle_tab(self):
        tabbed = self.query_one(TabbedContent)
        # Setting TabbedContent.active reverts within one event-loop tick
        # if the currently-focused widget (the Tree, focused since
        # on_mount) still lives inside the pane being switched away from
        # - a TabPane.Focused message re-asserts that pane as active.
        # Clearing focus first avoids the revert; confirmed via headless
        # reproduction during design (see this plan's Global Constraints).
        self.set_focus(None)
        if tabbed.active == "tab-sessions":
            tabbed.active = "tab-panorama"
        else:
            tabbed.active = "tab-sessions"
            self.query_one(Tree).focus()
        # Kept in sync here so a later action_cycle_period recompose (which
        # builds a brand-new TabbedContent from scratch) knows which pane
        # to reopen on, instead of always defaulting to the first one.
        self.active_tab_id = tabbed.active

    def on_composition_legend_highlight(self, event):
        self.query_one(TrendChart).set_highlight(event.series)

    def on_project_legend_highlight(self, event):
        self.query_one(TrendChart).set_project_highlight(event.projects)

    def action_cycle_period(self):
        ids = [p.id for p in self.mr.PERIODS]
        current = self.data.get("period_id", self.mr.DEFAULT_PERIOD_ID)
        next_id = ids[(ids.index(current) + 1) % len(ids)]
        # Focus is dropped before the rebuild for the same reason
        # action_toggle_tab does it: a focused widget inside a pane being
        # replaced makes Textual revert the change within one tick.
        self.set_focus(None)
        # Re-window data already in memory. This MUST NOT call load_data:
        # that re-reads both JSONL files, re-dedupes, re-labels and re-folds
        # subagent tokens, all of which produce the same answer for every
        # period, and all of which run on the event loop - blocking the
        # whole UI for seconds per press (2026-08-17).
        self.data = apply_period(self.base, next_id, self.mr)
        self.refresh(recompose=True)
        # Restore focus once the recompose above has actually swapped in the
        # new widgets, not before (2026-08-18). Found by hand: after "p" the
        # Footer went blank until Tab was pressed. Root cause was two
        # compounding effects of set_focus(None) + recompose, not one:
        #   1. Textual's Footer only repaints when the screen's
        #      bindings-changed signal fires (Screen.set_focus schedules
        #      that). recompose() throws away the old Footer instance
        #      (subscribed to that signal) and mounts a brand-new one that
        #      has never received it, so it renders nothing until some
        #      later focus change fires the signal again - which is exactly
        #      what pressing Tab did.
        #   2. Nothing was landing focus back on a widget belonging to the
        #      pane the recompose actually reopened on, so that "later focus
        #      change" never happened on its own.
        # Focusing a real widget here fixes both: it fires the same signal
        # Tab was relied on for, and it is what makes "the shortcuts are
        # visible without pressing anything" true. self.call_next (not
        # call_after_refresh) queues right behind the recompose's own
        # call_next(self._check_recompose) - confirmed headlessly that by
        # the time this runs, query_one(Tree) already returns the NEW tree,
        # not the one about to be torn down.
        self.call_next(self._restore_focus_after_period_cycle)
        self.notify(f"período: {dict((p.id, p.label) for p in self.mr.PERIODS)[next_id]}")

    def _restore_focus_after_period_cycle(self):
        """Focus a widget that actually belongs to the pane recompose
        reopened on (self.active_tab_id), mirroring the pattern
        action_toggle_tab already uses for the sessions tab."""
        if self.active_tab_id == "tab-sessions":
            self.query_one(Tree).focus()
        else:
            self.query_one(TrendChart).focus()


def main():
    args = build_arg_parser().parse_args()
    mr = import_metrics_report()
    data = load_data(args, mr=mr)
    app = MetricsTUIApp(mr, data, args=args)
    app.run()


if __name__ == "__main__":
    main()
