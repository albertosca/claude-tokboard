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
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, ClassVar

from rich.cells import cell_len
from rich.markup import escape
from rich.text import Text
from textual.app import App, ComposeResult, RenderResult
from textual.binding import BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Static, TabbedContent, TabPane, Tree
from textual_plotext import PlotextPlot

if TYPE_CHECKING:
    # Only for annotations below (model_slots' value type): the real
    # import stays the runtime-injected ModuleType parameter every
    # function here already takes, resolved via import_metrics_report()
    # after sys.path is set up, not this static one - mypy_path="lib" in
    # pyproject.toml lets mypy resolve this without that path munging.
    import metrics_core


def import_metrics_report() -> ModuleType:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
    import metrics_core

    return metrics_core


def load_base_data(
    arguments: argparse.Namespace, metrics_core_module: ModuleType | None = None
) -> dict[str, Any]:
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
    dataset of a few thousand sessions - long enough that the tmux pane looked hung and
    had to be killed. See tests/test_tui_responsiveness.py for the
    regression test that keeps that work out of the keypress path.
    """
    metrics_core_module = metrics_core_module or import_metrics_report()

    # Dedupe + normalize the same way dashboard_html.py's main() does
    # before ANYTHING else touches records (2026-07-21) - the Stop hook
    # appends a fresh cumulative snapshot every turn, so one session_id can
    # have dozens of duplicate rows with growing token counts (found
    # inflating a session's totals many times over upstream). normalize_project_paths then rolls a
    # subdirectory cwd up to its real project root and folds scratchpad/tmp
    # cwds into "sem projeto" - see that function's docstring in
    # dashboard_html.py for the fan-out safeguard that keeps it from
    # collapsing unrelated projects into a shared container directory.
    records = metrics_core_module.load_records(metrics_core_module.METRICS_FILE)
    # Read once (backlog #11: this used to also call metrics_core_module.load_project_roots(),
    # a second read of the same file) and derive everything the config can
    # give from this one dict.
    config = metrics_core_module.load_config()
    # Declared in ~/.claude/metrics/config.json; empty when there is no
    # config, which leaves normalize_project_paths exactly as it was.
    project_roots = metrics_core_module.project_roots_from(config)
    # Config first, then the machine's locale, then English - see
    # resolve_language's own docstring. Resolved once, here (the expensive,
    # once-per-launch half of the load), and carried in the returned dict so
    # apply_period can copy it into every re-windowed view without
    # re-resolving it on each "p" press.
    lang = metrics_core_module.resolve_language(config, os.environ)
    # Captured before dedupe_sessions discards every snapshot but one per
    # session_id - expand_records_to_daily_deltas needs the FULL raw
    # history to correctly spread a long-running session's token growth
    # across the days it actually happened (2026-08-06 fix), instead of
    # dumping it all onto the session's fixed start day.
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

    # The period is the single authority on time (2026-08-13). This used to
    # default to a 30-day window applied through filter_by_days BEFORE the
    # period filter ran - and filter_by_days keys on timestamp_start, the
    # exact start-date rule this phase replaced with span overlap. The
    # result was a dashboard that lied: "tudo" left out a fifth of the sessions,
    # "mes" and "tudo" were byte-identical on four of five surfaces, and
    # TrendChart disagreed with the rest of its own screen because
    # daily_chart_records was captured before the cap. --days stays as an
    # explicit user override, and --all is still accepted (it now names what
    # already happens by default) so existing invocations keep working.
    effective_days = arguments.days

    records = metrics_core_module.filter_by_days(records, effective_days)
    chart_records = records
    # No -n cap on the session list (2026-07-21, mirrors dashboard_html.py's
    # --html branch): a Textual Tree already scrolls natively, so there's no
    # "table overflows the terminal" problem an artificial cap would be
    # solving here - only the plain terminal-table mode in dashboard_html.py
    # still defaults to -n=20. --limit/-n is kept as a CLI flag for anyone
    # who wants to bound it explicitly, but it's no longer applied by default.
    if arguments.limit is not None:
        records = records[-arguments.limit :]

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
    raw_subagent_records = metrics_core_module.normalize_project_paths(
        metrics_core_module.load_records(metrics_core_module.SUBAGENTS_FILE), project_roots
    )
    subagent_records = metrics_core_module.dedupe_subagents(raw_subagent_records)
    # See expand_records_to_daily_deltas's docstring: used ONLY by the
    # trend chart, so every other view (composition legend, project
    # rankings, per-row totals) keeps using the regular deduped/folded
    # records untouched.
    daily_chart_records = metrics_core_module.expand_records_to_daily_deltas(
        raw_session_records
    ) + metrics_core_module.expand_records_to_daily_deltas(raw_subagent_records)
    agent_label_cache = metrics_core_module.load_agent_label_cache()
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
    # 1.86s with a serial stand-in, over five runs on a dataset of a few
    # thousand sessions - 0.21s, one time, while the app is already starting. That
    # buys keeping the structure this comment documents, which is the thing
    # that makes "serial ON PURPOSE" visible to whoever next touches the
    # budget>0 path where the subprocesses are real. What actually costs
    # here is neither the pool nor the cache: sessions that miss the
    # cache and, since --label-budget 0 deliberately declines to cache them
    # so a later run can pick them up, each load re-reads their transcripts
    # (~1.2s). That is unchanged by this split, and no longer on the
    # keypress path.
    label_budget = metrics_core_module.LabelBudget(
        None if arguments.label_budget < 0 else arguments.label_budget
    )
    agent_keys = list(unique_agents.keys())
    with ThreadPoolExecutor(max_workers=1) as executor:
        agent_labels = list(
            executor.map(
                lambda agent_key: metrics_core_module.get_agent_label(
                    unique_agents[agent_key], agent_label_cache, label_budget, lang=lang
                ),
                agent_keys,
            )
        )
    label_by_agent_id = dict(zip(agent_keys, agent_labels, strict=True))
    for subagent in subagent_records:
        subagent["agent_label"] = label_by_agent_id.get(subagent.get("agent_id"))
    metrics_core_module.save_agent_label_cache(agent_label_cache)
    subagents_by_session = metrics_core_module.group_subagents_by_session(subagent_records)
    # Mutates the same dict objects all_time_records/chart_records/records
    # already share (none of the load/dedupe/filter steps above copy the
    # records, only filter the list) - so every aggregate view downstream
    # (composition legend, trend chart, project totals) picks up subagent
    # tokens automatically. See fold_subagent_tokens_into_sessions's own
    # docstring for why this was missing (2026-08-05).
    metrics_core_module.fold_subagent_tokens_into_sessions(all_time_records, subagent_records)

    label_cache = metrics_core_module.load_label_cache()
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
                lambda session_key: metrics_core_module.get_task_label(
                    unique_sessions[session_key], label_cache, label_budget, lang=lang
                ),
                session_keys,
            )
        )
    label_by_session_id = dict(zip(session_keys, task_labels, strict=True))
    for record in records:
        record["task_label"] = label_by_session_id.get(record.get("session_id"))
    metrics_core_module.save_label_cache(label_cache)

    project_first_seen = metrics_core_module.compute_project_first_seen(all_time_records)
    project_last_seen = metrics_core_module.compute_project_last_seen(all_time_records)
    open_projects = metrics_core_module.get_open_projects()
    # Over the whole history, not per period, so a model keeps its colour
    # whichever period is on screen - see assign_model_slots. Ordered by
    # first appearance, not compute_model_totals' dict order (not
    # guaranteed chronological across sessions - see
    # order_models_by_first_appearance) - that order is what makes a
    # model's slot permanent as the history grows.
    model_slots = metrics_core_module.assign_model_slots(
        metrics_core_module.order_models_by_first_appearance(daily_chart_records),
        len(PANE_COLORS),
    )

    # The same eight project colour slots the HTML dashboard uses, ranked
    # over the feed the chart draws and fixed for the whole run, so a
    # project keeps its colour whichever period is on screen (2026-10-05).
    project_slots = metrics_core_module.assign_project_slots(
        daily_chart_records, datetime.now().astimezone()
    )

    return {
        "records": records,
        "chart_records": chart_records,
        "daily_chart_records": daily_chart_records,
        "subagents_by_session": subagents_by_session,
        "project_first_seen": project_first_seen,
        "project_last_seen": project_last_seen,
        "open_projects": open_projects,
        "model_slots": model_slots,
        "project_slots": project_slots,
        "lang": lang,
        # Period-independent like everything else here: the weekly window is
        # its own calendar, so "p" never re-windows it.
        "weekly_meter": metrics_core_module.load_weekly_meter_view(
            Path(metrics_core_module.METRICS_FILE).parent, lang, datetime.now().astimezone()
        ),
    }


def apply_period(
    base: dict[str, Any],
    period_id: str,
    metrics_core_module: ModuleType,
    now: datetime | None = None,
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
    window = metrics_core_module.resolve_period(period_id, render_now)
    period_records = metrics_core_module.filter_sessions_by_window(base["records"], window)
    return {
        "records": period_records,
        "branches": metrics_core_module.describe_branches(
            metrics_core_module.compute_branch_breakdown(period_records), base["lang"]
        ),
        "chart_records": metrics_core_module.filter_sessions_by_window(
            base["chart_records"], window
        ),
        "daily_chart_records": metrics_core_module.filter_deltas_by_window(
            base["daily_chart_records"], window
        ),
        "subagents_by_session": base["subagents_by_session"],
        "project_first_seen": base["project_first_seen"],
        "project_last_seen": base["project_last_seen"],
        "open_projects": base["open_projects"],
        "model_slots": base["model_slots"],
        "project_slots": base["project_slots"],
        "lang": base["lang"],
        "weekly_meter": base.get("weekly_meter"),
        "period_id": period_id,
        # The period-independent source this view was derived from, carried
        # along so any holder of a view can re-window it without reloading.
        # Kept inside the dict rather than added as a load_data return value
        # or an app constructor argument so that every existing caller -
        # main(), the headless drivers under .superpowers/ - keeps working
        # unchanged while still getting the fast path.
        "base": base,
    }


def load_data(
    arguments: argparse.Namespace, metrics_core_module: ModuleType | None = None
) -> dict[str, Any]:
    """Loads and windows in one call, at the default period.

    Kept for callers that just want data for one period (main(), the
    headless drivers, ad-hoc scripts). Anything that re-windows repeatedly
    must call load_base_data once and apply_period per period instead;
    calling this in a loop is precisely the defect it was split to fix.
    """
    metrics_core_module = metrics_core_module or import_metrics_report()
    return apply_period(
        load_base_data(arguments, metrics_core_module=metrics_core_module),
        metrics_core_module.DEFAULT_PERIOD_ID,
        metrics_core_module,
    )


def build_session_tree(
    records: list[dict[str, Any]],
    subagents_by_session: dict[str, list[dict[str, Any]]],
    metrics_core_module: ModuleType,
    *,
    lang: str,
) -> Tree[Any]:
    group_colors = metrics_core_module.assign_group_colors(records)
    groups = metrics_core_module.build_project_groups(records, group_colors)
    tree: Tree[Any] = Tree(metrics_core_module.t("tab.sessions", lang))
    tree.show_root = False
    for group in groups:
        project_path = group["project"]
        # Omit zero-token sessions (2026-07-21, ported from
        # dashboard_html.py's render_html) - a session that never recorded
        # any input/output tokens (aborted before the first turn, etc.) is
        # dead weight in the tree. A group whose every session is zero-token
        # is skipped entirely rather than left as an empty, expandable node.
        visible_records = [
            record
            for record in group["records"]
            if metrics_core_module.record_token_total(record) > 0
        ]
        if not visible_records:
            continue
        label = metrics_core_module.project_display_label(project_path, lang=lang)
        group_state = {
            "label": label,
            "records": visible_records,
            "branch": None,
            "subagents_by_session": subagents_by_session,
        }
        group_node = tree.root.add(
            group_label(group_state, metrics_core_module, lang=lang),
            data=group_state,
            expand=False,
        )
        add_session_leaves(
            group_node, visible_records, subagents_by_session, metrics_core_module, lang=lang
        )
    return tree


def group_label(group_state: dict[str, Any], metrics_core_module: ModuleType, *, lang: str) -> str:
    """The group's line: its project, what it currently shows, and the branch
    it is narrowed to (none = every branch)."""
    shown = matching_branch(group_state["records"], group_state["branch"])
    total_tokens = sum(metrics_core_module.record_token_total(record) for record in shown)
    session_count = metrics_core_module.tn(
        "sessions", len(shown), lang, n=metrics_core_module.format_tokens(len(shown))
    )
    # Escaped: a bracketed branch name is a Rich markup tag to the label.
    narrowed = f" {escape('[' + group_state['branch'] + ']')}" if group_state["branch"] else ""
    return (
        f"{group_state['label']} ({session_count} · "
        f"{metrics_core_module.format_tokens(total_tokens)} tokens){narrowed}"
    )


def matching_branch(records: list[dict[str, Any]], branch: str | None) -> list[dict[str, Any]]:
    """Records on `branch`, matched the way the page matches a row: by the text
    of the branch column, "-" standing for no branch. None keeps them all."""
    if branch is None:
        return records
    return [record for record in records if (record.get("git_branch") or "-") == branch]


def add_session_leaves(
    group_node: Any,
    records: list[dict[str, Any]],
    subagents_by_session: dict[str, list[dict[str, Any]]],
    metrics_core_module: ModuleType,
    *,
    lang: str,
) -> None:
    for record in records:
        session_id = record.get("session_id") or ""
        date_text = metrics_core_module.format_date(record.get("timestamp_start"), lang=lang)
        task_label = record.get("task_label") or "-"
        tokens = metrics_core_module.format_tokens(metrics_core_module.record_token_total(record))
        # A floor gets a "≥" inside the same right-aligned column; the
        # detail panel carries the note that says why.
        if metrics_core_module.incomplete_usage_messages(record):
            tokens = "≥" + tokens
        # format_duration, like the HTML table: raw seconds printed
        # "333874s" for a long session (2026-10-05 review).
        duration_text = metrics_core_module.format_duration(record.get("duration_seconds"))
        # Subagent count badge (2026-07-21, ported from
        # dashboard_html.py's render_session_row/subagent_badge) - lets
        # you tell which sessions dispatched subagents right in the
        # tree, without expanding every single one's detail panel.
        # Counts only subagents that actually recorded tokens, same
        # filter render_html applies to its own badge.
        session_subagents = subagents_by_session.get(session_id, [])
        visible_subagent_count = sum(
            1
            for subagent in session_subagents
            if metrics_core_module.record_token_total(subagent) > 0
        )
        subagent_badge = f" 🤖{visible_subagent_count}" if visible_subagent_count else ""
        row = (
            f"{session_id[:12]:<14}{date_text:<14}{task_label[:28]:<30}"
            f"{tokens:>8}   {duration_text:>9}{subagent_badge}"
        )
        group_node.add_leaf(row, data={"session_id": session_id})


class SessionDetailPanel(VerticalScroll):
    def __init__(
        self, metrics_core_module: ModuleType, *, lang: str, **keyword_arguments: Any
    ) -> None:
        super().__init__(**keyword_arguments)
        self.metrics_core_module = metrics_core_module
        self.lang = lang
        self._body = Static("")

    def compose(self) -> ComposeResult:
        yield self._body

    def clear(self) -> None:
        self._body.update("")

    def show_session(
        self, session_id: str, subagents: list[dict[str, Any]], incomplete_usage: int = 0
    ) -> None:
        """`incomplete_usage` is the session's folded count (its own messages
        plus every subagent's, see fold_subagent_tokens_into_sessions): when
        positive, the one note line saying why its figures carry a "≥"."""
        lines = [f"[bold]{session_id}[/bold]", ""]
        if not subagents:
            lines.append(
                f"[dim]{self.metrics_core_module.t('detail.no_subagents', self.lang)}[/dim]"
            )
        else:
            subagents_title = self.metrics_core_module.t(
                "detail.subagents_title", self.lang, n=len(subagents)
            )
            lines.append(f"[bold]{subagents_title}[/bold]")
            lines.append(
                f"{self.metrics_core_module.t('col.task', self.lang):<40}{'tokens':>12}"
                f"{'tools':>8}{self.metrics_core_module.t('col.duration', self.lang):>10}"
            )
            for row_index, subagent in enumerate(subagents):
                agent_type = subagent.get("agent_type") or "-"
                agent_label = subagent.get("agent_label") or "-"
                input_tokens_text = self.metrics_core_module.format_tokens(
                    subagent.get("input_tokens", 0)
                )
                output_tokens_text = self.metrics_core_module.format_tokens(
                    subagent.get("output_tokens", 0)
                )
                if self.metrics_core_module.incomplete_usage_messages(subagent):
                    output_tokens_text = "≥" + output_tokens_text
                duration_text = self.metrics_core_module.format_duration(
                    subagent.get("duration_seconds")
                )
                task = f"{agent_type} — {agent_label}"[:38]
                style = "on #202020" if row_index % 2 == 1 else ""
                row = (
                    f"{task:<40}{input_tokens_text}/{output_tokens_text:<8}"
                    f"{subagent.get('tool_call_count', 0):>8}{duration_text:>10}"
                )
                lines.append(f"[{style}]{row}[/{style}]" if style else row)
        if incomplete_usage:
            floor_note = self.metrics_core_module.tn(
                "floor_note",
                incomplete_usage,
                self.lang,
                n=self.metrics_core_module.format_tokens(incomplete_usage),
            )
            lines.append(f"[dim]{floor_note}[/dim]")
        self._body.update("\n".join(lines))


METER_BAR_CELLS = 40


def meter_bar(view: dict[str, Any]) -> str:
    """The meter as one line of block characters: filled cells up to the
    value, shaded cells across the observed range, so the range reads
    without colour (the numbers beside it say the same in words)."""
    start = view["range_start_percent"]
    end = view["range_end_percent"]
    filled = round(view["fill_percent"] * METER_BAR_CELLS / 100)
    cells = []
    for index in range(METER_BAR_CELLS):
        percent = (index + 0.5) * 100 / METER_BAR_CELLS
        in_range = start is not None and start <= percent <= end
        if index < filled:
            cells.append("▓" if in_range else "█")
        else:
            cells.append("▒" if in_range else "░")
    return "".join(cells)


class WeeklyMeterPanel(Static):
    """The weekly-limit panel: the same view dict the HTML page renders
    (metrics_core.describe_weekly_meter), so the two cannot disagree."""

    def __init__(
        self, view: dict[str, Any], metrics_core_module: ModuleType, **keyword_arguments: Any
    ) -> None:
        super().__init__(**keyword_arguments)
        self.view = view
        self.metrics_core_module = metrics_core_module

    def render(self) -> RenderResult:
        view = self.view
        lines = [Text(view["heading"], style="bold")]
        if view["empty"]:
            lines += [Text(view["status"]), Text(view["caveat"], style="dim")]
            return Text("\n").join(lines)
        lines.append(Text(f"{meter_bar(view)}  {view['value_text']} {view['tiers']}"))
        lines.append(Text(view["status"]))
        if view["range"]:
            lines.append(Text(view["range"]))
        lines += [Text(view["five_hour"]), Text(view["caveat"], style="dim")]
        label_width = max((len(row["window"]) for row in view["rows"]), default=0)
        for row in view["rows"]:
            lines.append(
                Text(
                    f"{row['window']:<{label_width}} {row['accumulated']:>8}  "
                    f"{row['tiers']}  {row['outcome']}"
                )
            )
        lines += self.causes_lines()
        return Text("\n").join(lines)

    def causes_lines(self) -> list[Text]:
        """The cache-creation causes, from the same view dict the HTML page
        renders (metrics_core.describe_cache_causes)."""
        causes = self.view.get("causes")
        if not causes or causes["empty"]:
            return []
        lang = causes["lang"]

        def label(key: str) -> str:
            return str(self.metrics_core_module.t(f"causes.col.{key}", lang))

        lines = [Text(""), Text(causes["heading"], style="bold"), Text(causes["note"], style="dim")]
        for warning in (causes["alert"], causes["first_jump"]):
            if warning:
                lines.append(Text(f"⚠ {warning}", style="bold"))
        window_keys = ("total", "cold_start", "rewrite", "incremental", "after_idle")
        window_width = max((len(row["window"]) for row in causes["windows"]), default=0)
        for row in causes["windows"]:
            fields = "  ".join(f"{label(key)} {row[key]}" for key in window_keys)
            lines.append(Text(f"{row['window']:<{window_width}} {fields}"))
            lines.append(
                Text(
                    f"  {label('main_short')} {row['main_short']}  "
                    f"{label('subagent_short')} {row['subagent_short']}",
                    style="dim",
                )
            )
        size_keys = ("runs", "creation", "rewrite")
        size_labels = {"runs": "runs", "creation": "creation", "rewrite": "rewrite_share"}
        size_width = max((len(row["size"]) for row in causes["sizes"]), default=0)
        for row in causes["sizes"]:
            fields = "  ".join(f"{label(size_labels[key])} {row[key]}" for key in size_keys)
            lines.append(Text(f"{row['size']:<{size_width}} {fields}"))
        for row in causes["first_requests"]:
            lines.append(
                Text(
                    f"{label('version')} {row['version']}  "
                    f"{label('conversations')} {row['conversations']}  "
                    f"{label('median')} {row['median']}"
                )
            )
        for row in causes["first_by_project"]:
            lines.append(
                Text(
                    f"{row['project']}  "
                    f"{label('conversations')} {row['conversations']}  "
                    f"{label('median')} {row['median']}"
                )
            )
        for row in causes["idle_sessions"]:
            lines.append(
                Text(
                    f"{row['session']}  {label('idle_tokens')} {row['tokens']}  "
                    f"{label('resumes')} {row['resumes']}"
                )
            )
        return lines


class BranchesPanel(Static):
    """Tokens by branch inside each project: the same view dict the HTML page
    renders (metrics_core.describe_branches), so the two cannot disagree."""

    def __init__(self, view: dict[str, Any], **keyword_arguments: Any) -> None:
        super().__init__(**keyword_arguments)
        self.view = view

    def render(self) -> RenderResult:
        view = self.view
        columns = view["columns"]
        lines = [Text(view["heading"], style="bold"), Text(view["note"], style="dim")]
        for project in view["projects"]:
            rows = project["rows"]
            branch_width = max([len(columns["branch"])] + [len(row["branch"]) for row in rows])
            lines.append(Text(""))
            lines.append(Text(project["project"], style="bold"))
            lines.append(
                Text(
                    f"{columns['branch']:<{branch_width}} {columns['sessions']:>8} "
                    f"{columns['tokens']:>8} {columns['share']:>8}",
                    style="dim",
                )
            )
            for row in rows:
                lines.append(
                    Text(
                        f"{row['branch']:<{branch_width}} {row['sessions']:>8} "
                        f"{row['tokens']:>8} {row['share']:>8}"
                    )
                )
        return Text("\n").join(lines)


class QuotaSummary(Static):
    """A separate widget from CompositionLegend, deliberately - not a
    second line inside it. CompositionLegend.on_click hit-tests a click
    purely by event.x against the chip texts' widths (no event.y check at
    all), so a second line in the SAME widget would make a click on this
    line get misread as a click on a chip."""

    def __init__(
        self,
        totals: dict[str, int],
        metrics_core_module: ModuleType,
        *,
        lang: str,
        **keyword_arguments: Any,
    ) -> None:
        super().__init__(**keyword_arguments)
        self.totals = totals
        self.metrics_core_module = metrics_core_module
        self.lang = lang

    def render(self) -> str:
        quota_input, quota_output = self.metrics_core_module.compute_quota_totals(self.totals)
        line: str = self.metrics_core_module.t(
            "quota_line",
            self.lang,
            input=self.metrics_core_module.format_tokens(quota_input),
            output=self.metrics_core_module.format_tokens(quota_output),
        )
        return line


LEGEND_CHIP_SEPARATOR = "   "


def legend_chip_index_at(chip_labels: Sequence[str], column: int) -> int:
    """The chip a click on this column of a legend line landed on.

    The legend prints its chips one after another, each as wide as its own
    text, joined by LEGEND_CHIP_SEPARATOR; a click belongs to the chip whose
    text, or whose gap to the next chip, it hit. Dividing the widget into
    equal parts instead lands on the neighbouring chip whenever the labels
    differ in width, which they always do (backlog 2026-10-23 #1). A column
    past the last chip's end is that last chip.
    """
    gap = cell_len(LEGEND_CHIP_SEPARATOR)
    chip_end = -gap
    for index, label in enumerate(chip_labels):
        chip_end += gap + cell_len(label)
        if column < chip_end + gap:
            return index
    return len(chip_labels) - 1


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

    def __init__(
        self,
        totals: dict[str, int],
        metrics_core_module: ModuleType,
        *,
        lang: str,
        **keyword_arguments: Any,
    ) -> None:
        super().__init__(**keyword_arguments)
        self.totals = totals
        self.metrics_core_module = metrics_core_module
        self.lang = lang

    def _chip_labels(self) -> list[str]:
        return [
            f"{name} · {self.metrics_core_module.format_tokens(self.totals.get(series, 0))}"
            for series, name in self.SERIES
        ]

    def render(self) -> str:
        chips = []
        for (series, _name), label in zip(self.SERIES, self._chip_labels(), strict=True):
            is_highlighted = self.highlighted is not None and series in self.highlighted
            style = "bold reverse" if is_highlighted else ""
            chips.append(f"[{style}]{label}[/{style}]" if style else label)
        return LEGEND_CHIP_SEPARATOR.join(chips)

    def on_click(self, event: Any) -> None:
        series = self.SERIES[legend_chip_index_at(self._chip_labels(), event.x)][0]
        current: set[str] = self.highlighted or set()
        if event.shift:
            self.highlighted = current | {series}
        elif event.ctrl:
            remaining = current - {series}
            self.highlighted = remaining or None
        else:
            self.highlighted = None if current == {series} else {series}
        self.post_message(self.Highlight(self.highlighted))


# A plotext colour: a name, or an (r, g, b) tuple drawn as-is.
PlotColor = str | tuple[int, int, int]
# Five names and two RGB tuples, not seven names (2026-09-23). plotext
# emits a name as an ANSI colour and Textual repaints ANSI colours through
# its theme, which draws "red" exactly like "magenta" and "yellow" exactly
# like one of the other five - seven names, five colours on screen, so two
# pairs of projects shared a colour. An RGB tuple is not remapped. Mint and cream were picked to sit
# at least 57 delta E from every themed colour here and from the "other"
# gray; tests/test_tui_palette.py measures the rendered result.
#
# Twelve, not seven (2026-09-29): the model chart gives every model its
# own slot, and a real history had already outgrown seven, so the
# newest was drawn in a borrowed colour. The five added RGB tuples (pink,
# hot magenta, deep blue, forest green, red) were picked greedily to
# keep the closest pair of the whole palette, gray included, as far
# apart as possible: 48.2 CIE76 delta E, against the test's floor of 20.
# The project pane uses the first PROJECT_COLOR_SLOTS (eight), by slot.
PANE_COLORS: list[PlotColor] = [
    "green",
    "blue",
    "magenta",
    "orange",
    "cyan",
    (0, 255, 180),
    (255, 240, 180),
    (255, 180, 240),
    (255, 0, 195),
    (0, 120, 210),
    (45, 135, 45),
    (255, 30, 0),
]
PANE_OTHER_COLOR = "gray"
# "sem projeto" keeps a band of its own, as in the HTML dashboard: since the
# ephemeral-path fix it holds every throwaway-cwd session (skill evals),
# which "outros" would hide (2026-10-05 review). A light neutral, measured
# at delta E 30.7 from the closest rendered colour above
# (tests/test_tui_palette.py).
PANE_NO_PROJECT_COLOR: PlotColor = (200, 198, 190)


class OthersBucket:
    """The type of PANE_OTHER_KEY, the pane chart's "others" series key.

    Not a string on purpose: a project key is whatever project_path a
    row carries, so any string can be one. The key used to be
    "__outros__", and a project by that name ranked among the top ones
    put the same key in the series list twice - the chart raised
    ValueError, and the legend would have called the project "outros"
    and painted it gray. An instance of this class equals nothing but
    itself, so the bucket's label (from the catalog) and its gray (by
    key) can never land on a real project.
    """


PANE_OTHER_KEY = OthersBucket()
# A pane series: a project's color group key, or the "others" bucket.
PaneKey = str | None | OthersBucket


def compute_pane_projects(
    metrics_core_module: ModuleType,
    chart_records: list[dict[str, Any]],
    project_slots: dict[str, int],
) -> tuple[list[str | None], bool]:
    """The projects that get their own band in this window, and whether an
    "outros" bucket is needed.

    Returns (keys, has_other): keys are the color_group_keys holding one
    of the colour slots that have tokens in chart_records, ordered by
    their spend here (the legend reads busiest first); has_other is True
    when any project without a slot has tokens here. "No project" (None)
    is listed last when it has tokens - it never holds a slot, and never
    folds into "outros" either. The slots themselves
    are fixed for the run (metrics_core.assign_project_slots), so a
    project's colour does not change with the period on screen.
    """
    totals: dict[str, int] = {}
    no_project_tokens = 0
    has_other = False
    for record in chart_records:
        key = metrics_core_module.color_group_key(record.get("project_path"))
        tokens = metrics_core_module.record_token_total(record)
        if key is None:
            no_project_tokens += tokens
            continue
        if key not in project_slots:
            has_other = has_other or tokens > 0
            continue
        totals[key] = totals.get(key, 0) + tokens
    project_keys = sorted(
        (key for key in totals if totals[key] > 0),
        key=lambda sort_key: (-totals[sort_key], sort_key),
    )
    keys: list[str | None] = [*project_keys]
    # "sem projeto" keeps its own band after the projects, like the HTML
    # legend; it never takes a colour slot.
    if no_project_tokens > 0:
        keys.append(None)
    return keys, has_other


def pane_series_label(metrics_core_module: ModuleType, key: PaneKey, *, lang: str) -> str:
    if isinstance(key, OthersBucket):
        others: str = metrics_core_module.t("others", lang)
        return others
    label: str = metrics_core_module.project_display_label(key, lang=lang)
    return label


def partial_marker(
    metrics_core_module: ModuleType,
    granularity: str,
    pct: int,
    canvas_width: int | None,
    *,
    lang: str,
) -> str | None:
    """The in-progress marker for the chart's last bucket, in the longest
    form that fits under a canvas this many cells wide.

    plotext centres an x label on the canvas and silently drops it when it
    would run past either edge; one no longer than the canvas always fits
    (plotext/_build.py: col_center = col_start + width_canvas // 2, and the
    frame adds a cell on the right). None - before the widget is laid out -
    means the full sentence. A canvas under three cells has room for none of
    them, and gets nothing rather than a label plotext would drop anyway.
    """
    forms: list[str] = [
        metrics_core_module.t(f"partial_bar.{granularity}", lang, pct=pct),
        metrics_core_module.t("partial_bar.short", lang, pct=pct),
        f"{pct}%",
    ]
    if canvas_width is None:
        return forms[0]
    return next((form for form in forms if len(form) <= canvas_width), None)


def draw_stacked_bars(
    plt: Any,
    xlabels: list[str],
    series_lists: list[list[int]],
    series_labels: list[str],
    colors: Sequence[PlotColor],
    *,
    canvas_width: int | None,
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

    `canvas_width` is the plot area in character cells, which decides how
    wide a bar can be before it shares a cell with its neighbour (see
    _bar_width_on). None, before the widget is laid out, draws plotext's
    default width.
    """
    bar_positions = list(range(1, len(xlabels) + 1))
    bar_width = (
        BAR_WIDTH if canvas_width is None else _bar_width_on(canvas_width, len(bar_positions))
    )
    cumulative: list[list[int]] = []
    running = [0] * len(bar_positions)
    for series in series_lists:
        running = [
            total_so_far + next_value
            for total_so_far, next_value in zip(running, series, strict=True)
        ]
        cumulative.append(running)
    # An invisible, unlabelled layer of zero-height bars at EVERY position,
    # drawn first. plotext takes the x range from the bars actually drawn,
    # and every real layer skips its zero days - so without this, a bucket
    # with no tokens at either edge fell off the axis (taking today's
    # in-progress label with it), and a window with no tokens at all drew
    # a bare frame. A zero bar only paints its base cell with the
    # background, and every real layer drawn after it paints over that.
    plt.bar(
        bar_positions,
        [0] * len(bar_positions),
        label=None,
        color=None,
        width=_bar_width_for(bar_positions, bar_width),
    )
    for series_index in reversed(range(len(series_lists))):
        shown = [
            position_index
            for position_index, value in enumerate(series_lists[series_index])
            if value > 0
        ]
        if shown:
            positions = [bar_positions[position_index] for position_index in shown]
            plt.bar(
                positions,
                [cumulative[series_index][position_index] for position_index in shown],
                label=series_labels[series_index],
                color=colors[series_index],
                width=_bar_width_for(positions, bar_width),
            )
    plt.xticks(bar_positions, xlabels)


