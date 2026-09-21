#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core import append_record as _append_record
from metrics_core import parse_transcript

METRICS_DIR = Path("~/.claude/metrics").expanduser()
METRICS_FILE = METRICS_DIR / "subagents.jsonl"


def get_project_root() -> str | None:
    """The project root Claude Code hands every hook, or None.

    The cwd a subagent is logged with is often a SUBDIRECTORY of the
    project, and nothing in the path says where the project stops - which
    is why the dashboard needs a declared list of project containers to
    name older records. CLAUDE_PROJECT_DIR is the authoritative answer for
    new ones. It stays at the project the session STARTED in even after
    Claude enters a worktree, while the cwd follows - so both are worth
    keeping, and the display layer decides which to use.
    """
    return os.environ.get("CLAUDE_PROJECT_DIR") or None


def build_record(hook_input: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    project_root = get_project_root()
    record = {
        "session_id": hook_input.get("session_id"),
        "agent_id": hook_input.get("agent_id"),
        "agent_type": hook_input.get("agent_type"),
        "project_path": hook_input.get("cwd"),
        "timestamp_start": parsed["timestamp_start"],
        "timestamp_end": parsed["timestamp_end"],
        "duration_seconds": parsed["duration_seconds"],
        "input_tokens": parsed["input_tokens"],
        "output_tokens": parsed["output_tokens"],
        "cache_read_tokens": parsed["cache_read_tokens"],
        "cache_creation_tokens": parsed["cache_creation_tokens"],
        "tool_call_count": parsed["tool_call_count"],
        "tokens_by_model": parsed["tokens_by_model"],
        "agent_transcript_path": hook_input.get("agent_transcript_path"),
    }
    # See the sibling hook: added, never set to None.
    if project_root is not None:
        record["project_root"] = project_root
    return record


def append_record(record: dict[str, Any]) -> None:
    _append_record(METRICS_FILE, record)


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
        transcript_path = hook_input.get("agent_transcript_path")
        if not transcript_path or not Path(transcript_path).exists():
            return
        parsed = parse_transcript(transcript_path)
        record = build_record(hook_input, parsed)
        append_record(record)
    except Exception:
        return


if __name__ == "__main__":
    main()
    sys.exit(0)
