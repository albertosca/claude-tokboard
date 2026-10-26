#!/usr/bin/env python3
"""Print Claude Code's own /usage report next to nothing else: opt-in, run by hand.

`claude -p "/usage"` is the only non-interactive way to read the plan's
server-side percentages. It makes a network request through the Claude Code
binary (the plugin itself sends nothing), its text is approximate and may
change between versions, and it counts sessions on this machine only. So this
script is never run by a hook or a dashboard: you run it when you want the
official figure to compare with the dashboards' own cache-creation meter.
It writes nothing.
"""

from __future__ import annotations

import re
import subprocess
import sys

USAGE_COMMAND = ["claude", "-p", "/usage"]
TIMEOUT_SECONDS = 90
_METER_LINE = re.compile(
    r"^(?P<label>Current [^:]+): (?P<percent>\d+)% used(?: · (?P<resets>.*))?$"
)


def parse_meters(text: str) -> list[dict[str, str]]:
    """The `Current ...: N% used · resets ...` lines, in order, as label, percent, resets."""
    meters = []
    for line in text.splitlines():
        match = _METER_LINE.match(line.strip())
        if match:
            meters.append(
                {
                    "label": match["label"],
                    "percent": match["percent"],
                    "resets": match["resets"] or "",
                }
            )
    return meters


def read_usage() -> str | None:
    """The report text, or None when the command is missing, fails or times out."""
    try:
        completed = subprocess.run(
            USAGE_COMMAND, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def main() -> int:
    text = read_usage()
    if text is None:
        print("could not read /usage: is `claude` on PATH and signed in?", file=sys.stderr)
        return 1
    meters = parse_meters(text)
    for meter in meters:
        print(f"{meter['label']}: {meter['percent']}% used ({meter['resets']})")
    if not meters:
        print("no meter lines found; the report format may have changed. Raw output:")
    print()
    print(text.rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
