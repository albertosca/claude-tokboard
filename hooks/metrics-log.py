#!/usr/bin/env python3
import glob
import json
import os
import stat
import subprocess
import sys
from datetime import datetime

METRICS_DIR = os.path.expanduser("~/.claude/metrics")
METRICS_FILE = os.path.join(METRICS_DIR, "sessions.jsonl")


def get_tmux_window():
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


def get_internal_name(session_id, sessions_dir=None):
    if not session_id:
        return None
    if sessions_dir is None:
        sessions_dir = os.path.expanduser("~/.claude/sessions")
    candidates = [os.path.join(sessions_dir, f"{os.getppid()}.json")]
    try:
        candidates += glob.glob(os.path.join(sessions_dir, "*.json"))
    except Exception:
        pass
    seen = set()
    for path in candidates:
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("sessionId") == session_id and data.get("nameSource") is None:
            return data.get("name")
    return None


def parse_transcript(path):
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    turn_count = 0
    tool_call_count = 0
    seen_message_ids = set()
    first_timestamp = None
    last_timestamp = None
    git_branch = None

    with open(path, "r") as f:
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

            branch = entry.get("gitBranch")
            if branch:
                git_branch = branch

            if entry.get("type") == "assistant":
                message = entry.get("message") or {}
                # One assistant message can appear many times in a single
                # transcript: resuming or compacting a session replays
                # earlier turns back into the file. Summing every
                # occurrence inflated every number this tool reports -
                # measured 2026-08-11 across the twelve largest
                # transcripts, 47.2% of all token volume was a repeat of
                # an id already counted, an overall 1.89x. Entries with no
                # id can't be deduped, so they are always counted.
                message_id = message.get("id")
                if message_id is not None:
                    if message_id in seen_message_ids:
                        continue
                    seen_message_ids.add(message_id)
                turn_count += 1
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
        "git_branch": git_branch,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "turn_count": turn_count,
        "tool_call_count": tool_call_count,
    }


def build_record(hook_input, parsed):
    session_id = hook_input.get("session_id")
    return {
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
        "tmux_window": get_tmux_window(),
        "internal_name": get_internal_name(session_id),
        "transcript_path": hook_input.get("transcript_path"),
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
        transcript_path = hook_input.get("transcript_path")
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