# plotext's own default bar width (plotext/_default.py: bar_width = 4 / 5),
# and the widest a bar gets - on a canvas with room to spare.
#
# Bars of one width can still render a cell apart - 16 and 17 cells on one
# chart - and that is plotext's rasteriser, not this width: each edge is
# floor()ed to a cell on its own, so a bar covers one cell more or fewer
# depending on where its edges fall between cells. Measured on 2026-09-23:
# floor(0.5 + s * (x - x_min)) predicted every rendered width exactly, at
# four sizes. Evening them out would mean drawing around plotext's
# rounding rather than with it, for a one-cell difference.
BAR_WIDTH = 0.8


def _bar_width_on(canvas_width: int, days: int) -> float:
    """The widest a bar can be, in days, and still leave a blank cell
    between it and the next day's bar on a canvas this many cells wide.

    plotext puts x in cell floor(0.5 + s * (x - x_min)), with s =
    (canvas_width - 1) / (x_max - x_min) cells per day (plotext/_utility.py,
    get_matrix_data), and x_min/x_max are the outer edges of the first and
    last bar: 1 - w/2 and days + w/2. Two edges at least 2 / s apart land at
    least two cells apart, leaving one blank between them, so the gap from
    day k's right edge to day k+1's left edge, (1 - w) * s, must be at least
    2. At plotext's 0.8 that gap is a fifth of a day - under one cell as
    soon as a day is narrower than five cells, and there two neighbouring
    days painted the same cell, in the colour of whichever layer plotext
    drew last (2026-09-23 review: two series on alternating days, 30 days
    at 120 columns).

    Solving (1 - w) * (canvas_width - 1) >= 2 * (days - 1 + w) for w, and
    never wider than plotext's own 0.8. With no room for a gap at all the
    result is negative, and plotext draws a negative width as zero
    (plotext/_monitor.py, draw_bar): each bar is then the one cell its day
    lands in, and days a cell or more apart never land in the same one -
    only a canvas with fewer cells than days makes two days share, which no
    width can prevent.

    A canvas of no cells at all - a widget no wider than its y labels and
    frame - has no room for anything, and the formula would divide by zero
    at -1 and turn positive again below it.
    """
    if canvas_width < 1:
        return 0.0
    cells = canvas_width - 1
    return min(BAR_WIDTH, (cells - 2 * (days - 1)) / (cells + 2))


