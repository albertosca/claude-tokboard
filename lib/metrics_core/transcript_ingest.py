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


def usage_values(message: dict[str, Any]) -> tuple[int, ...]:
    """The four token counts of one assistant message, in TOKEN_FIELDS order."""
    usage = message.get("usage") or {}
    return tuple(usage.get(USAGE_KEYS[field], 0) or 0 for field in TOKEN_FIELDS)


def usage_increment(
    peaks: dict[Any, tuple[int, ...]], message_id: Any, values: tuple[int, ...]
) -> tuple[int, ...]:
    """What one assistant entry adds to a running total: a message counts at
    its PER-FIELD PEAK across every entry that carries its id.

    One message id appears in a transcript many times, for two reasons. A
    resumed or compacted session REPLAYS earlier turns, with identical usage
    (measured 2026-08-11: 47.2% of all token volume in the twelve largest
    transcripts was a repeat, an overall 1.89x when every entry was summed).
    And a streamed response is written as one entry PER CONTENT BLOCK, whose
    output_tokens grow block by block while input and cache usage repeat
    unchanged. Measured 2026-09-23 over every transcript on disk: 28,509 of
    39,335 repeated ids in subagent transcripts differ, all of them only in
    output_tokens; 0 of 21,280 in session transcripts do.

    Counting the first entry of an id, as this module did from 2026-08-11 to
    2026-09-23, handles replays and undercounts every streamed message - the
    same subagent transcripts summed to 5,196,123 output tokens that way
    against 31,540,502 at the peak. The last entry is not safe either: 5 ids
    were replayed interleaved (2, 270, 2, 270, 2), so file order does not
    end on the peak. The per-field maximum recovers the LARGEST usage the
    transcript recorded for a message, whatever order its entries came in:
    summing the increments this returns over any subset of an id's entries
    gives the maximum over that subset, which is what makes as-of
    reconstruction over timestamp-sorted entries agree with a hook that read
    the file in order.

    What it cannot recover is usage the transcript never recorded. Since
    Claude Code 2.1.278, many subagent messages never get a final entry with
    a stop_reason, so their full usage is never written and the largest
    recorded output is a streaming placeholder of 1-4 tokens (the 2026-09-23
    review counted 12,214 such ids, an estimated ~7.8M output tokens missing
    since 2026-09-21). No counting rule can restore those; these totals are
    a floor for them, not the true figure.

    Entries with no id cannot be matched to anything, so they always count
    in full. `peaks` is the caller's per-transcript state, updated in place.
    """
    if message_id is None:
        return values
    peak = peaks.get(message_id)
    if peak is None:
        peaks[message_id] = values
        return values
    # Indexed rather than zip(..., strict=True): the hooks import this module
    # and may run under macOS's bundled Python 3.9, which has no `strict`.
    fields = range(len(values))
    peaks[message_id] = tuple(max(values[field_index], peak[field_index]) for field_index in fields)
    return tuple(max(0, values[field_index] - peak[field_index]) for field_index in fields)


def count_incomplete(message_order: list[Any], finalised: set[Any]) -> int:
    """How many messages are over but never got their final usage entry.

    Claude Code writes a message's final entry with a non-null stop_reason,
    and that entry carries the message's full usage. Measured 2026-09-25 over
    every transcript on disk: all 74,698 ids that have one carry their
    largest output there; replays repeat it, so an id can have several, all
    with the same reason. An id with NONE never had its usage written - since
    Claude Code 2.1.278, ~17,000 subagent ids end on a streaming placeholder
    (output 1-4 tokens for about a third of them) - so the totals counted for
    it are a floor, and this is the count that says so.

    The message first seen LAST is excluded: a snapshot taken mid-response
    sees the newest message before its final entry exists, and counting it
    would flag every in-progress snapshot. A message is over once another
    one has started after it. The price is that a transcript whose very last
    message was never finalised goes unflagged (25 of 1,315 subagent
    transcripts on 2026-09-25) - an undercount of the flag, never a false one.
    `message_order` lists ids in first-seen order.
    """
    return sum(1 for message_id in message_order[:-1] if message_id not in finalised)


