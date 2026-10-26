"""How much tool output enters a conversation, by tool and by kind of Bash command.

The metrics store has no tool output: only the transcripts pair a `tool_use`
with its `tool_result` by id. This module reads them (read-only) and answers
where the volume comes from and how little a size cap would trim (backlog
2026-10-07 #13). Sizes are CHARACTERS; tokens are an estimate, not a count.

Claude Code replays earlier turns into a resumed session's new transcript, so
a tool call is counted once per id across every file the caller feeds in.

hooks import metrics_core and may run under a stock macOS Python 3.9.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Characters per token for English-like tool output; an estimate, stated as one.
CHARACTERS_PER_TOKEN = 3

# Caps the simulator tries, in characters: the size a single result is cut to.
DEFAULT_CAPS = {"Bash": 8_000, "Read": 20_000}

SEARCH = "search"
READ = "read"
CLAUDE = "claude"
GIT = "git"
TESTS = "tests"
OTHER = "other"
BASH_KINDS = (SEARCH, READ, CLAUDE, GIT, TESTS, OTHER)

_FIRST_WORD_KIND = {
    "grep": SEARCH,
    "rg": SEARCH,
    "ag": SEARCH,
    "find": SEARCH,
    "fd": SEARCH,
    "cat": READ,
    "head": READ,
    "tail": READ,
    "sed": READ,
    "awk": READ,
    "nl": READ,
    "less": READ,
    "claude": CLAUDE,
    "git": GIT,
    "pytest": TESTS,
    "jest": TESTS,
    "vitest": TESTS,
    "rspec": TESTS,
}
# `<runner> test` and friends: the word after the runner decides.
_TEST_RUNNERS = {"npm": "test", "pnpm": "test", "yarn": "test", "mix": "test", "cargo": "test"}
_ENVIRONMENT_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")


def bash_kind(command: Any) -> str:
    """The kind of a Bash command, from its first real word.

    Leading `VAR=value` assignments are skipped; `python3 -m pytest` and
    `go test` count as tests. A non-string or empty command is "other".
    """
    if not isinstance(command, str):
        return OTHER
    words = [word for word in command.split() if not _ENVIRONMENT_ASSIGNMENT.match(word)]
    if not words:
        return OTHER
    first = Path(words[0]).name
    second = words[1] if len(words) > 1 else ""
    if first in _TEST_RUNNERS and second == _TEST_RUNNERS[first]:
        return TESTS
    if first == "go" and second == "test":
        return TESTS
    if first.startswith("python") and "-m" in words[:-1]:
        return TESTS if words[words.index("-m") + 1] == "pytest" else OTHER
    return _FIRST_WORD_KIND.get(first, OTHER)


def _result_characters(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(block["text"])
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return 0


def _blocks(entry: Any) -> list[Any]:
    message = entry.get("message") if isinstance(entry, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def tool_result_sizes(transcript_path: Path, seen: set[str]) -> Iterator[dict[str, Any]]:
    """One dict per tool call whose result is in this transcript: the tool, the
    Bash kind (None for other tools), the result's characters and the sidechain
    flag. A call id already in `seen` is skipped, and added otherwise."""
    pending: dict[str, tuple[str, str | None]] = {}
    with transcript_path.open("rb") as transcript:
        for raw_line in transcript:
            if b"tool_use" not in raw_line and b"tool_result" not in raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except ValueError:
                continue
            for block in _blocks(entry):
                if not isinstance(block, dict):
                    continue
                identifier = block.get("id") if block.get("type") == "tool_use" else None
                if isinstance(identifier, str) and isinstance(block.get("name"), str):
                    arguments = block.get("input")
                    command = arguments.get("command") if isinstance(arguments, dict) else None
                    kind = bash_kind(command) if block["name"] == "Bash" else None
                    pending[identifier] = (block["name"], kind)
                elif block.get("type") == "tool_result":
                    identifier = block.get("tool_use_id")
                    if (
                        isinstance(identifier, str)
                        and identifier in pending
                        and identifier not in seen
                    ):
                        seen.add(identifier)
                        tool, kind = pending[identifier]
                        yield {
                            "tool": tool,
                            "kind": kind,
                            "characters": _result_characters(block.get("content")),
                            "sidechain": bool(entry.get("isSidechain")),
                        }


def _share(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def summarize(rows: list[dict[str, Any]], caps: dict[str, int] | None = None) -> dict[str, Any]:
    """Totals by tool and by Bash kind, the sidechain share, and what each cap
    in `caps` (tool name -> characters) would have trimmed."""
    caps = DEFAULT_CAPS if caps is None else caps
    total = sum(row["characters"] for row in rows)
    by_tool: dict[str, list[int]] = {}
    by_kind: dict[str, int] = {}
    for row in rows:
        by_tool.setdefault(row["tool"], []).append(row["characters"])
        if row["kind"] is not None:
            by_kind[row["kind"]] = by_kind.get(row["kind"], 0) + row["characters"]
    tools: list[dict[str, Any]] = [
        {
            "tool": tool,
            "calls": len(sizes),
            "characters": sum(sizes),
            "share": _share(sum(sizes), total),
            "mean_characters": sum(sizes) // len(sizes),
            "largest_characters": max(sizes),
        }
        for tool, sizes in by_tool.items()
    ]
    tools.sort(key=lambda entry: (-entry["characters"], entry["tool"]))
    bash_total = sum(by_kind.values())
    kinds: list[dict[str, Any]] = [
        {"kind": kind, "characters": by_kind[kind], "share": _share(by_kind[kind], bash_total)}
        for kind in BASH_KINDS
        if kind in by_kind
    ]
    kinds.sort(key=lambda entry: (-entry["characters"], entry["kind"]))
    simulated = []
    for tool, cap in sorted(caps.items()):
        sizes = by_tool.get(tool, [])
        trimmed = sum(size - cap for size in sizes if size > cap)
        simulated.append(
            {
                "tool": tool,
                "cap": cap,
                "trimmed_characters": trimmed,
                "trimmed_share": _share(trimmed, sum(sizes)),
            }
        )
    return {
        "calls": len(rows),
        "characters": total,
        "estimated_tokens": total // CHARACTERS_PER_TOKEN,
        "sidechain_share": _share(
            sum(row["characters"] for row in rows if row["sidechain"]), total
        ),
        "tools": tools,
        "bash_kinds": kinds,
        "caps": simulated,
    }
