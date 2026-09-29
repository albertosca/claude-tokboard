#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core import append_record as _append_record
from metrics_core import parse_transcript

METRICS_DIRECTORY = Path("~/.claude/metrics").expanduser()
METRICS_FILE = METRICS_DIRECTORY / "sessions.jsonl"


def get_tmux_window() -> str | None:
    if not os.environ.get("TMUX"):
        return None
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#W"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        window = result.stdout.strip()
        return window or None
    except Exception:
        return None


def get_internal_name(session_id: str | None, sessions_directory: Path | None = None) -> str | None:
    if not session_id:
        return None
    if sessions_directory is None:
        sessions_directory = Path("~/.claude/sessions").expanduser()
    candidates = [sessions_directory / f"{os.getppid()}.json"]
    with contextlib.suppress(Exception):
        # Path.glob (unlike glob.glob) also matches dot-prefixed entries; safe
        # here because Claude Code's own naming scheme never produces a
        # leading dot.
        candidates += list(sessions_directory.glob("*.json"))
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            with path.open() as session_file:
                data = json.load(session_file)
        except Exception:
            continue
        if data.get("sessionId") == session_id and data.get("nameSource") is None:
            name = data.get("name")
            return name if isinstance(name, str) else None
    return None


def get_project_root() -> str | None:
    """The project root Claude Code hands every hook, or None.

    The cwd a session is logged with is often a SUBDIRECTORY of the
    project, and nothing in the path says where the project stops - which
    is why the dashboard needs a declared list of project containers to
    name older records. CLAUDE_PROJECT_DIR is the authoritative answer for
    new ones. It stays at the project the session STARTED in even after
    Claude enters a worktree, while the cwd follows - so both are worth
    keeping, and the display layer decides which to use.
    """
    return os.environ.get("CLAUDE_PROJECT_DIR") or None


def build_record(hook_input: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    session_id = hook_input.get("session_id")
    project_root = get_project_root()
    record = {
        "session_id": session_id,
        "timestamp_start": parsed["timestamp_start"],
        "timestamp_end": parsed["timestamp_end"],
        "duration_seconds": parsed["duration_seconds"],
        "project_path": hook_input.get("cwd"),
        "git_branch": parsed["git_branch"],
        "tag": os.environ.get("CLAUDE_METRIC_TAG"),
        "input_tokens": parsed["input_tokens"],
        "output_tokens": parsed["output_tokens"],
        "cache_read_tokens": parsed["cache_read_tokens"],
        "cache_creation_tokens": parsed["cache_creation_tokens"],
        "turn_count": parsed["turn_count"],
        "tool_call_count": parsed["tool_call_count"],
        "tokens_by_model": parsed["tokens_by_model"],
        "tmux_window": get_tmux_window(),
        "internal_name": get_internal_name(session_id),
        "transcript_path": hook_input.get("transcript_path"),
    }
    # Only when there are some: absent means zero, as for every row
    # written before the key existed, and most rows have none.
    if parsed["incomplete_usage_messages"]:
        record["incomplete_usage_messages"] = parsed["incomplete_usage_messages"]
    # Backlog 2026-09-28 #5: absent when the transcript never carried a
    # version at all, same reasoning as incomplete_usage_messages above -
    # the whole historical corpus predates this key. claude_code_version_first
    # is added only when parse_transcript already determined it differs from
    # claude_code_version (a session that spanned an upgrade); it can only
    # be present alongside claude_code_version, never alone.
    if parsed["claude_code_version"] is not None:
        record["claude_code_version"] = parsed["claude_code_version"]
        if parsed["claude_code_version_first"] is not None:
            record["claude_code_version_first"] = parsed["claude_code_version_first"]
    # Added, never set to None: the whole historical corpus has no such
    # key, so "absent" already has to mean "unknown" - a null would be a
    # second spelling of the same thing for every reader to handle.
    if project_root is not None:
        record["project_root"] = project_root
    return record


def append_record(record: dict[str, Any]) -> None:
    _append_record(METRICS_FILE, record)


def main() -> None:
    try:
        hook_input = json.load(sys.stdin)
        transcript_path = hook_input.get("transcript_path")
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
