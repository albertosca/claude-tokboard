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
