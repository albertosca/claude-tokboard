#!/usr/bin/env python3
"""Size of an instruction file over the months of its git history, with a growth alert.

Every request starts with the instruction files in context, so a file that grew
a lot costs tokens in every conversation. Name the file with --file (it must be
tracked in a git repository); each month prints the size it ended on, and a
month that grew by more than --alert-percent over the previous one is flagged.
Read-only. Sizes are bytes; roughly 4 bytes make a token.
"""

from __future__ import annotations

import argparse
import itertools
import subprocess
import sys
from pathlib import Path


def git_output(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments], capture_output=True, text=True, check=False
    )
    return completed.stdout


def monthly_sizes(file_path: Path) -> list[tuple[str, int]]:
    """(month, bytes at the month's last commit), oldest first."""
    repository = file_path.resolve().parent
    name = file_path.name
    log = git_output(repository, "log", "--follow", "--format=%H %aI", "--", name).splitlines()
    last_of_month: dict[str, str] = {}
    for line in log:  # newest first, so the first commit seen per month is its last
        commit, moment = line.split()
        last_of_month.setdefault(moment[:7], commit)
    sizes = []
    for month in sorted(last_of_month):
        size = git_output(repository, "cat-file", "-s", f"{last_of_month[month]}:./{name}").strip()
        if size.isdigit():
            sizes.append((month, int(size)))
    return sizes


def growth_flags(sizes: list[tuple[str, int]], alert_percent: float) -> list[bool]:
    flags = [False]
    for (_, before), (_, after) in itertools.pairwise(sizes):
        flags.append(before > 0 and (after - before) / before * 100 > alert_percent)
    return flags


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--file", required=True)
    parser.add_argument("--alert-percent", type=float, default=25.0)
    arguments = parser.parse_args()
    sizes = monthly_sizes(Path(arguments.file))
    if not sizes:
        print("no history found: is the file tracked in a git repository?", file=sys.stderr)
        return 1
    for (month, size), flagged in zip(
        sizes, growth_flags(sizes, arguments.alert_percent), strict=True
    ):
        marker = f"  <- grew more than {arguments.alert_percent:g}%" if flagged else ""
        print(f"{month}  {size:>9,} bytes  (~{size // 4:,} tokens){marker}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