def parse_transcript(path: str) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_creation_tokens = 0
    turn_count = 0
    tool_call_count = 0
    peaks: dict[Any, tuple[int, ...]] = {}
    seen_tool_ids: set[Any] = set()
    message_order: list[Any] = []
    finalised: set[Any] = set()
    first_timestamp = None
    last_timestamp = None
    git_branch = None
    claude_code_version = None
    claude_code_version_first = None
    tokens_by_model: dict[str, dict[str, int]] = {}

    with Path(path).open() as transcript_file:
        for line in transcript_file:
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

            # Backlog 2026-09-28 #5: every entry Claude Code writes (not
            # just assistant ones) carries the CLI's own "version", e.g.
            # "2.1.284" - present on the same top-level shape as gitBranch
            # above, so it is captured the same way. A long session can
            # span an upgrade, so the LAST entry with a version wins as
            # `claude_code_version` (what the row's tokens were actually
            # produced under, for the tail end of the session) and the
            # FIRST is kept separately only long enough to compare against
            # it after the loop.
            version = entry.get("version")
            if version:
                if claude_code_version is None:
                    claude_code_version_first = version
                claude_code_version = version

            if entry.get("type") == "assistant":
                message = entry.get("message") or {}
                # One assistant message can appear many times in a single
                # transcript - replayed by a resume or compaction, and
                # split into one entry per content block when streamed.
                # It counts once, at its per-field peak: see
                # usage_increment for the measurements behind the rule.
                message_id = message.get("id")
                first_occurrence = message_id is None or message_id not in peaks
                increment = usage_increment(peaks, message_id, usage_values(message))
                input_tokens += increment[0]
                output_tokens += increment[1]
                cache_read_tokens += increment[2]
                cache_creation_tokens += increment[3]

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
                # display change never needs a re-backfill. A bucket gains
                # exactly the increment the totals gained, so the two stay
                # equal under the peak rule as they did under the old one;
                # a later entry of a message only touches a bucket when it
                # raises the message's peak.
                model = model_key(message)
                if any(increment) or (first_occurrence and model != SYNTHETIC_MODEL):
                    bucket = tokens_by_model.get(model)
                    if bucket is None:
                        bucket = tokens_by_model[model] = dict.fromkeys(TOKEN_FIELDS, 0)
                    for position, field in enumerate(TOKEN_FIELDS):
                        bucket[field] += increment[position]

                if first_occurrence:
                    turn_count += 1
                if message_id is not None:
                    if first_occurrence:
                        message_order.append(message_id)
                    if message.get("stop_reason") is not None:
                        finalised.add(message_id)
                # Tool calls are counted per DISTINCT tool_use block id,
                # across every entry of the message: a streamed message
                # writes each content block as its own entry, so its
                # tool_use blocks sit in LATER entries of the id, which a
                # first-entry count never saw (2026-09-23, over every
                # transcript on disk: 7,856 of 31,154 distinct tool_use
                # blocks counted in sessions, 12,005 of 57,374 in
                # subagents). The block id makes a replayed entry count
                # once. A block without an id cannot be matched, so it
                # counts on its message's first entry only, as before.
                content = message.get("content") or []
                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_use":
                            continue
                        block_id = block.get("id")
                        if block_id is None:
                            tool_call_count += first_occurrence
                        elif block_id not in seen_tool_ids:
                            seen_tool_ids.add(block_id)
                            tool_call_count += 1

    duration_seconds = None
    if first_timestamp and last_timestamp:
        try:
            start = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
            end = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
            duration_seconds = (end - start).total_seconds()
        except ValueError:
            duration_seconds = None

    # claude_code_version_first is dropped back to None here when it never
    # differed from the (last-wins) claude_code_version above - keeping the
    # record small is the point (see the loop's own comment): a session
    # that never spanned an upgrade has nothing extra to say.
    if claude_code_version_first == claude_code_version:
        claude_code_version_first = None

    return {
        "timestamp_start": first_timestamp,
        "timestamp_end": last_timestamp,
        "duration_seconds": duration_seconds,
        "git_branch": git_branch,
        "claude_code_version": claude_code_version,
        "claude_code_version_first": claude_code_version_first,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "tokens_by_model": tokens_by_model,
        "turn_count": turn_count,
        "tool_call_count": tool_call_count,
        "incomplete_usage_messages": count_incomplete(message_order, finalised),
    }


def append_record(metrics_file: Path, record: dict[str, Any]) -> None:
    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    is_new = not metrics_file.exists()
    with metrics_file.open("a") as metrics_output_file:
        metrics_output_file.write(json.dumps(record) + "\n")
    if is_new:
        metrics_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
