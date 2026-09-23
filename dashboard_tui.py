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

from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Static, TabbedContent, TabPane, Tree
from textual_plotext import PlotextPlot


def import_metrics_report() -> ModuleType:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import metrics_core

    return metrics_core


def load_base_data(args: argparse.Namespace, mr: ModuleType | None = None) -> dict[str, Any]:
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

    # Dedupe + normalize the same way dashboard_html.py's main() does
    # before ANYTHING else touches records (2026-07-21) - the Stop hook
    # appends a fresh cumulative snapshot every turn, so one session_id can
    # have dozens of duplicate rows with growing token counts (found up to
    # ~50x inflation upstream). normalize_project_paths then rolls a
    # subdirectory cwd up to its real project root and folds scratchpad/tmp
    # cwds into "sem projeto" - see that function's docstring in
    # dashboard_html.py for the fan-out safeguard that keeps it from
    # collapsing unrelated projects into a shared container directory.
    records = mr.load_records(mr.METRICS_FILE)
    # Declared in ~/.claude/metrics/config.json; empty when there is no
    # config, which leaves normalize_project_paths exactly as it was.
    project_roots = mr.load_project_roots()
    # Config first, then the machine's locale, then English - see
    # resolve_language's own docstring. Resolved once, here (the expensive,
    # once-per-launch half of the load), and carried in the returned dict so
    # apply_period can copy it into every re-windowed view without
    # re-resolving it on each "p" press.
    lang = mr.resolve_language(mr.load_config(), os.environ)
    # Captured before dedupe_sessions discards every snapshot but one per
    # session_id - expand_records_to_daily_deltas needs the FULL raw
    # history to correctly spread a long-running session's token growth
    # across the days it actually happened (2026-08-06 fix), instead of
    # dumping it all onto the session's fixed start day.
    raw_session_records = mr.normalize_project_paths(list(records), project_roots)
    raw_session_records = mr.filter_records(raw_session_records, tag=args.tag, project=args.project)
    records = mr.dedupe_sessions(records)
    records = mr.normalize_project_paths(records, project_roots)
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
    # No -n cap on the session list (2026-07-21, mirrors dashboard_html.py's
    # --html branch): a Textual Tree already scrolls natively, so there's no
    # "table overflows the terminal" problem an artificial cap would be
    # solving here - only the plain terminal-table mode in dashboard_html.py
    # still defaults to -n=20. --limit/-n is kept as a CLI flag for anyone
    # who wants to bound it explicitly, but it's no longer applied by default.
    if args.limit is not None:
        records = records[-args.limit :]

    # Parallelized labeling, same pattern as dashboard_html.py's --html
    # branch. subagent_records is already deduped by agent_id above the
    # growing-snapshot problem dedupe_subagents fixes - preferring a
    # source-bearing duplicate as representative here is a separate, smaller
    # concern: two THREADS racing the same cache key could otherwise
    # interleave a real label with a failed one, and negative-caching would
    # then permanently lock in whichever thread wrote last.
    # Subagent paths go through the same normalization as session paths
    # (2026-09-21). They did not, until a legend entry turned out to be a
    # subagent's literal working directory - subagent deltas feed the trend
    # chart below, so their raw cwds reached the legend unchanged. Run over
    # the subagent stream on its own rather than over a merged list, the
    # same way the raw/deduped session streams are normalized separately.
    raw_subagent_records = mr.normalize_project_paths(
        mr.load_records(mr.SUBAGENTS_FILE), project_roots
    )
    subagent_records = mr.dedupe_subagents(raw_subagent_records)
    # See expand_records_to_daily_deltas's docstring: used ONLY by the
    # trend chart, so every other view (composition legend, project
    # rankings, per-row totals) keeps using the regular deduped/folded
    # records untouched.
    daily_chart_records = mr.expand_records_to_daily_deltas(
        raw_session_records
    ) + mr.expand_records_to_daily_deltas(raw_subagent_records)
    agent_label_cache = mr.load_agent_label_cache()
    unique_agents: dict[str, dict[str, Any]] = {}
    for subagent in subagent_records:
        key = subagent.get("agent_id")
        if key is None:
            continue
        # DEAD BY DRIFT, and this is the TWIN of the clause phase 3c
        # documented in dashboard_html.py (see its main(), same two loops,
        # same reasoning). Confirmed here by mutation on 2026-09-02:
        # inverting the entire second arm of BOTH loops leaves all 577
        # tests green. 77f49d5 (2026-07-18) added the source-bearing-
        # duplicate preference when neither list had any dedup upstream -
        # correct at the time. 328d5d6 (2026-07-21) then added
        # dedupe_subagents/dedupe_sessions ABOVE these loops, which already
        # guarantee one record per key, so `existing` is always None the one
        # time each key is seen.
        #
        # Kept rather than deleted, for the same reason as the twin: the
        # clause becomes necessary again the moment that upstream dedup is
        # removed or reordered, and deleting it now would replant the exact
        # regression this comment exists to explain.
        #
        # `no branch`, NOT `no cover`: what is unreachable is the false arc
        # of the `if`, not the statement. `no cover` on an `if` line
        # excludes the body too, which would drop the always-executed
        # assignment below out of the denominator and hide it from phase 4's
        # 100% gate forever.
        #
        # That this went undiscovered here for six weeks after being found
        # and documented in the twin is itself the finding recorded in
        # docs/superpowers/BACKLOG.md about these two dashboards duplicating
        # logic: the defect was never propagated, because the code was.
        existing = unique_agents.get(key)
        if existing is None or (  # pragma: no branch - unreachable, see comment above
            not existing.get("agent_transcript_path") and subagent.get("agent_transcript_path")
        ):
            unique_agents[key] = subagent
    # max_workers=1 (serial), matching dashboard_html.py's own fix
    # (2026-07-20/23): each worker spawns a full `claude -p` CLI
    # subprocess, not a lightweight thread task - 8 concurrent ones
    # spiked load average past 50 and froze the machine during a
    # cold-cache backfill. label_budget caps how many brand-new labels
    # (real API calls) this run generates before it stops (2026-07-23,
    # same reasoning as dashboard_html.py's --label-budget: unbounded
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
            executor.map(
                lambda k: mr.get_agent_label(unique_agents[k], agent_label_cache, label_budget),
                agent_keys,
            )
        )
    label_by_agent_id = dict(zip(agent_keys, agent_labels, strict=True))
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
    unique_sessions: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record.get("session_id")
        if key is None:
            continue
        # Same dead-by-drift clause as unique_agents above, same evidence,
        # same ruling - dedupe_sessions runs upstream of this loop.
        existing = unique_sessions.get(key)
        if existing is None or (  # pragma: no branch - unreachable, see comment above
            not (existing.get("internal_name") or existing.get("transcript_path"))
            and (record.get("internal_name") or record.get("transcript_path"))
        ):
            unique_sessions[key] = record
    session_keys = list(unique_sessions.keys())
    with ThreadPoolExecutor(max_workers=1) as executor:
        task_labels = list(
            executor.map(
                lambda k: mr.get_task_label(unique_sessions[k], label_cache, label_budget),
                session_keys,
            )
        )
    label_by_session_id = dict(zip(session_keys, task_labels, strict=True))
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
        "lang": lang,
    }


