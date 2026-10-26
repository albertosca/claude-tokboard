#!/usr/bin/env python3
"""Decompose the fixed context of a Claude Code request by switching parts off.

Nine variants each send one minimal request through `claude -p`; the context
size the API reports (uncached + cache write + cache read) is compared across
variants to attribute the start-up cost to skills, MCP servers, user and
project settings, tool definitions and the default system prompt. Run it right
after changing a CLAUDE.md, plugin or hook to see the delta.

COST: it makes nine real API calls with your account, so it never runs from a
hook or a dashboard and needs --run; without it the variants are only listed.
It writes nothing. It runs from the current directory, so project settings
are the ones of the project you are in.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

PROMPT = "Reply with the single word OK."
NO_SETTINGS = ["--setting-sources", ""]
NO_SKILLS = ["--disable-slash-commands"]
NO_MCP = ["--strict-mcp-config"]
NO_TOOLS = ["--tools", ""]
VARIANTS: list[tuple[str, list[str]]] = [
    ("full", []),
    ("no skills", NO_SKILLS),
    ("no MCP servers", NO_MCP),
    ("settings: user only", ["--setting-sources", "user"]),
    ("settings: project only", ["--setting-sources", "project"]),
    ("settings: none", NO_SETTINGS),
    ("bare", NO_SETTINGS + NO_SKILLS + NO_MCP),
    ("bare, no tools", NO_SETTINGS + NO_SKILLS + NO_MCP + NO_TOOLS),
    (
        "bare, no tools, minimal prompt",
        NO_SETTINGS + NO_SKILLS + NO_MCP + NO_TOOLS + ["--system-prompt", "Reply OK."],
    ),
]
ATTRIBUTIONS = [
    ("skills list", "full", "no skills"),
    ("MCP servers", "full", "no MCP servers"),
    ("user settings (CLAUDE.md, hooks, plugins)", "settings: user only", "settings: none"),
    ("project settings", "settings: project only", "settings: none"),
    ("settings, skills and MCP together", "full", "bare"),
    ("tool definitions", "bare", "bare, no tools"),
    ("default system prompt", "bare, no tools", "bare, no tools, minimal prompt"),
]


def context_size(payload: dict[str, Any]) -> int:
    usage = payload["usage"]
    return sum(
        usage.get(field, 0)
        for field in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )


def run_variant(flags: list[str], model: str) -> int | None:
    command = [
        "claude",
        "-p",
        "--model",
        model,
        "--no-session-persistence",
        "--output-format",
        "json",
        *flags,
    ]
    try:
        completed = subprocess.run(
            command, input=PROMPT, capture_output=True, text=True, timeout=300, check=False
        )
        return context_size(json.loads(completed.stdout)) if completed.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return None


def attribute(sizes: dict[str, int | None]) -> list[tuple[str, int | None]]:
    rows = []
    for label, larger, smaller in ATTRIBUTIONS:
        big, small = sizes.get(larger), sizes.get(smaller)
        rows.append((label, None if big is None or small is None else big - small))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--run", action="store_true", help="make the nine real API calls")
    parser.add_argument("--model", default="claude-sonnet-5-5")
    arguments = parser.parse_args()
    if not arguments.run:
        print("dry run: nine requests would be sent, one per variant:")
        for name, flags in VARIANTS:
            print(f"  {name:<32} {' '.join(flags) or '(no flags)'}")
        print("add --run to send them (it uses your account)")
        return 0
    sizes: dict[str, int | None] = {}
    for name, flags in VARIANTS:
        sizes[name] = run_variant(flags, arguments.model)
        print(f"{name:<32} {sizes[name] if sizes[name] is not None else 'ERROR'}", flush=True)
    print("\nAttribution by difference (tokens):")
    for label, delta in attribute(sizes):
        print(f"  {label:<44}{'-' if delta is None else delta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
