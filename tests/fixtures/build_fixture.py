"""Builds a small synthetic metrics dataset inside a throwaway HOME.

Timestamps are offsets from a supplied `now` rather than fixed dates, so
the records keep landing in the same period windows however long from now
the suite is run. They sit hours away from every window edge on purpose:
boundary arithmetic is already covered deterministically by
tests/test_period_filters.py with an injected clock, and reproducing it
here against wall-clock time would make midnight a flaky failure.
"""
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../lib"))
import metrics_core as mr  # noqa: E402

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def _at(now, days_ago, hour):
    return (now - timedelta(days=days_ago)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).isoformat()


def _record(session_id, project, start, end, tokens):
    record = {
        "session_id": session_id,
        "project_path": project,
        "timestamp_start": start,
        "timestamp_end": end,
        "duration_seconds": 3600,
        "turn_count": 4,
        "tool_call_count": 7,
    }
    record.update(dict(zip(TOKEN_FIELDS, tokens)))
    return record


def build_fixture(home_dir, now):
    records = [
        # The span-overlap case this entire filter design exists for: opened
        # a month ago, still working yesterday. A start-date filter would
        # wrongly drop it from "ontem".
        _record("long-1", "/projects/alpha", _at(now, 30, 9), _at(now, 1, 20), (10, 20, 900, 70)),
        # Yesterday only.
        _record("yest-1", "/projects/beta", _at(now, 1, 10), _at(now, 1, 11), (1, 2, 90, 7)),
        # Inside the last 15 days but not yesterday.
        _record("week-1", "/projects/beta", _at(now, 9, 10), _at(now, 9, 12), (3, 4, 200, 20)),
        # Older than every window except "tudo".
        _record("old-1", "/projects/gamma", _at(now, 50, 10), _at(now, 49, 10), (5, 5, 500, 40)),
    ]

    metrics_dir = os.path.join(home_dir, ".claude", "metrics")
    os.makedirs(metrics_dir, exist_ok=True)
    with open(os.path.join(metrics_dir, "sessions.jsonl"), "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    # An empty subagents file, not a missing one: load_records tolerates
    # absence, but an empty file is what a real install actually looks like
    # before any subagent has run.
    open(os.path.join(metrics_dir, "subagents.jsonl"), "w").close()

    # Expected values come from metrics_core - the tested data layer - with
    # no help from the rendering code. The page then has to reproduce them
    # by being DRIVEN, which is the whole point.
    expected = {}
    for period in mr.PERIODS:
        window = mr.resolve_period(period.id, now)
        scoped = mr.filter_sessions_by_window(records, window)
        expected[period.id] = {
            "session_ids": {r["session_id"] for r in scoped},
            "token_total": sum(mr.record_token_total(r) for r in scoped),
        }
    return {"expected": expected, "records": records}