def apply_period(
    base: dict[str, Any], period_id: str, mr: ModuleType, now: datetime | None = None
) -> dict[str, Any]:
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
        "lang": base["lang"],
        "period_id": period_id,
        # The period-independent source this view was derived from, carried
        # along so any holder of a view can re-window it without reloading.
        # Kept inside the dict rather than added as a load_data return value
        # or an app constructor argument so that every existing caller -
        # main(), the headless drivers under .superpowers/ - keeps working
        # unchanged while still getting the fast path.
        "base": base,
    }


def load_data(args: argparse.Namespace, mr: ModuleType | None = None) -> dict[str, Any]:
    """Loads and windows in one call, at the default period.

    Kept for callers that just want data for one period (main(), the
    headless drivers, ad-hoc scripts). Anything that re-windows repeatedly
    must call load_base_data once and apply_period per period instead;
    calling this in a loop is precisely the defect it was split to fix.
    """
    mr = mr or import_metrics_report()
    return apply_period(load_base_data(args, mr=mr), mr.DEFAULT_PERIOD_ID, mr)


def build_session_tree(
    records: list[dict[str, Any]],
    subagents_by_session: dict[str, list[dict[str, Any]]],
    mr: ModuleType,
    *,
    lang: str,
) -> Tree[Any]:
    group_colors = mr.assign_group_colors(records)
    groups = mr.build_project_groups(records, group_colors)
    tree: Tree[Any] = Tree(mr.t("tab.sessions", lang))
    tree.show_root = False
    for group in groups:
        project_path = group["project"]
        # Omit zero-token sessions (2026-07-21, ported from
        # dashboard_html.py's render_html) - a session that never recorded
        # any input/output tokens (aborted before the first turn, etc.) is
        # dead weight in the tree. A group whose every session is zero-token
        # is skipped entirely rather than left as an empty, expandable node.
        visible_records = [r for r in group["records"] if mr.record_token_total(r) > 0]
        if not visible_records:
            continue
        label = mr.project_display_label(project_path, lang=lang)
        total_tokens = sum(mr.record_token_total(r) for r in visible_records)
        group_node = tree.root.add(
            f"{label} ({mr.tn('sessions', len(visible_records), lang)} · "
            f"{mr.format_tokens(total_tokens)} tokens)",
            expand=False,
        )
        for record in visible_records:
            session_id = record.get("session_id") or ""
            date_str = mr.format_date(record.get("timestamp_start"), lang=lang)
            task_label = record.get("task_label") or "-"
            tokens = mr.format_tokens(mr.record_token_total(record))
            duration = record.get("duration_seconds")
            duration_str = f"{duration:.0f}s" if duration is not None else "-"
            # Subagent count badge (2026-07-21, ported from
            # dashboard_html.py's render_session_row/subagent_badge) - lets
            # you tell which sessions dispatched subagents right in the
            # tree, without expanding every single one's detail panel.
            # Counts only subagents that actually recorded tokens, same
            # filter render_html applies to its own badge.
            session_subagents = subagents_by_session.get(session_id, [])
            visible_subagent_count = sum(
                1 for s in session_subagents if mr.record_token_total(s) > 0
            )
            subagent_badge = f" 🤖{visible_subagent_count}" if visible_subagent_count else ""
            row = (
                f"{session_id[:12]:<14}{date_str:<14}{task_label[:28]:<30}"
                f"{tokens:>8}   {duration_str:>7}{subagent_badge}"
            )
            group_node.add_leaf(row, data={"session_id": session_id})
    return tree


