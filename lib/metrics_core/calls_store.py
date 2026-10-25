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
import stat
import tempfile
from collections.abc import Iterable, Iterator
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


# Whole rows are written in chunks of at most this many bytes, one write(2)
# each, so two sessions appending at once can interleave at a row boundary
# at worst, never inside a row.
APPEND_CHUNK_BYTES = 4096


def append_rows(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    lines = [(json.dumps(row, separators=(",", ":")) + "\n").encode() for row in rows]
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("ab", buffering=0) as handle:
        chunk = b""
        for line in lines:
            if chunk and len(chunk) + len(line) > APPEND_CHUNK_BYTES:
                handle.write(chunk)
                chunk = b""
            chunk += line
        handle.write(chunk)
    if is_new:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def load_cursor(metrics_directory: Path) -> dict[str, int]:
    """transcript path -> byte offset already logged. Lenient: a damaged
    cursor only means re-reading, which the readers' dedup absorbs."""
    try:
        loaded = json.loads((metrics_directory / CURSOR_FILE_NAME).read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        key: value
        for key, value in loaded.items()
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    }


def save_cursor(metrics_directory: Path, cursor: dict[str, int]) -> None:
    # Pruned on every save so the file stays as small as the transcripts left.
    # Two hooks saving at once can lose one entry; that transcript is then
    # read again from 0 and its rows deduplicated on read.
    alive = {path: offset for path, offset in cursor.items() if Path(path).exists()}
    metrics_directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=metrics_directory, prefix=".calls_cursor.")
    with os.fdopen(descriptor, "w") as handle:
        json.dump(alive, handle)
    Path(temporary_name).replace(metrics_directory / CURSOR_FILE_NAME)


def read_jsonl_rows(path: Path) -> Iterator[dict[str, Any]]:
    try:
        handle = path.open("rb")
    except OSError:
        return
    with handle:
        for raw_line in handle:
            try:
                row = json.loads(raw_line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def read_calls(path: Path) -> list[dict[str, Any]]:
    """Every API response once, at its per-field peak, oldest first."""
    merged: dict[str, dict[str, Any]] = {}
    for row in read_jsonl_rows(path):
        message_id = row.get("message_id")
        if not isinstance(message_id, str) or not isinstance(row.get("timestamp"), str):
            continue
        counts = {field: _count(row.get(field)) for field in USAGE_FIELDS}
        known = merged.get(message_id)
        if known is None:
            merged[message_id] = {**row, **counts}
            continue
        for field in USAGE_FIELDS:
            known[field] = max(known[field], counts[field])
    return sorted(merged.values(), key=lambda row: row["timestamp"])


def read_limit_hits(path: Path) -> list[dict[str, Any]]:
    """Every rejection once, oldest first."""
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in read_jsonl_rows(path):
        timestamp = row.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        key = (timestamp, row.get("rate_limit_type"), row.get("resets_at"))
        unique.setdefault(key, row)
    return sorted(unique.values(), key=lambda row: row["timestamp"])


def log_transcript(transcript_path: str, metrics_directory: Path) -> None:
    """Append what a transcript gained since the last call. Rows go in
    BEFORE the cursor moves, so a crash in between duplicates, never loses."""
    cursor = load_cursor(metrics_directory)
    rows, hits, offset = read_region(transcript_path, cursor.get(transcript_path, 0))
    append_rows(metrics_directory / CALLS_FILE_NAME, rows)
    append_rows(metrics_directory / LIMIT_HITS_FILE_NAME, hits)
    cursor[transcript_path] = offset
    save_cursor(metrics_directory, cursor)
