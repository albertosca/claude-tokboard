"""Transcript parsing and record-appending shared by hooks/metrics-log.py
and hooks/subagent-metrics-log.py.

Backlog item, 2026-09-01: the two hooks shared ~80% of this logic,
copy-pasted rather than shared, which meant a fix applied to one (the
seen_message_ids dedup, or the is_new chmod guard) would not reach the
other unless someone remembered to apply it twice. Extracted here so a
fix lands once - each hook still keeps its own build_record() (the
actual output shape genuinely differs) and its own main(), so nothing
about how either hook behaves as a standalone script changes.

parse_transcript() always computes the full superset of fields (including
git_branch and turn_count, which only the session hook's build_record
uses) rather than taking a flag to skip them - the subagent hook simply
ignores the two extra keys, which is simpler and cheaper than a bimodal
API for a handful of local variables in one loop.
"""

from __future__ import annotations

import json
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

# The record's four token field names, and which transcript `usage` key
# feeds each one. Defined here rather than in backfill.py because this is
# the module that reads a transcript in the first place - backfill.py
# reconstructs exactly this mapping and imports it from here, so there is
# one copy and the dependency points from the heavy offline module to the
# light ingest module, never the reverse.
#
# That is a direction guarantee, NOT an import-cost one: a hook importing
# `metrics_core` (which is how both hooks import parse_transcript) still
# pulls backfill.py, bisect and hashlib in, because the package's
# __init__.py imports .backfill for the dashboards. Measured 2026-09-16.
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
USAGE_KEYS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cache_read_input_tokens",
    "cache_creation_tokens": "cache_creation_input_tokens",
}

# The pseudo-model Claude Code writes for entries it generated without a
# model call. Measured over every transcript on one machine (534 entries):
# always zero in all four token fields. It is filtered by CONTRIBUTION, not
# by name - see model_key's callers - so a future synthetic entry carrying
# tokens still reconciles instead of vanishing.
SYNTHETIC_MODEL = "<synthetic>"


def model_key(message: dict[str, Any]) -> str:
    """The bucket an assistant message's tokens belong to.

    A non-string `model` (absent, null, malformed) becomes "unknown" rather
    than being skipped: every message must land in some bucket, or the
    per-model sums stop reconciling with the record's own four totals with
    no signal that they have.
    """
    raw = message.get("model")
    return raw if isinstance(raw, str) else "unknown"


def parse_transcript(path: str) -> dict[str, Any]:
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
    tokens_by_model: dict[str, dict[str, int]] = {}

    with Path(path).open() as f:
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

                # Per-message attribution, keyed by the model that produced
                # the message (the same session routinely uses several).
                # A message with no "model" field lands under "unknown"
                # rather than being skipped: every message must land in
                # some bucket, so the per-model sums equal the totals above
                # as a property of this loop's shape, not of what happens
                # to be present in a given transcript. `<synthetic>` is the
                # one skip, and it is conditional for exactly that reason:
                # it is a pseudo-model measured as carrying zero in all four
                # fields, so bucketing it would only ever add a
                # permanently-empty series downstream - but a `<synthetic>`
                # message that did carry tokens would break the invariant
                # silently (its usage is already in the four totals above),
                # so it is bucketed as soon as it carries anything. The
                # invariant is therefore structural, not contingent on that
                # measurement holding forever. Raw model id, deliberately:
                # shortening is a display concern, and storing raw means a
                # display change never needs a re-backfill.
                model = model_key(message)
                if model != SYNTHETIC_MODEL or any(
                    usage.get(USAGE_KEYS[field], 0) or 0 for field in TOKEN_FIELDS
                ):
                    bucket = tokens_by_model.get(model)
                    if bucket is None:
                        bucket = tokens_by_model[model] = dict.fromkeys(TOKEN_FIELDS, 0)
                    for field in TOKEN_FIELDS:
                        bucket[field] += usage.get(USAGE_KEYS[field], 0) or 0

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
        "tokens_by_model": tokens_by_model,
        "turn_count": turn_count,
        "tool_call_count": tool_call_count,
    }


def append_record(metrics_file: Path, record: dict[str, Any]) -> None:
    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    is_new = not metrics_file.exists()
    with metrics_file.open("a") as f:
        f.write(json.dumps(record) + "\n")
    if is_new:
        metrics_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