def _bar_width_for(positions: list[int], bar_width: float) -> float:
    """The `width` to pass so these bars come out bar_width wide.

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
        return bar_width
    return bar_width / ((positions[-1] - positions[0]) / (len(positions) - 1))


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
        metrics_core_module: ModuleType,
        chart_records: list[dict[str, Any]],
        mode: str = "pane",
        *,
        lang: str,
        model_slots: dict[str, metrics_core.ModelColorSlot] | None = None,
        project_slots: dict[str, int] | None = None,
        **keyword_arguments: Any,
    ) -> None:
        super().__init__(**keyword_arguments)
        self.metrics_core_module = metrics_core_module
        self.chart_records = chart_records
        self.mode = mode
        self.lang = lang
        # Each project's colour slot, settled once per run by
        # load_base_data. None - a chart built on its own - settles them
        # over this chart's records instead, like model_slots below.
        self.project_slots = (
            project_slots
            if project_slots is not None
            else metrics_core_module.assign_project_slots(
                chart_records, datetime.now().astimezone()
            )
        )
        # Each model's colour slot, settled over the whole history by
        # load_base_data. None - a chart built on its own - settles them
        # over this chart's records instead.
        self.model_slots = model_slots
        self.highlighted: set[str] | None = None
        self.project_highlighted: set[PaneKey] | None = None
        self._now: datetime | None = None
        self._drawn_width = 0
        self._too_narrow = False

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

    def set_project_highlight(self, projects: set[PaneKey] | None) -> None:
        self.project_highlighted = projects
        if self.mode == "pane":
            self.render_chart()

    def render_chart(self, now: datetime | None = None) -> None:
        self._now = now
        self._draw_chart(now)
        self.refresh()

    def render(self) -> RenderResult:
        # The bars are sized for the canvas they are drawn on (see
        # draw_stacked_bars), and the widget's width is only known once it
        # is laid out - after on_mount's first draw - and changes with every
        # resize. Redrawn here, where textual_plotext is about to build.
        if self.size.width != self._drawn_width:
            self._draw_chart(self._now)
        if self._too_narrow:
            return Text(
                self.metrics_core_module.t("chart_too_narrow", self.lang),
                style="dim",
                no_wrap=True,
                overflow="ellipsis",
            )
        return super().render()

    def _draw_chart(self, now: datetime | None) -> None:
        self._drawn_width = self.size.width
        self._too_narrow = False
        # aggregate_by_day returns (granularity, days) since
        # dashboard_html.py's adaptive-granularity feature (2026-07-21) -
        # this file wasn't updated when that landed, so it was treating
        # the whole tuple as the days list. Bucket keys are also
        # granularity-specific now ("YYYY-MM-DDTHH" for hour, "YYYY-MM"
        # for month, "YYYY" for year, not always "YYYY-MM-DD"), so the
        # tick label must use that granularity's own parse/tick format
        # instead of a hardcoded [5:] MM-DD slice.
        granularity, days = self.metrics_core_module.aggregate_by_day(self.chart_records)
        self.plt.clear_data()
        # clear_data keeps labels, and the in-progress marker below must not
        # outlive the bucket it describes: a chart left open past the end
        # of that bucket, or dragged too narrow for the marker, redraws
        # through here without setting it.
        self.plt.xlabel(None)
        if len(days) < 2:
            return
        parse_format = self.metrics_core_module.GRANULARITY_CONFIG[granularity]["parse_fmt"]
        labels = [
            self.metrics_core_module.tick_label(
                granularity, datetime.strptime(day["day"], parse_format), self.lang
            )
            for day in days
        ]
        # The last bucket is always a fraction of a full one while it's
        # still running (backlog, 2026-09-02: "the newest bar always reads
        # as a decline") - said in words rather than greyed out like
        # dashboard_html.py's SVG rects are: each series is drawn in one
        # colour across every bucket (see draw_stacked_bars).
        partial_fraction = self.metrics_core_module.partial_bucket_fraction(
            granularity, days[-1]["day"], now or datetime.now().astimezone()
        )
        color: Sequence[PlotColor]
        if self.mode == "pane":
            top_keys, has_other = compute_pane_projects(
                self.metrics_core_module, self.chart_records, self.project_slots
            )
            series_keys: list[PaneKey] = [*top_keys, *([PANE_OTHER_KEY] if has_other else [])]
            self.pane_series_keys = series_keys
            series_data: dict[PaneKey, list[int]] = {series_key: [] for series_key in series_keys}
            for day in days:
                day_totals = dict.fromkeys(series_keys, 0)
                for record in day["records"]:
                    record_key = self.metrics_core_module.color_group_key(
                        record.get("project_path")
                    )
                    tokens = self.metrics_core_module.record_token_total(record)
                    # A zero-token record names no band: compute_pane_projects
                    # leaves its project out, and adding 0 changes nothing.
                    if not tokens:
                        continue
                    bucket: PaneKey = record_key if record_key in top_keys else PANE_OTHER_KEY
                    day_totals[bucket] += tokens
                for series_key in series_keys:
                    series_data[series_key].append(day_totals[series_key])
            series_lists = [series_data[series_key] for series_key in series_keys]
            series_labels = [
                pane_series_label(self.metrics_core_module, series_key, lang=self.lang)
                for series_key in series_keys
            ]
            # Each project by its own slot, never by its rank in this window,
            # so its colour survives a period change (2026-10-05); the
            # "outros" bucket is gray by its key.
            palette = [
                PANE_OTHER_COLOR
                if isinstance(series_key, OthersBucket)
                else PANE_NO_PROJECT_COLOR
                if series_key is None
                else PANE_COLORS[self.project_slots[series_key]]
                for series_key in series_keys
            ]
            if self.project_highlighted:
                color = [
                    palette[palette_index] if key in self.project_highlighted else "gray"
                    for palette_index, key in enumerate(series_keys)
                ]
            else:
                color = palette
            title = "chart_title.pane"
        elif self.mode == "model":
            # Every model seen anywhere in the window gets a band in every
            # day, so a model's colour means the same thing across the whole
            # chart instead of shifting with each day's cast.
            all_models: dict[str, int] = {}
            for day in days:
                for model, amount in self.metrics_core_module.compute_model_totals(day["records"])[
                    0
                ].items():
                    all_models[model] = all_models.get(model, 0) + amount
            model_keys = sorted(all_models, key=lambda model: -all_models[model])
            per_day: list[dict[str, int]] = []
            unattributed: list[int] = []
            for day in days:
                by_model, remainder = self.metrics_core_module.compute_model_totals(day["records"])
                per_day.append(by_model)
                unattributed.append(remainder)
            # The unattributed band sits LAST so the models stay adjacent to
            # one another, and carries the "other" colour it shares with the
            # pane mode's overflow bucket - both mean "everything else".
            series_lists = [[day.get(model, 0) for day in per_day] for model in model_keys]
            series_lists.append(unattributed)
            series_labels = [
                self.metrics_core_module.model_display_label(model) for model in model_keys
            ]
            series_labels.append(self.metrics_core_module.t("no_model", self.lang))
            # By the model's own slot, not its rank here: ranked, a model
            # changed colour whenever another outspent it in the period on
            # screen (backlog 2026-09-24 #6). The legend still lists them by
            # spend.
            slots = (
                self.model_slots
                if self.model_slots is not None
                else self.metrics_core_module.assign_model_slots(model_keys, len(PANE_COLORS))
            )
            # An overflow model reuses an earlier model's colour rather than
            # going uncoloured (see ModelColorSlot.is_overflow) - the TUI has
            # no click-to-highlight to keep the two apart visually the way
            # the HTML dashboard's separate CSS class does, so the legend
            # label itself carries the marker instead.
            for index, model in enumerate(model_keys):
                slot = slots.get(model)
                if slot is not None and slot.is_overflow:
                    series_labels[index] = f"{series_labels[index]} *"
            color = [
                PANE_OTHER_COLOR
                if (slot := slots.get(model)) is None
                else PANE_COLORS[slot.palette_index]
                for model in model_keys
            ]
            color.append(PANE_OTHER_COLOR)
            title = "chart_title.model"
        else:
            input_totals = [
                sum(record.get("input_tokens", 0) or 0 for record in day["records"]) for day in days
            ]
            output_totals = [
                sum(record.get("output_tokens", 0) or 0 for record in day["records"])
                for day in days
            ]
            cache_read_totals = [
                sum(record.get("cache_read_tokens", 0) or 0 for record in day["records"])
                for day in days
            ]
            cache_creation_totals = [
                sum(record.get("cache_creation_tokens", 0) or 0 for record in day["records"])
                for day in days
            ]
            if self.highlighted:
                color = [
                    self.SERIES_COLORS[series_index] if key in self.highlighted else "gray"
                    for series_index, key in enumerate(self.SERIES_KEYS)
                ]
            else:
                color = self.SERIES_COLORS
            series_lists = [input_totals, output_totals, cache_read_totals, cache_creation_totals]
            series_labels = ["input", "output", "cache read", "cache creation"]
            title = "chart_title.tokens"
        maximum_total = max((sum(day) for day in zip(*series_lists, strict=True)), default=0)
        # plotext's default y-axis ticks are raw, unformatted floats (e.g.
        # "1591684.7") - unreadable next to token counts everywhere else in
        # this dashboard already using k/M/B/T suffixes. The ticks sit on
        # round values (the same nice_axis_ticks the HTML chart uses; an
        # even split of the tallest bar printed 2.0B/4.1B/6.1B/8.1B), and
        # ylim below tops the axis at the last one so the bars are measured
        # against the gridlines they are read by.
        tick_values = self.metrics_core_module.nice_axis_ticks(maximum_total)
        tick_labels = [self.metrics_core_module.format_tokens(value) for value in tick_values]
        # The canvas is what is left of the widget once plotext has drawn
        # the widest y tick label and the two vertical frame lines beside
        # the plot (plotext/_build.py: width_canvas = width - yaxes -
        # width_ylabels). Unknown until the widget has been laid out.
        canvas_width = (
            self.size.width - 2 - max(len(label) for label in tick_labels)
            if self.size.width
            else None
        )
        if canvas_width is not None and canvas_width < 1:
            # No cell to draw a bar in. plotext itself raises on a canvas of
            # 0 cells once there are bars to fill (a ValueError in
            # get_fill_boundaries, measured 2026-09-23), so nothing is drawn
            # and render() says why instead of showing a bare frame. One
            # cell is the least plotext was measured to draw at, at any
            # height down to one row.
            self._too_narrow = True
            return
        draw_stacked_bars(
            self.plt, labels, series_lists, series_labels, color, canvas_width=canvas_width
        )
        self.plt.title(self.metrics_core_module.t(title, self.lang))
        self.plt.ylim(0, tick_values[-1])
        self.plt.yticks(tick_values, tick_labels)
        # On a line of its own under the axis, not appended to the last
        # tick label as it once was: plotext drops any tick label that
        # would touch the one before it, walking left to right, so the
        # last one - made longest by the suffix - was the first to go.
        # Measured 2026-09-25 over 30 days of data: the percentage showed
        # at 25 of the 141 widths from 60 to 200 columns, none under 163.
        if partial_fraction is not None:
            marker = partial_marker(
                self.metrics_core_module,
                granularity,
                round(partial_fraction * 100),
                canvas_width,
                lang=self.lang,
            )
            if marker is not None:
                self.plt.xlabel(marker)


class ProjectLegend(Static):
    highlighted: reactive[set[PaneKey] | None] = reactive(None)

    class Highlight(Message):
        def __init__(self, projects: set[PaneKey] | None) -> None:
            self.projects = projects
            super().__init__()

    def __init__(
        self,
        metrics_core_module: ModuleType,
        chart_records: list[dict[str, Any]],
        *,
        lang: str,
        project_slots: dict[str, int] | None = None,
        **keyword_arguments: Any,
    ) -> None:
        super().__init__(**keyword_arguments)
        self.metrics_core_module = metrics_core_module
        self.chart_records = chart_records
        self.lang = lang
        # The chart's own slots (TrendChart.project_slots), so a project
        # wears one colour in both. Both read the same daily deltas, so a
        # band never lacks its legend entry.
        self.project_slots = (
            project_slots
            if project_slots is not None
            else metrics_core_module.assign_project_slots(
                chart_records, datetime.now().astimezone()
            )
        )

    def _series_keys(self) -> list[PaneKey]:
        top_keys, has_other = compute_pane_projects(
            self.metrics_core_module, self.chart_records, self.project_slots
        )
        return [*top_keys, *([PANE_OTHER_KEY] if has_other else [])]

    def _chip_labels(self, series_keys: list[PaneKey]) -> list[str]:
        return [
            pane_series_label(self.metrics_core_module, key, lang=self.lang) for key in series_keys
        ]

    def render(self) -> str:
        series_keys = self._series_keys()
        if not series_keys:
            return f"[dim]{self.metrics_core_module.t('no_data', self.lang)}[/dim]"
        chips = []
        for key, label in zip(series_keys, self._chip_labels(series_keys), strict=True):
            is_highlighted = self.highlighted is not None and key in self.highlighted
            style = "bold reverse" if is_highlighted else ""
            chip = f"[{style}]{label}[/{style}]" if style else label
            chips.append(chip)
        return LEGEND_CHIP_SEPARATOR.join(chips)

    def on_click(self, event: Any) -> None:
        series_keys = self._series_keys()
        if not series_keys:
            return
        key = series_keys[legend_chip_index_at(self._chip_labels(series_keys), event.x)]
        current: set[PaneKey] = self.highlighted or set()
        if event.shift:
            self.highlighted = current | {key}
        elif event.ctrl:
            remaining = current - {key}
            self.highlighted = remaining or None
        else:
            self.highlighted = None if current == {key} else {key}
        self.post_message(self.Highlight(self.highlighted))


# Textual markup for the trend chart's four series colours. plotext draws
# SERIES_COLORS ("green", "blue", "magenta", "orange") as the ANSI codes
# 2, 4, 5 and 3; these names are the same four ANSI colours, and Textual
# repaints both through its theme, so the ranking's segments match the
# chart's bands (tests/test_tui_unconstrained.py checks the codes).
SERIES_MARKUP_COLORS = ["ansi_green", "ansi_blue", "ansi_magenta", "ansi_yellow"]
SERIES_NAMES = ["input", "output", "cache read", "cache creation"]


def composition_bar_markup(
    series_totals: dict[str, int], bar_length: int, bar_character: str
) -> str:
    """A bar of `bar_length` cells split by token type, one coloured run per
    series. Cells are shared out by largest remainder, so the runs always
    add up to exactly `bar_length` - never a cell gained or lost to
    rounding. The ranking only lists projects with tokens, so the total is
    always positive here; a 0-cell bar comes out as an empty string."""
    values = [series_totals.get(f"series-{index + 1}", 0) for index in range(4)]
    total = sum(values)
    exact = [value / total * bar_length for value in values]
    cells = [int(share) for share in exact]
    by_remainder = sorted(range(4), key=lambda index: exact[index] - cells[index], reverse=True)
    for index in by_remainder[: bar_length - sum(cells)]:
        cells[index] += 1
    return "".join(
        f"[{SERIES_MARKUP_COLORS[index]}]{bar_character * count}[/{SERIES_MARKUP_COLORS[index]}]"
        for index, count in enumerate(cells)
        if count
    )


class ProjectRankingList(Static):
    expanded = reactive(False)

    def __init__(
        self,
        metrics_core_module: ModuleType,
        chart_records: list[dict[str, Any]],
        project_first_seen: dict[str, str],
        project_last_seen: dict[str, str],
        open_projects: set[str],
        *,
        lang: str,
        **keyword_arguments: Any,
    ) -> None:
        super().__init__(**keyword_arguments)
        self.metrics_core_module = metrics_core_module
        self.chart_records = chart_records
        self.project_first_seen = project_first_seen
        self.project_last_seen = project_last_seen
        self.open_projects = open_projects
        self.lang = lang

    def toggle_more(self) -> None:
        self.expanded = not self.expanded

    def render(self) -> str:
        ordered, totals, maximum_total = self.metrics_core_module.compute_project_totals(
            self.chart_records, self.project_last_seen
        )
        # Omit zero-token projects (2026-07-21, ported from
        # dashboard_html.py's render_project_totals_chart) - a project with
        # nothing to show is just dead weight in this ranking.
        ordered = [project for project in ordered if totals.get(project, 0) > 0]
        if not ordered:
            return f"[dim]{self.metrics_core_module.t('no_data', self.lang)}[/dim]"

        maximum_total = maximum_total or 1
        series_by_project, _grand_totals = self.metrics_core_module.compute_project_series_totals(
            self.chart_records
        )
        visible = ordered[:10]
        hidden = ordered[10:]
        shown = visible + (hidden if self.expanded else [])

        lines = [
            f"[bold]{self.metrics_core_module.t('project_totals_title', self.lang)}[/bold]",
            "",
        ]
        for project in shown:
            total = totals[project]
            label = self.metrics_core_module.project_display_label(project, lang=self.lang)
            bar_length = int((total / maximum_total) * 30)
            is_open = project in self.open_projects
            bar_char = "█" if is_open else "▓"
            days_running = self.metrics_core_module.project_days_running(
                self.project_first_seen, project, lang=self.lang
            )
            formatted_total = self.metrics_core_module.format_tokens(total)
            # A cut name says so (2026-10-05 review: "candidatador-de-vagas /"
            # read as a whole name), and the bar is split by token type like
            # the HTML ranking's. The markup tags take no cells, so the bar is
            # padded by hand to keep the total column at its fixed offset.
            shown_label = label if len(label) <= 24 else label[:23] + "…"
            bar = composition_bar_markup(series_by_project.get(project, {}), bar_length, bar_char)
            padding = " " * (32 - bar_length)
            lines.append(f"{shown_label:<26}{bar}{padding}{formatted_total}{days_running}")

        if hidden:
            toggle_label = (
                self.metrics_core_module.t("fewer", self.lang)
                if self.expanded
                else f"▸ {self.metrics_core_module.tn('past_projects', len(hidden), self.lang)}"
            )
            lines.append("")
            lines.append(f"[dim]{toggle_label}[/dim]")

        lines.append("")
        lines.append(
            "   ".join(
                f"[{colour}]█[/{colour}] {name}"
                for colour, name in zip(SERIES_MARKUP_COLORS, SERIES_NAMES, strict=True)
            )
        )
        lines.append(
            f"[dim]{self.metrics_core_module.t('legend.open_session_tui', self.lang)}[/dim]"
        )
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


# How many refreshes _restore_focus_after_period_cycle waits for the
# recomposed pane's widget to be mounted before giving up on focusing it.
FOCUS_RESTORE_ATTEMPTS = 5


class MetricsTUIApp(App[None]):
    # Without this Textual titles the terminal window after the class, so
    # it read "MetricsTUIApp" - a class name, in the one place a user reads
    # a product name.
    TITLE = "tokboard"

    # TabbedContent's height is `auto` by default and its panes fill the
    # screen, so it used to claim the whole terminal whatever sat above it:
    # the layout ran taller than the screen by the backfill note's height,
    # and focusing the tree on mount scrolled the note out of sight on both
    # tabs (measured 2026-09-25: 2-4 lines at 80-140 columns). At 1fr it
    # takes what the note leaves. The note itself is held to one line, so
    # it never takes the chart's rows either; the whole text is its tooltip.
    CSS = """
    Horizontal { height: 1fr; }
    TabbedContent { height: 1fr; }
    Tree { width: 62%; }
    SessionDetailPanel { width: 1fr; border-left: solid $panel; padding: 1; }
    .backfill-note {
        color: $text-muted; height: 1; padding: 0 1;
        text-wrap: nowrap; text-overflow: ellipsis;
    }
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
        ("b", "cycle_branch", "key.branch_filter"),
    )

    # `arguments` is required, not an arguments=None default (2026-08-13): the app
    # hands it straight to load_data on every period cycle, so a None here
    # does not fail at construction - it raises AttributeError deep inside
    # load_data the first time someone presses "p".
    def __init__(
        self,
        metrics_core_module: ModuleType,
        data: dict[str, Any],
        arguments: argparse.Namespace,
        **keyword_arguments: Any,
    ) -> None:
        super().__init__(**keyword_arguments)
        self.metrics_core_module = metrics_core_module
        self.data = data
        self.arguments = arguments
        # Read eagerly, not lazily inside action_cycle_period, for the same
        # reason `arguments` is required above: a data dict built by hand without
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
            self.bind(
                key, action, description=self.metrics_core_module.t(message, self.data["lang"])
            )

    def compose(self) -> ComposeResult:
        lang = self.data["lang"]
        note = self.metrics_core_module.describe_backfill_state(
            self.metrics_core_module.load_backfill_state(), lang=lang
        )
        backfill_note = Static(note, classes="backfill-note")
        backfill_note.tooltip = note
        yield backfill_note
        with TabbedContent(initial=self.active_tab_id):
            with (
                TabPane(self.metrics_core_module.t("tab.sessions", lang), id="tab-sessions"),
                Horizontal(),
            ):
                yield build_session_tree(
                    self.data["records"],
                    self.data["subagents_by_session"],
                    self.metrics_core_module,
                    lang=lang,
                )
                yield SessionDetailPanel(self.metrics_core_module, lang=lang)
            with TabPane(self.metrics_core_module.t("tab.panorama", lang), id="tab-panorama"):
                totals = self.metrics_core_module.compute_token_type_totals(self.data["records"])
                # Visibility is set here, as each legend is built, rather than
                # fixed up afterwards: "p" recomposes every widget, and a fix-up
                # queued behind the recompose could leave one frame with all
                # three legends showing (2026-09-23 review).
                shown = legend_visibility(self.chart_mode)
                if self.data.get("weekly_meter"):
                    yield WeeklyMeterPanel(self.data["weekly_meter"], self.metrics_core_module)
                if self.data.get("branches") and not self.data["branches"]["empty"]:
                    yield BranchesPanel(self.data["branches"])
                quota = QuotaSummary(totals, self.metrics_core_module, lang=lang)
                quota.display = shown["quota"]
                yield quota
                composition = CompositionLegend(totals, self.metrics_core_module, lang=lang)
                composition.display = shown["composition"]
                yield composition
                projects = ProjectLegend(
                    self.metrics_core_module,
                    self.data["daily_chart_records"],
                    lang=lang,
                    project_slots=self.data["project_slots"],
                )
                projects.display = shown["projects"]
                yield projects
                # In the mode the reader last picked, not the default: "p"
                # recomposes every widget, and a fresh chart would silently
                # drop the slicing they chose (2026-09-22).
                yield TrendChart(
                    self.metrics_core_module,
                    self.data["daily_chart_records"],
                    mode=self.chart_mode,
                    lang=lang,
                    model_slots=self.data["model_slots"],
                    project_slots=self.data["project_slots"],
                )
                yield ProjectRankingList(
                    self.metrics_core_module,
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
        if not session_data or "session_id" not in session_data:
            return
        session_id = session_data["session_id"]
        subagents = self.data["subagents_by_session"].get(session_id, [])
        # The session's own row, already folded, holds the count its "≥"
        # stands for - so the panel explains exactly what the row shows.
        incomplete = next(
            (
                self.metrics_core_module.incomplete_usage_messages(record)
                for record in self.data["records"]
                if record.get("session_id") == session_id
            ),
            0,
        )
        self.query_one(SessionDetailPanel).show_session(session_id, subagents, incomplete)

    def action_toggle_chart_mode(self) -> None:
        chart = self.query_one(TrendChart)
        chart.toggle_mode()
        self.chart_mode = chart.mode
        self._sync_legend_visibility()

    def action_cycle_branch(self) -> None:
        """Narrow the highlighted project group to its next branch; after the
        last one, back to every branch. In place, so the tree keeps its
        expanded groups and scroll position (unlike "p", this never needs the
        whole app rebuilt)."""
        if self.active_tab_id != "tab-sessions":
            return
        node = self.query_one(Tree).cursor_node
        if node is not None and not (node.data and "records" in node.data):
            node = node.parent
        if node is None or not (node.data and "records" in node.data):
            return
        group_state = node.data
        lang = self.data["lang"]
        view = self.metrics_core_module.describe_branch_filter(
            group_state["records"], group_state["label"], lang
        )
        if view is None:
            self.notify(
                self.metrics_core_module.t(
                    "notify.branch_single", lang, project=group_state["label"]
                )
            )
            return
        keys: list[str | None] = [None, *(chip["key"] for chip in view["chips"])]
        group_state["branch"] = keys[(keys.index(group_state["branch"]) + 1) % len(keys)]
        node.remove_children()
        add_session_leaves(
            node,
            matching_branch(group_state["records"], group_state["branch"]),
            group_state["subagents_by_session"],
            self.metrics_core_module,
            lang=lang,
        )
        node.set_label(group_label(group_state, self.metrics_core_module, lang=lang))
        self.notify(
            self.metrics_core_module.t(
                "notify.branch",
                lang,
                project=group_state["label"],
                branch=view["all"]
                if group_state["branch"] is None
                else next(
                    chip["label"] for chip in view["chips"] if chip["key"] == group_state["branch"]
                ),
            )
        )

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
        ids = [period.id for period in self.metrics_core_module.PERIODS]
        current = self.data.get("period_id", self.metrics_core_module.DEFAULT_PERIOD_ID)
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
        self.data = apply_period(self.base, next_id, self.metrics_core_module)
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
        self.notify(
            self.metrics_core_module.t(
                "notify.period",
                lang,
                name=self.metrics_core_module.period_label(next_id, lang=lang),
            )
        )

    def _restore_focus_after_period_cycle(
        self, attempts_left: int = FOCUS_RESTORE_ATTEMPTS
    ) -> None:
        """Focus a widget that actually belongs to the pane recompose
        reopened on (self.active_tab_id), mirroring the pattern
        action_toggle_tab already uses for the sessions tab.

        recompose() only waits for the widgets it composes directly, not for
        the ones nested inside TabbedContent > TabPane, so on a loaded
        machine this can run before the new widget is mounted - seen once
        in 15 runs beside 128 busy loops (2026-09-23), where query_one
        raised NoMatches and took the whole app down. The widget is looked
        for again after the next refresh, a bounded number of times, and
        if it never appears nothing happens: focus is a nicety, a crash is
        not."""
        widget_type = Tree if self.active_tab_id == "tab-sessions" else TrendChart
        try:
            widget = self.query_one(widget_type)
        except NoMatches:
            if attempts_left > 1:
                self.call_after_refresh(self._restore_focus_after_period_cycle, attempts_left - 1)
            return
        widget.focus()


def main() -> None:
    arguments = build_arg_parser().parse_args()
    metrics_core_module = import_metrics_report()
    data = load_data(arguments, metrics_core_module=metrics_core_module)
    app = MetricsTUIApp(metrics_core_module, data, arguments=arguments)
    app.run()


if __name__ == "__main__":
    main()
