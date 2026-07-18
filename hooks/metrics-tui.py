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

from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static, Tree
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

    records = mr.load_records(mr.METRICS_FILE)
    records = mr.filter_records(records, tag=args.tag, project=args.project)
    all_time_records = records

    if args.days is not None:
        effective_days = args.days
    elif args.all:
        effective_days = None
    else:
        effective_days = 30

    records = mr.filter_by_days(records, effective_days)
    if effective_days is None:
        records = records[-args.limit:]
        chart_records = records
    else:
        chart_records = records
        records = records[-args.limit:]

    subagent_records = mr.load_records(mr.SUBAGENTS_FILE)
    agent_label_cache = mr.load_agent_label_cache()
    for subagent in subagent_records:
        subagent["agent_label"] = mr.get_agent_label(subagent, agent_label_cache)
    mr.save_agent_label_cache(agent_label_cache)
    subagents_by_session = mr.group_subagents_by_session(subagent_records)

    label_cache = mr.load_label_cache()
    for record in records:
        record["task_label"] = mr.get_task_label(record, label_cache)
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


def build_session_tree(records, mr):
    group_colors = mr.assign_group_colors(records)
    groups = mr.build_project_groups(records, group_colors)
    tree = Tree("Sessões")
    tree.show_root = False
    for group in groups:
        project_path = group["project"]
        label = mr.project_display_label(project_path)
        total_tokens = sum(
            (r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0)
            for r in group["records"]
        )
        group_node = tree.root.add(
            f"{label} ({len(group['records'])} sessões · {mr.format_tokens(total_tokens)} tokens)",
            expand=False,
        )
        for record in group["records"]:
            session_id = record.get("session_id") or ""
            date_str = mr.format_date(record.get("timestamp_start"))
            task_label = record.get("task_label") or "-"
            tokens = mr.format_tokens(
                (record.get("input_tokens", 0) or 0) + (record.get("output_tokens", 0) or 0)
            )
            duration = record.get("duration_seconds")
            duration_str = f"{duration:.0f}s" if duration is not None else "-"
            group_node.add_leaf(
                f"{session_id[:12]:<14}{date_str:<14}{task_label[:28]:<30}{tokens:>8}   {duration_str:>7}",
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
    def __init__(self, mr, chart_records, **kwargs):
        super().__init__(**kwargs)
        self.mr = mr
        self.chart_records = chart_records
        self.mode = "pane"

    def on_mount(self):
        self.render_chart()

    def toggle_mode(self):
        self.mode = "tokens" if self.mode == "pane" else "pane"
        self.render_chart()

    def render_chart(self):
        days = self.mr.aggregate_by_day(self.chart_records)
        self.plt.clear_data()
        if len(days) < 2:
            self.refresh()
            return
        labels = [d["day"][5:] for d in days]  # MM-DD
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
            self.plt.stacked_bar(
                labels,
                [input_totals, output_totals, cache_read_totals, cache_creation_totals],
                labels=["input", "output", "cache read", "cache creation"],
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
        if not totals:
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
    parser.add_argument("-n", "--limit", type=int, default=20, help="Number of most recent sessions to show")
    parser.add_argument("--days", type=int, default=None, help="Only include sessions from the last N days")
    parser.add_argument("--all", action="store_true", help="Show the full history, bypassing the default 30-day active-project window")
    return parser


def main():
    args = build_arg_parser().parse_args()
    data = load_data(args)
    print(f"OK: loaded {len(data['records'])} sessions, {len(data['chart_records'])} chart_records")


if __name__ == "__main__":
    main()
