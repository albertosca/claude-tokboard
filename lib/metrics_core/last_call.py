"""Recover a subagent's last call from the result its parent session recorded.

Since Claude Code 2.1.278 a subagent transcript often ends on a streaming
placeholder: the last message's output sits at a few tokens, and nothing in the
transcript holds the real figure. The parent session, though, records the
finished agent's result with the agent id and a `usage` block. Measured
2026-10-25 over every such result on one disk, `totalTokens` equals the sum of
ONE call's four fields and `usage.iterations` has one entry: the block is the
agent's LAST call, never a cumulative total. So it can repair that last
message and nothing earlier - which is all this module claims.

The repair is gated three ways, each of which skips (and names the reason)
instead of guessing: the parent block must be a single call, its other three
fields must equal the transcript's last message (same call), and the stored
row must be exactly the transcript's final snapshot.

hooks import metrics_core and may run under a stock macOS Python 3.9.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .transcript_ingest import TOKEN_FIELDS, USAGE_KEYS, model_key, parse_transcript, usage_values

OUTPUT_INDEX = TOKEN_FIELDS.index("output_tokens")

# Why a row was left alone.
NO_TRANSCRIPT = "its transcript is not on disk"
NO_PARENT_RESULT = "its parent session holds no result for it"
NOT_ONE_CALL = "the parent result is not a single call"
DIFFERENT_CALL = "the parent result is not the transcript's last call"
NOT_FINAL_ROW = "the stored row is not the transcript's final snapshot"
MAP_MISMATCH = "the row's model map does not hold the last call's model"
NOTHING_TO_GAIN = "the last call's output is already complete"
ALREADY_RECOVERED = "already recovered"


def parent_session_path(transcript_path: str) -> Path:
    """`<slug>/<session>/subagents/agent-<id>.jsonl` -> `<slug>/<session>.jsonl`."""
    path = Path(transcript_path)
    return path.parents[2] / f"{path.parents[1].name}.jsonl"


def parent_last_calls(parent_path: Path) -> dict[str, tuple[int, ...] | None]:
    """agent id -> the four token fields of the parent's recorded result, or
    None when that block is not provably one call (its `totalTokens` is not
    the sum of the four fields, or it lists other than one iteration)."""
    found: dict[str, tuple[int, ...] | None] = {}
    if not parent_path.is_file():
        return found
    with parent_path.open() as parent_file:
        for line in parent_file:
            if '"agentId"' not in line or '"totalTokens"' not in line:
                continue
            try:
                result = json.loads(line).get("toolUseResult")
            except ValueError:
                continue
            usage = result.get("usage") if isinstance(result, dict) else None
            agent_id = result.get("agentId") if isinstance(result, dict) else None
            if not isinstance(usage, dict) or not isinstance(agent_id, str):
                continue
            values = tuple(usage.get(USAGE_KEYS[field], 0) or 0 for field in TOKEN_FIELDS)
            single = (
                all(isinstance(value, int) for value in values)
                and result.get("totalTokens") == sum(values)
                and len(usage.get("iterations") or []) == 1
            )
            found[agent_id] = values if single else None
    return found


def last_message(transcript_path: str) -> tuple[str, tuple[int, ...]] | None:
    """(model bucket, per-field peak) of the message first seen LAST, or None."""
    order: list[Any] = []
    peaks: dict[Any, tuple[int, ...]] = {}
    models: dict[Any, str] = {}
    with Path(transcript_path).open() as transcript:
        for line in transcript:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message") if entry.get("type") == "assistant" else None
            if not isinstance(message, dict) or message.get("id") is None:
                continue
            identifier = message["id"]
            values = usage_values(message)
            if identifier not in peaks:
                order.append(identifier)
                models[identifier] = model_key(message)
                peaks[identifier] = values
            else:
                peaks[identifier] = tuple(
                    max(peaks[identifier][position], values[position])
                    for position in range(len(values))
                )
    if not order:
        return None
    return models[order[-1]], peaks[order[-1]]


def plan_last_call(
    record: dict[str, Any], parents: dict[str, dict[str, tuple[int, ...] | None]]
) -> tuple[dict[str, Any] | None, str | None]:
    """(the row with its last call recovered, None) or (None, why not).

    `parents` caches parent_last_calls per parent path, filled here."""
    transcript_path = record.get("agent_transcript_path")
    agent_id = record.get("agent_id")
    if not isinstance(transcript_path, str) or not Path(transcript_path).is_file():
        return None, NO_TRANSCRIPT
    parent_path = parent_session_path(transcript_path)
    if str(parent_path) not in parents:
        parents[str(parent_path)] = parent_last_calls(parent_path)
    if agent_id not in parents[str(parent_path)]:
        return None, NO_PARENT_RESULT
    reported = parents[str(parent_path)][agent_id]
    if reported is None:
        return None, NOT_ONE_CALL
    last = last_message(transcript_path)
    if last is None:
        return None, NO_TRANSCRIPT
    model, peak = last
    others = [index for index in range(len(TOKEN_FIELDS)) if index != OUTPUT_INDEX]
    if any(reported[index] != peak[index] for index in others):
        return None, DIFFERENT_CALL
    if reported[OUTPUT_INDEX] < peak[OUTPUT_INDEX]:
        return None, DIFFERENT_CALL
    gain = reported[OUTPUT_INDEX] - peak[OUTPUT_INDEX]
    if gain == 0:
        return None, NOTHING_TO_GAIN
    parsed = parse_transcript(transcript_path)
    stored = [record.get(field, 0) or 0 for field in TOKEN_FIELDS]
    final = [parsed[field] for field in TOKEN_FIELDS]
    recovered = list(final)
    recovered[OUTPUT_INDEX] += gain
    if stored == recovered:
        return None, ALREADY_RECOVERED
    if stored != final:
        return None, NOT_FINAL_ROW
    updated = dict(record)
    updated["output_tokens"] = stored[OUTPUT_INDEX] + gain
    by_model = record.get("tokens_by_model")
    if isinstance(by_model, dict):
        if model not in by_model:
            return None, MAP_MISMATCH
        copied = {key: dict(bucket) for key, bucket in by_model.items()}
        copied[model]["output_tokens"] += gain
        updated["tokens_by_model"] = copied
    return updated, None
