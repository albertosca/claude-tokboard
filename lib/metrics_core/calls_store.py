"""Per-call usage rows and rate-limit rows, read from transcripts.

The metrics store keeps per-session totals, which cannot say how much of a
session fell inside which quota window. This module keeps one row per API
response (`calls.jsonl`) and one per rate-limit rejection
(`limit-hits.jsonl`), so window arithmetic works from the store and no
longer needs the transcripts, which Claude Code deletes.

Transcripts repeat every API response, once per content block with growing
output tokens, and a resumed session replays earlier messages into a NEW
file. So the writers are best-effort and the READERS are the guarantee:
read_calls counts a message id once, at its per-field peak, across every
conversation. Never sum the raw file.

hooks import this module and may run under a stock macOS Python 3.9.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .transcript_ingest import model_key

CALLS_FILE_NAME = "calls.jsonl"
LIMIT_HITS_FILE_NAME = "limit-hits.jsonl"
CURSOR_FILE_NAME = "calls_cursor.json"

USAGE_FIELDS = ("input", "output", "cache_read", "cache_5m", "cache_1h")

# Only lines holding one of these are worth parsing; a transcript is mostly
# other entries, and json.loads on every line of a multi-hundred-MB file is
# the slow part.
_LINE_MARKERS = (b'"usage"', b'"quotaLimits"')


def _count(value: Any) -> int:
    """A token count, or 0 for anything that is not a positive integer."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def usage_fields(usage: dict[str, Any]) -> dict[str, int]:
    creation = usage.get("cache_creation")
    tiers = creation if isinstance(creation, dict) else {}
    cache_1h = _count(tiers.get("ephemeral_1h_input_tokens"))
    cache_5m = _count(tiers.get("ephemeral_5m_input_tokens"))
    if cache_1h + cache_5m == 0:
        # No split recorded: count the whole creation as the cheaper tier.
        cache_5m = _count(usage.get("cache_creation_input_tokens"))
    return {
        "input": _count(usage.get("input_tokens")),
        "output": _count(usage.get("output_tokens")),
        "cache_read": _count(usage.get("cache_read_input_tokens")),
        "cache_5m": cache_5m,
        "cache_1h": cache_1h,
    }


def _without_none(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value is not None}


def call_row(entry: dict[str, Any], conversation: str, byte_offset: int) -> dict[str, Any] | None:
    if entry.get("type") != "assistant":
        return None
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    fields = usage_fields(usage)
    if not any(fields.values()):
        return None
    message_id = (
        message.get("id")
        or entry.get("requestId")
        or entry.get("uuid")
        or f"@{conversation}:{byte_offset}"
    )
    row = _without_none(
        {
            "session_id": entry.get("sessionId"),
            "conversation": conversation,
            "message_id": str(message_id),
            "timestamp": entry.get("timestamp"),
            "model": model_key(message),
            "sidechain": 1 if entry.get("isSidechain") else 0,
            "claude_code_version": entry.get("version") or None,
        }
    )
    row.update(fields)
    return row


def limit_hit_row(entry: dict[str, Any]) -> dict[str, Any] | None:
    limits = entry.get("quotaLimits")
    if not isinstance(limits, dict):
        return None
    return _without_none(
        {
            "timestamp": entry.get("timestamp"),
            "rate_limit_type": limits.get("rateLimitType"),
            "resets_at": limits.get("resetsAt"),
            "status": limits.get("status"),
            "overage_status": limits.get("overageStatus"),
            "overage_disabled_reason": limits.get("overageDisabledReason"),
        }
    )


def read_region(
    transcript_path: str, start_offset: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """(call rows, hit rows, offset after the last COMPLETE line) from `start_offset`.

    A transcript being written ends in a partial line; the offset stops
    before it, so the next run reads it whole. An offset beyond the file's
    size means the file was rewritten: read it from the start.
    """
    conversation = Path(transcript_path).stem
    calls: dict[str, dict[str, Any]] = {}
    hits: list[dict[str, Any]] = []
    with Path(transcript_path).open("rb") as transcript:
        size = os.fstat(transcript.fileno()).st_size
        offset = start_offset if 0 <= start_offset <= size else 0
        transcript.seek(offset)
        for raw_line in transcript:
            if not raw_line.endswith(b"\n"):
                break
            line_offset = offset
            offset += len(raw_line)
            if not any(marker in raw_line for marker in _LINE_MARKERS):
                continue
            try:
                entry = json.loads(raw_line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            hit = limit_hit_row(entry)
            if hit is not None:
                hits.append(hit)
            row = call_row(entry, conversation, line_offset)
            if row is None:
                continue
            known = calls.get(row["message_id"])
            if known is None:
                calls[row["message_id"]] = row
                continue
            for field in USAGE_FIELDS:
                known[field] = max(known[field], row[field])
    return list(calls.values()), hits, offset
