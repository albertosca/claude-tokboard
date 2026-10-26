#!/usr/bin/env python3
"""Report how much tool output enters conversations, by tool and by kind of Bash command.

Read-only: it opens the transcripts under --projects-dir and writes nothing,
neither there nor in the metrics store. Sizes are characters; the token
figure is an estimate (characters / 3). It also simulates what a size cap on
a single result would have trimmed (--bash-cap, --read-cap, in characters).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core.tool_results import DEFAULT_CAPS, summarize, tool_result_sizes

PROJECTS_DIRECTORY = str(Path("~/.claude/projects").expanduser())


def collect(projects_directory: Path) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for transcript_path in sorted(projects_directory.glob("*/*.jsonl")):
        rows.extend(tool_result_sizes(transcript_path, seen))
    return rows


def print_report(summary: dict[str, Any]) -> None:
    print(
        f"{summary['calls']} tool calls, {summary['characters']:,} characters "
        f"(~{summary['estimated_tokens']:,} tokens, estimated), "
        f"{summary['sidechain_share']:.0%} in subagents"
    )
    print("\nBy tool:")
    for entry in summary["tools"]:
        print(
            f"  {entry['tool']:<12} {entry['share']:>4.0%}  {entry['calls']:>6} calls  "
            f"mean {entry['mean_characters']:,}  largest {entry['largest_characters']:,}"
        )
    print("\nBash by kind:")
    for kind in summary["bash_kinds"]:
        print(f"  {kind['kind']:<12} {kind['share']:>4.0%}  {kind['characters']:,} characters")
    print("\nCap simulation (the share of that tool's output a per-result cap would trim):")
    for cap in summary["caps"]:
        print(
            f"  {cap['tool']:<12} cap {cap['cap']:,}: trims {cap['trimmed_characters']:,} "
            f"characters ({cap['trimmed_share']:.1%})"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--projects-dir", default=PROJECTS_DIRECTORY)
    parser.add_argument("--bash-cap", type=int, default=DEFAULT_CAPS["Bash"])
    parser.add_argument("--read-cap", type=int, default=DEFAULT_CAPS["Read"])
    arguments = parser.parse_args()
    rows = collect(Path(arguments.projects_dir))
    print_report(summarize(rows, {"Bash": arguments.bash_cap, "Read": arguments.read_cap}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
