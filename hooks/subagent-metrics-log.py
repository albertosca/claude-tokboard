#!/usr/bin/env python3
import json
import os
import stat
import sys
from datetime import datetime

METRICS_DIR = os.path.expanduser("~/.claude/metrics")
METRICS_FILE = os.path.join(METRICS_DIR, "subagents.jsonl")


def parse_transcript(path):
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    tool_call_count = 0
    seen_message_ids = set()
    first_timestamp = None
    last_timestamp = None

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            timestamp = entry.get("timestamp")
            if timestamp:
                if first_timestamp is None:
                    first_timestamp = timestamp
                last_timestamp = timestamp

            if entry.get("type") == "assistant":
                message = entry.get("message") or {}
                # Same replay problem the session hook guards against: a
                # transcript can hold several copies of one assistant
                # message, and counting each one inflates every figure
                # (see metrics-log.py for the measured 1.89x). Entries
                # with no id can't be deduped, so they are always counted.
                message_id = message.get("id")
                if message_id is not None:
                    if message_id in seen_message_ids:
                        continue
                    seen_message_ids.add(message_id)
                usage = message.get("usage") or {}
                input_tokens += usage.get("input_tokens", 0) or 0
                output_tokens += usage.get("output_tokens", 0) or 0
                cache_read_tokens += usage.get("cache_read_input_tokens", 0) or 0
                cache_creation_tokens += usage.get("cache_creation_input_tokens", 0) or 0

                content = message.get("content") or []
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_call_count += 1

    duration_seconds = None
    if first_timestamp and last_timestamp:
        try:
            start = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
            end = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
            duration_seconds = (end - start).total_seconds()
        except ValueError:
            duration_seconds = None

    return {
        "timestamp_start": first_timestamp,
        "timestamp_end": last_timestamp,
        "duration_seconds": duration_seconds,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "tool_call_count": tool_call_count,
    }


def build_record(hook_input, parsed):
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


def append_record(record):
    os.makedirs(METRICS_DIR, exist_ok=True)
    is_new = not os.path.exists(METRICS_FILE)
    with open(METRICS_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
    if is_new:
        os.chmod(METRICS_FILE, stat.S_IRUSR | stat.S_IWUSR)


def main():
    try:
        hook_input = json.load(sys.stdin)
        transcript_path = hook_input.get("agent_transcript_path")
        if not transcript_path or not os.path.exists(transcript_path):
            return
        parsed = parse_transcript(transcript_path)
        record = build_record(hook_input, parsed)
        append_record(record)
    except Exception:
        return


if __name__ == "__main__":
    main()
    sys.exit(0)
