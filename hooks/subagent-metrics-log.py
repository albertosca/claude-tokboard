#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from metrics_core import append_record as _append_record
from metrics_core import parse_transcript

METRICS_DIR = Path("~/.claude/metrics").expanduser()
METRICS_FILE = METRICS_DIR / "subagents.jsonl"


def build_record(hook_input: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    return {
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
        "agent_transcript_path": hook_input.get("agent_transcript_path"),
    }


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
