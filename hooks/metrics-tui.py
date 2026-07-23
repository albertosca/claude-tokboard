#!/usr/bin/env python3
"""Terminal dashboard (TUI) for Claude Code session metrics.

Reuses hooks/metrics-report.py's data functions as-is (see that file
for the data model and docs/superpowers/specs/2026-07-18-terminal-dashboard-design.md
for the full design) - this file is presentation-layer only, exactly
like every HTML dashboard round was. Requires textual + textual-plotext
(see requirements-tui.txt) - run inside this project's own .venv:
    source .venv/bin/activate && python3 hooks/metrics-tui.py
"""
import argparse
import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Footer, Static, TabbedContent, TabPane, Tree
from textual_plotext import PlotextPlot


def import_metrics_report():
    spec = importlib.util.spec_from_file_location(
        "metrics_report",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics-report.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_data(args, mr=None):
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
    records = mr.dedupe_sessions(records)
    records = mr.normalize_project_paths(records)
    records = mr.filter_records(records, tag=args.tag, project=args.project)
    all_time_records = records

    if args.days is not None:
        effective_days = args.days
    elif args.all:
        effective_days = None
    else:
        effective_days = 30

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
    subagent_records = mr.dedupe_subagents(mr.load_records(mr.SUBAGENTS_FILE))
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
        "subagents_by_session": subagents_by_session,
        "project_first_seen": project_first_seen,
        "project_last_seen": project_last_seen,
        "open_projects": open_projects,
    }


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
            if (r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0) > 0
        ]
        if not visible_records:
            continue
        label = mr.project_display_label(project_path)
        total_tokens = sum(
            (r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0)
            for r in visible_records
        )
        group_node = tree.root.add(
            f"{label} ({len(visible_records)} sessões · {mr.format_tokens(total_tokens)} tokens)",
            expand=False,
        )
        for record in visible_records:
            session_id = record.get("session_id") or ""
            date_str = mr.format_date(record.get("timestamp_start"))
            task_label = record.get("task_label") or "-"
            tokens = mr.format_tokens(
                (record.get("input_tokens", 0) or 0) + (record.get("output_tokens", 0) or 0)
            )
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
                if (s.get("input_tokens", 0) or 0) + (s.get("output_tokens", 0) or 0) > 0
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
            style = "bold reverse" if self.highlighted == series else ""
            chip = f"[{style}]{name} · {formatted}[/{style}]" if style else f"{name} · {formatted}"
            chips.append(chip)
        return "   ".join(chips)

    def on_click(self, event):
        x = event.x
        chip_width = self.size.width // 4 if self.size.width else 0
        index = min(x // max(chip_width, 1), 3) if chip_width else 0
        series = self.SERIES[index][0]
        self.highlighted = None if self.highlighted == series else series
        self.post_message(self.Highlight(self.highlighted))


class TrendChart(PlotextPlot):
    SERIES_KEYS = ["series-1", "series-2", "series-3", "series-4"]
    SERIES_COLORS = ["green", "blue", "magenta", "orange"]

    def __init__(self, mr, chart_records, **kwargs):
        super().__init__(**kwargs)
        self.mr = mr
        self.chart_records = chart_records
        self.mode = "pane"
        self.highlighted = None

    def on_mount(self):
        self.render_chart()

    def toggle_mode(self):
        self.mode = "tokens" if self.mode == "pane" else "pane"
        self.render_chart()

    def set_highlight(self, series):
        self.highlighted = series
        if self.mode == "tokens":
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
            totals = [
                sum((r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0) for r in d["records"])
                for d in days
            ]
            self.plt.bar(labels, totals)
            self.plt.title("Tokens por dia (por projeto)")
        else:
            input_totals = [sum(r.get("input_tokens", 0) or 0 for r in d["records"]) for d in days]
            output_totals = [sum(r.get("output_tokens", 0) or 0 for r in d["records"]) for d in days]
            cache_read_totals = [sum(r.get("cache_read_tokens", 0) or 0 for r in d["records"]) for d in days]
            cache_creation_totals = [sum(r.get("cache_creation_tokens", 0) or 0 for r in d["records"]) for d in days]
            if self.highlighted:
                color = [
                    self.SERIES_COLORS[i] if key == self.highlighted else "gray"
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
        self.refresh()


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
    parser.add_argument("--all", action="store_true", help="Show the full history, bypassing the default 30-day active-project window")
    parser.add_argument(
        "--label-budget", type=int, default=100,
        help="Max NEW labels (real claude -p calls) to generate this run; already-cached labels are free and unlimited. Use 0 for none, a negative number for unlimited.",
    )
    return parser


class MetricsTUIApp(App):
    CSS = """
    Horizontal { height: 1fr; }
    Tree { width: 62%; }
    SessionDetailPanel { width: 1fr; border-left: solid $panel; padding: 1; }
    """
    BINDINGS = [
        ("q", "quit", "Sair"),
        ("t", "toggle_chart_mode", "Alterna modo do gráfico"),
        ("m", "toggle_more_projects", "Mais/menos projetos"),
    ]

    def __init__(self, mr, data, **kwargs):
        super().__init__(**kwargs)
        self.mr = mr
        self.data = data

    def compose(self) -> ComposeResult:
        with TabbedContent():
            with TabPane("Sessões", id="tab-sessions"):
                with Horizontal():
                    yield build_session_tree(self.data["records"], self.data["subagents_by_session"], self.mr)
                    yield SessionDetailPanel(self.mr)
            with TabPane("Panorama", id="tab-panorama"):
                totals = self.mr.compute_token_type_totals(self.data["records"])
                yield CompositionLegend(totals, self.mr)
                yield TrendChart(self.mr, self.data["chart_records"])
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

    def on_tree_node_selected(self, event):
        session_data = event.node.data
        if not session_data:
            return
        session_id = session_data["session_id"]
        subagents = self.data["subagents_by_session"].get(session_id, [])
        self.query_one(SessionDetailPanel).show_session(session_id, subagents)

    def action_toggle_chart_mode(self):
        self.query_one(TrendChart).toggle_mode()

    def action_toggle_more_projects(self):
        self.query_one(ProjectRankingList).toggle_more()

    def on_composition_legend_highlight(self, event):
        self.query_one(TrendChart).set_highlight(event.series)


def main():
    args = build_arg_parser().parse_args()
    mr = import_metrics_report()
    data = load_data(args, mr=mr)
    app = MetricsTUIApp(mr, data)
    app.run()


if __name__ == "__main__":
    main()
