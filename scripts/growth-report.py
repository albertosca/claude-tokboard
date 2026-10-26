#!/usr/bin/env python3
"""Per ISO week: API calls, cache creation, commits and the ratios between them.

Separates "more work" from "heavier process": cache creation per commit rising
while commits stay flat means each unit of work got heavier. Read-only. The
commits come from the repositories you name with --repo (git log by author
date, all branches); the calls come from calls.jsonl through read_calls.
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core.calls_store import CALLS_FILE_NAME, read_calls

METRICS_DIRECTORY = str(Path("~/.claude/metrics").expanduser())


def week_of(moment: datetime.datetime) -> str:
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def calls_by_week(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    weeks: dict[str, dict[str, int]] = {}
    for row in rows:
        moment = datetime.datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
        entry = weeks.setdefault(week_of(moment), {"calls": 0, "creation": 0})
        entry["calls"] += 1
        entry["creation"] += row["cache_5m"] + row["cache_1h"]
    return weeks


def commits_by_week(repository: Path) -> dict[str, int]:
    completed = subprocess.run(
        ["git", "-C", str(repository), "log", "--all", "--format=%aI"],
        capture_output=True,
        text=True,
        check=False,
    )
    weeks: dict[str, int] = {}
    for line in completed.stdout.splitlines():
        label = week_of(datetime.datetime.fromisoformat(line))
        weeks[label] = weeks.get(label, 0) + 1
    return weeks


def build_table(calls: dict[str, dict[str, int]], commits: dict[str, int]) -> list[dict[str, Any]]:
    table = []
    for label in sorted(calls):
        made = calls[label]
        count = commits.get(label, 0)
        table.append(
            {
                "week": label,
                "calls": made["calls"],
                "creation": made["creation"],
                "commits": count,
                "calls_per_commit": made["calls"] / count if count else None,
                "creation_per_commit": made["creation"] / count if count else None,
            }
        )
    return table


def format_ratio(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--metrics-dir", default=METRICS_DIRECTORY)
    parser.add_argument("--repo", action="append", default=[], help="a git repository; repeatable")
    arguments = parser.parse_args()
    commits: dict[str, int] = {}
    for repository in arguments.repo:
        for label, count in commits_by_week(Path(repository)).items():
            commits[label] = commits.get(label, 0) + count
    rows = read_calls(Path(arguments.metrics_dir) / CALLS_FILE_NAME)
    print(
        f"{'week':<10}{'calls':>8}{'creation':>14}{'commits':>9}{'calls/commit':>14}{'creation/commit':>17}"
    )
    for entry in build_table(calls_by_week(rows), commits):
        print(
            f"{entry['week']:<10}{entry['calls']:>8,}{entry['creation']:>14,}{entry['commits']:>9}"
            f"{format_ratio(entry['calls_per_commit']):>14}"
            f"{format_ratio(entry['creation_per_commit']):>17}"
        )
    if not arguments.repo:
        print("no --repo given: the commit columns are empty")
    return 0


if __name__ == "__main__":
    sys.exit(main())