class SessionDetailPanel(VerticalScroll):
    def __init__(self, mr: ModuleType, *, lang: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.mr = mr
        self.lang = lang
        self._body = Static("")

    def compose(self) -> ComposeResult:
        yield self._body

    def clear(self) -> None:
        self._body.update("")

    def show_session(self, session_id: str, subagents: list[dict[str, Any]]) -> None:
        lines = [f"[bold]{session_id}[/bold]", ""]
        if not subagents:
            lines.append(f"[dim]{self.mr.t('detail.no_subagents', self.lang)}[/dim]")
        else:
            lines.append(
                f"[bold]{self.mr.t('detail.subagents_title', self.lang, n=len(subagents))}[/bold]"
            )
            lines.append(
                f"{self.mr.t('col.task', self.lang):<40}{'tokens':>12}"
                f"{'tools':>8}{self.mr.t('col.duration', self.lang):>10}"
            )
            for i, subagent in enumerate(subagents):
                agent_type = subagent.get("agent_type") or "-"
                agent_label = subagent.get("agent_label") or "-"
                in_tok = self.mr.format_tokens(subagent.get("input_tokens", 0))
                out_tok = self.mr.format_tokens(subagent.get("output_tokens", 0))
                duration = subagent.get("duration_seconds")
                duration_str = f"{duration:.0f}s" if duration is not None else "-"
                task = f"{agent_type} — {agent_label}"[:38]
                style = "on #202020" if i % 2 == 1 else ""
                row = (
                    f"{task:<40}{in_tok}/{out_tok:<8}"
                    f"{subagent.get('tool_call_count', 0):>8}{duration_str:>10}"
                )
                lines.append(f"[{style}]{row}[/{style}]" if style else row)
        self._body.update("\n".join(lines))


class QuotaSummary(Static):
    """A separate widget from CompositionLegend, deliberately - not a
    second line inside it. CompositionLegend.on_click hit-tests a click
    purely by event.x against self.size.width // 4 (no event.y check at
    all), so a second line in the SAME widget would both throw off that
    math (its width would grow to whatever this line's own text needs)
    and make a click on this line get misread as a click on a chip."""

    def __init__(self, totals: dict[str, int], mr: ModuleType, *, lang: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.totals = totals
        self.mr = mr
        self.lang = lang

    def render(self) -> str:
        quota_input, quota_output = self.mr.compute_quota_totals(self.totals)
        line: str = self.mr.t(
            "quota_line",
            self.lang,
            input=self.mr.format_tokens(quota_input),
            output=self.mr.format_tokens(quota_output),
        )
        return line


class CompositionLegend(Static):
    highlighted: reactive[set[str] | None] = reactive(None)

    class Highlight(Message):
        def __init__(self, series: set[str] | None) -> None:
            self.series = series
            super().__init__()

    SERIES: ClassVar[list[tuple[str, str]]] = [
        ("series-1", "input"),
        ("series-2", "output"),
        ("series-3", "cache read"),
        ("series-4", "cache creation"),
    ]

    def __init__(self, totals: dict[str, int], mr: ModuleType, *, lang: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.totals = totals
        self.mr = mr
        self.lang = lang

    def render(self) -> str:
        chips = []
        for series, name in self.SERIES:
            total = self.totals.get(series, 0)
            formatted = self.mr.format_tokens(total)
            is_highlighted = self.highlighted is not None and series in self.highlighted
            style = "bold reverse" if is_highlighted else ""
            chip = f"[{style}]{name} · {formatted}[/{style}]" if style else f"{name} · {formatted}"
            chips.append(chip)
        return "   ".join(chips)

    def on_click(self, event: Any) -> None:
        x = event.x
        chip_width = self.size.width // 4 if self.size.width else 0
        index = min(x // max(chip_width, 1), 3) if chip_width else 0
        series = self.SERIES[index][0]
        current: set[str] = self.highlighted or set()
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


def compute_pane_projects(
    mr: ModuleType, chart_records: list[dict[str, Any]]
) -> tuple[list[str | None], bool]:
    """Rank projects by total tokens in chart_records, grouped by color_group_key.

    Returns (top_keys, has_other): top_keys is the ordered list of the
    PANE_MAX_PROJECTS highest-token project keys; has_other is True when
    there are more distinct projects than that, meaning callers should
    add one more "outros" bucket for everything not in top_keys.
    """
    totals: dict[str | None, int] = {}
    for r in chart_records:
        key = mr.color_group_key(r.get("project_path"))
        totals[key] = totals.get(key, 0) + mr.record_token_total(r)
    ordered = sorted(totals.keys(), key=lambda k: totals[k], reverse=True)
    top_keys = ordered[:PANE_MAX_PROJECTS]
    has_other = len(ordered) > PANE_MAX_PROJECTS
    return top_keys, has_other


def pane_series_label(mr: ModuleType, key: str | None, *, lang: str) -> str:
    if key == PANE_OTHER_KEY:
        others: str = mr.t("others", lang)
        return others
    label: str = mr.project_display_label(key, lang=lang)
    return label


def draw_stacked_bars(
    plt: Any,
    xlabels: list[str],
    series_lists: list[list[int]],
    series_labels: list[str],
    colors: list[str],
) -> None:
    """A stacked bar chart, drawn as layers instead of with plotext's own
    stacked_bar.

    stacked_bar leaves a bar floating one row above the zero line whenever
    its FIRST series is 0 there - measured against the installed plotext on
    2026-09-22. The old workaround set that series to 1 token, and plotext
    draws any nonzero value as at least one full row, so the bar's base came
    out in the colour, and under the legend name, of a series that had no
    tokens on that day: the model mode painted the top model under every day
    older than the per-model map.

    Here each series is a plain bar from zero up to its CUMULATIVE total,
    drawn from the top of the stack down so every lower layer paints over
    the one above it. Every layer starts at zero, so nothing floats. A
    series is simply not drawn where it is 0 - which is also required,
    because plotext paints a zero-valued bar's base cell with the background
    and would erase what is under it. Bars are placed by number, not by
    label, so a layer that skips a day still lines up with the others.
    """
    xs = list(range(1, len(xlabels) + 1))
    cumulative: list[list[int]] = []
    running = [0] * len(xs)
    for series in series_lists:
        running = [a + b for a, b in zip(running, series, strict=True)]
        cumulative.append(running)
    # An invisible, unlabelled layer of zero-height bars at EVERY position,
    # drawn first. plotext takes the x range from the bars actually drawn,
    # and every real layer skips its zero days - so without this, a bucket
    # with no tokens at either edge fell off the axis (taking today's
    # in-progress label with it), and a window with no tokens at all drew
    # a bare frame. A zero bar only paints its base cell with the
    # background, and every real layer drawn after it paints over that.
    plt.bar(xs, [0] * len(xs), label=None, color=None, width=_bar_width_for(xs))
    for i in reversed(range(len(series_lists))):
        shown = [j for j, value in enumerate(series_lists[i]) if value > 0]
        if shown:
            positions = [xs[j] for j in shown]
            plt.bar(
                positions,
                [cumulative[i][j] for j in shown],
                label=series_labels[i],
                color=colors[i],
                width=_bar_width_for(positions),
            )
    plt.xticks(xs, xlabels)


# plotext's own default bar width (plotext/_default.py: bar_width = 4 / 5).
BAR_WIDTH = 0.8


def _bar_width_for(positions: list[int]) -> float:
    """The `width` to pass so these bars come out BAR_WIDTH wide.

    plotext does not draw `width` as given: it multiplies it by the average
    spacing of the x values in that one call (plotext/_utility.py, bars():
    half = width / 2 * (max - min) / (n - 1) whenever n > 1). A series drawn
    without its zero days has wider spacing, so without this its bars come
    out wider too - three times as wide for a series present on the first
    and last of four days, painting its colour across the two days between,
    where it has nothing. Found in review on 2026-09-23. Dividing by that
    same average spacing cancels it exactly, even when the spacing is
    irregular, because plotext uses the average and so does this.
    """
    if len(positions) < 2:
        return BAR_WIDTH
    return BAR_WIDTH / ((positions[-1] - positions[0]) / (len(positions) - 1))


class TrendChart(PlotextPlot):
    # PlotextPlot is not focusable by default (2026-08-18): it needs to be
    # so action_cycle_period has something inside the Panorama pane to land
    # focus on after "p", the same way Tree already anchors the Sessões
    # pane. It is the widget the user actually meant by "o gráfico".
    can_focus = True

    SERIES_KEYS: ClassVar[list[str]] = ["series-1", "series-2", "series-3", "series-4"]
    SERIES_COLORS: ClassVar[list[str]] = ["green", "blue", "magenta", "orange"]

    def __init__(
        self,
        mr: ModuleType,
        chart_records: list[dict[str, Any]],
        mode: str = "pane",
        *,
        lang: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.mr = mr
        self.chart_records = chart_records
        self.mode = mode
        self.lang = lang
        self.highlighted: set[str] | None = None
        self.project_highlighted: set[str | None] | None = None

    def on_mount(self) -> None:
        self.render_chart()

    # The three ways the same quantity - a day's total tokens - gets sliced.
    # "t" walks them in this order and wraps, so a third press returns to
    # where it started rather than stranding the user in the last mode.
    MODES: ClassVar[list[str]] = ["pane", "tokens", "model"]

    def toggle_mode(self) -> None:
        self.mode = self.MODES[(self.MODES.index(self.mode) + 1) % len(self.MODES)]
        self.render_chart()

    def set_highlight(self, series: set[str] | None) -> None:
        self.highlighted = series
        if self.mode == "tokens":
            self.render_chart()

    def set_project_highlight(self, projects: set[str | None] | None) -> None:
        self.project_highlighted = projects
        if self.mode == "pane":
            self.render_chart()

    def render_chart(self, now: datetime | None = None) -> None:
        # aggregate_by_day returns (granularity, days) since
        # dashboard_html.py's adaptive-granularity feature (2026-07-21) -
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
        labels = [
            self.mr.tick_label(granularity, datetime.strptime(d["day"], parse_fmt), self.lang)
            for d in days
        ]
        # The last bucket is always a fraction of a full one while it's
        # still running (backlog, 2026-09-02: "the newest bar always reads
        # as a decline") - annotated on its own label rather than greyed
        # out like dashboard_html.py's SVG rects are: each series is drawn
        # in one colour across every bucket (see draw_stacked_bars).
        partial_frac = self.mr.partial_bucket_fraction(
            granularity, days[-1]["day"], now or datetime.now().astimezone()
        )
        if partial_frac is not None:
            labels[-1] += f" · {round(partial_frac * 100)}%"
        if self.mode == "pane":
            top_keys, has_other = compute_pane_projects(self.mr, self.chart_records)
            series_keys = list(top_keys) + ([PANE_OTHER_KEY] if has_other else [])
            self.pane_series_keys = series_keys
            series_data: dict[str | None, list[int]] = {k: [] for k in series_keys}
            for d in days:
                day_totals = dict.fromkeys(series_keys, 0)
                for r in d["records"]:
                    r_key = self.mr.color_group_key(r.get("project_path"))
                    bucket = r_key if r_key in top_keys else PANE_OTHER_KEY
                    day_totals[bucket] += self.mr.record_token_total(r)
                for k in series_keys:
                    series_data[k].append(day_totals[k])
            series_lists = [series_data[k] for k in series_keys]
            series_labels = [pane_series_label(self.mr, k, lang=self.lang) for k in series_keys]
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
            draw_stacked_bars(self.plt, labels, series_lists, series_labels, color)
            self.plt.title(self.mr.t("chart_title.pane", self.lang))
            max_total = max(
                (sum(series_data[k][i] for k in series_keys) for i in range(len(days))), default=0
            )
        elif self.mode == "model":
            # Every model seen anywhere in the window gets a band in every
            # day, so a model's colour means the same thing across the whole
            # chart instead of shifting with each day's cast.
            all_models: dict[str, int] = {}
            for d in days:
                for model, amount in self.mr.compute_model_totals(d["records"])[0].items():
                    all_models[model] = all_models.get(model, 0) + amount
            model_keys = sorted(all_models, key=lambda m: -all_models[m])
            per_day: list[dict[str, int]] = []
            unattributed: list[int] = []
            for d in days:
                by_model, remainder = self.mr.compute_model_totals(d["records"])
                per_day.append(by_model)
                unattributed.append(remainder)
            # The unattributed band sits LAST so the models stay adjacent to
            # one another, and carries the "other" colour it shares with the
            # pane mode's overflow bucket - both mean "everything else".
            series_lists = [[day.get(m, 0) for day in per_day] for m in model_keys]
            series_lists.append(unattributed)
            series_labels = [self.mr.model_display_label(m) for m in model_keys]
            series_labels.append(self.mr.t("no_model", self.lang))
            color = [
                PANE_COLORS[i] if i < len(PANE_COLORS) else PANE_OTHER_COLOR
                for i in range(len(model_keys))
            ]
            color.append(PANE_OTHER_COLOR)
            day_totals_list = [sum(series[i] for series in series_lists) for i in range(len(days))]
            draw_stacked_bars(self.plt, labels, series_lists, series_labels, color)
            self.plt.title(self.mr.t("chart_title.model", self.lang))
            max_total = max(day_totals_list, default=0)
        else:
            input_totals = [sum(r.get("input_tokens", 0) or 0 for r in d["records"]) for d in days]
            output_totals = [
                sum(r.get("output_tokens", 0) or 0 for r in d["records"]) for d in days
            ]
            cache_read_totals = [
                sum(r.get("cache_read_tokens", 0) or 0 for r in d["records"]) for d in days
            ]
            cache_creation_totals = [
                sum(r.get("cache_creation_tokens", 0) or 0 for r in d["records"]) for d in days
            ]
            if self.highlighted:
                color = [
                    self.SERIES_COLORS[i] if key in self.highlighted else "gray"
                    for i, key in enumerate(self.SERIES_KEYS)
                ]
            else:
                color = self.SERIES_COLORS
            draw_stacked_bars(
                self.plt,
                labels,
                [input_totals, output_totals, cache_read_totals, cache_creation_totals],
                ["input", "output", "cache read", "cache creation"],
                color,
            )
            self.plt.title(self.mr.t("chart_title.tokens", self.lang))
            max_total = max(
                (
                    input_totals[i]
                    + output_totals[i]
                    + cache_read_totals[i]
                    + cache_creation_totals[i]
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
    highlighted: reactive[set[str | None] | None] = reactive(None)

    class Highlight(Message):
        def __init__(self, projects: set[str | None] | None) -> None:
            self.projects = projects
            super().__init__()

    def __init__(
        self, mr: ModuleType, chart_records: list[dict[str, Any]], *, lang: str, **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self.mr = mr
        self.chart_records = chart_records
        self.lang = lang

    def _series_keys(self) -> list[str | None]:
        top_keys, has_other = compute_pane_projects(self.mr, self.chart_records)
        return list(top_keys) + ([PANE_OTHER_KEY] if has_other else [])

    def render(self) -> str:
        series_keys = self._series_keys()
        if not series_keys:
            return f"[dim]{self.mr.t('no_data', self.lang)}[/dim]"
        chips = []
        for key in series_keys:
            label = pane_series_label(self.mr, key, lang=self.lang)
            is_highlighted = self.highlighted is not None and key in self.highlighted
            style = "bold reverse" if is_highlighted else ""
            chip = f"[{style}]{label}[/{style}]" if style else label
            chips.append(chip)
        return "   ".join(chips)

    def on_click(self, event: Any) -> None:
        series_keys = self._series_keys()
        if not series_keys:
            return
        chip_width = self.size.width // len(series_keys) if self.size.width else 0
        index = min(event.x // max(chip_width, 1), len(series_keys) - 1) if chip_width else 0
        key = series_keys[index]
        current: set[str | None] = self.highlighted or set()
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

    def __init__(
        self,
        mr: ModuleType,
        chart_records: list[dict[str, Any]],
        project_first_seen: dict[str, str],
        project_last_seen: dict[str, str],
        open_projects: set[str],
        *,
        lang: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.mr = mr
        self.chart_records = chart_records
        self.project_first_seen = project_first_seen
        self.project_last_seen = project_last_seen
        self.open_projects = open_projects
        self.lang = lang

    def toggle_more(self) -> None:
        self.expanded = not self.expanded

    def render(self) -> str:
        ordered, totals, max_total = self.mr.compute_project_totals(
            self.chart_records, self.project_last_seen
        )
        # Omit zero-token projects (2026-07-21, ported from
        # dashboard_html.py's render_project_totals_chart) - a project with
        # nothing to show is just dead weight in this ranking.
        ordered = [p for p in ordered if totals.get(p, 0) > 0]
        if not ordered:
            return f"[dim]{self.mr.t('no_data', self.lang)}[/dim]"

        max_total = max_total or 1
        visible = ordered[:10]
        hidden = ordered[10:]
        shown = visible + (hidden if self.expanded else [])

        lines = [f"[bold]{self.mr.t('project_totals_title', self.lang)}[/bold]", ""]
        for project in shown:
            total = totals[project]
            label = self.mr.project_display_label(project, lang=self.lang)
            bar_len = int((total / max_total) * 30)
            is_open = project in self.open_projects
            bar_char = "█" if is_open else "▓"
            days_running = self.mr.project_days_running(
                self.project_first_seen, project, lang=self.lang
            )
            formatted_total = self.mr.format_tokens(total)
            lines.append(f"{label[:24]:<26}{bar_char * bar_len:<32}{formatted_total}{days_running}")

        if hidden:
            toggle_label = (
                self.mr.t("fewer", self.lang)
                if self.expanded
                else f"▸ {self.mr.tn('past_projects', len(hidden), self.lang)}"
            )
            lines.append("")
            lines.append(f"[dim]{toggle_label}[/dim]")

        lines.append("")
        lines.append(f"[dim]{self.mr.t('legend.open_session_tui', self.lang)}[/dim]")
        return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Terminal (TUI) dashboard for Claude Code session metrics."
    )
    parser.add_argument("--tag", default=None, help="Filter by session tag")
    parser.add_argument("--project", default=None, help="Filter by project path")
    # No default cap (2026-07-21): the Tree widget scrolls natively, so unlike
    # dashboard_html.py's plain terminal-table mode there's no overflow
    # problem a default -n=20 would be solving here. Pass -n explicitly to
    # bound it anyway.
    parser.add_argument(
        "-n",
        "--limit",
        type=int,
        default=None,
        help="Number of most recent sessions to show (unbounded by default)",
    )
    parser.add_argument(
        "--days", type=int, default=None, help="Only include sessions from the last N days"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Accepted for compatibility and now the default: the full history is shown "
            "unless --days is given"
        ),
    )
    parser.add_argument(
        "--label-budget",
        type=int,
        default=0,
        help=(
            "Max NEW labels (real claude -p calls) to generate this run; already-cached "
            "labels are free and unlimited. Use 0 for none, a negative number for unlimited."
        ),
    )
    return parser


def legend_visibility(mode: str) -> dict[str, bool]:
    """Which legend widgets belong to a chart mode. The "model" mode has
    none: its bands are labelled by plotext's own legend, since nothing
    highlights them the way the other two modes' clickable chips do."""
    return {
        "quota": mode == "tokens",
        "composition": mode == "tokens",
        "projects": mode == "pane",
    }


class MetricsTUIApp(App[None]):
    # Without this Textual titles the terminal window after the class, so
    # it read "MetricsTUIApp" - a class name, in the one place a user reads
    # a product name.
    TITLE = "tokboard"

    CSS = """
    Horizontal { height: 1fr; }
    Tree { width: 62%; }
    SessionDetailPanel { width: 1fr; border-left: solid $panel; padding: 1; }
    .backfill-note { color: $text-muted; height: auto; padding: 0 1; }
    """
    # Bound per instance in __init__, because their descriptions are in
    # the run's language and a class attribute is fixed before any app
    # exists.
    BINDINGS: ClassVar[list[BindingType]] = []
    _KEYS: ClassVar[tuple[tuple[str, str, str], ...]] = (
        ("q", "quit", "key.quit"),
        ("t", "toggle_chart_mode", "key.chart_mode"),
        ("m", "toggle_more_projects", "key.more_projects"),
        ("g", "toggle_tab", "key.toggle_tab"),
        ("p", "cycle_period", "key.cycle_period"),
    )

    # `args` is required, not an args=None default (2026-08-13): the app
    # hands it straight to load_data on every period cycle, so a None here
    # does not fail at construction - it raises AttributeError deep inside
    # load_data the first time someone presses "p".
    def __init__(
        self, mr: ModuleType, data: dict[str, Any], args: argparse.Namespace, **kwargs: Any
    ) -> None:
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
        self.chart_mode = "pane"
        for key, action, message in self._KEYS:
            self.bind(key, action, description=self.mr.t(message, self.data["lang"]))

    def compose(self) -> ComposeResult:
        lang = self.data["lang"]
        yield Static(
            self.mr.describe_backfill_state(self.mr.load_backfill_state(), lang=lang),
            classes="backfill-note",
        )
        with TabbedContent(initial=self.active_tab_id):
            with TabPane(self.mr.t("tab.sessions", lang), id="tab-sessions"), Horizontal():
                yield build_session_tree(
                    self.data["records"], self.data["subagents_by_session"], self.mr, lang=lang
                )
                yield SessionDetailPanel(self.mr, lang=lang)
            with TabPane(self.mr.t("tab.panorama", lang), id="tab-panorama"):
                totals = self.mr.compute_token_type_totals(self.data["records"])
                # Visibility is set here, as each legend is built, rather than
                # fixed up afterwards: "p" recomposes every widget, and a fix-up
                # queued behind the recompose could leave one frame with all
                # three legends showing (2026-09-23 review).
                shown = legend_visibility(self.chart_mode)
                quota = QuotaSummary(totals, self.mr, lang=lang)
                quota.display = shown["quota"]
                yield quota
                composition = CompositionLegend(totals, self.mr, lang=lang)
                composition.display = shown["composition"]
                yield composition
                projects = ProjectLegend(self.mr, self.data["chart_records"], lang=lang)
                projects.display = shown["projects"]
                yield projects
                # In the mode the reader last picked, not the default: "p"
                # recomposes every widget, and a fresh chart would silently
                # drop the slicing they chose (2026-09-22).
                yield TrendChart(
                    self.mr, self.data["daily_chart_records"], mode=self.chart_mode, lang=lang
                )
                yield ProjectRankingList(
                    self.mr,
                    self.data["chart_records"],
                    self.data["project_first_seen"],
                    self.data["project_last_seen"],
                    self.data["open_projects"],
                    lang=lang,
                )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Tree).focus()

    def _sync_legend_visibility(self) -> None:
        shown = legend_visibility(self.query_one(TrendChart).mode)
        self.query_one(QuotaSummary).display = shown["quota"]
        self.query_one(CompositionLegend).display = shown["composition"]
        self.query_one(ProjectLegend).display = shown["projects"]

    def on_tree_node_selected(self, event: Any) -> None:
        session_data = event.node.data
        if not session_data:
            return
        session_id = session_data["session_id"]
        subagents = self.data["subagents_by_session"].get(session_id, [])
        self.query_one(SessionDetailPanel).show_session(session_id, subagents)

    def action_toggle_chart_mode(self) -> None:
        chart = self.query_one(TrendChart)
        chart.toggle_mode()
        self.chart_mode = chart.mode
        self._sync_legend_visibility()

    def action_toggle_more_projects(self) -> None:
        self.query_one(ProjectRankingList).toggle_more()

    def action_toggle_tab(self) -> None:
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

    def on_composition_legend_highlight(self, event: CompositionLegend.Highlight) -> None:
        self.query_one(TrendChart).set_highlight(event.series)

    def on_project_legend_highlight(self, event: ProjectLegend.Highlight) -> None:
        self.query_one(TrendChart).set_project_highlight(event.projects)

    def action_cycle_period(self) -> None:
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
        lang = self.data["lang"]
        self.notify(self.mr.t("notify.period", lang, name=self.mr.period_label(next_id, lang=lang)))

    def _restore_focus_after_period_cycle(self) -> None:
        """Focus a widget that actually belongs to the pane recompose
        reopened on (self.active_tab_id), mirroring the pattern
        action_toggle_tab already uses for the sessions tab."""
        if self.active_tab_id == "tab-sessions":
            self.query_one(Tree).focus()
        else:
            self.query_one(TrendChart).focus()


def main() -> None:
    args = build_arg_parser().parse_args()
    mr = import_metrics_report()
    data = load_data(args, mr=mr)
    app = MetricsTUIApp(mr, data, args=args)
    app.run()


if __name__ == "__main__":
    main()
