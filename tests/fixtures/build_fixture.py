"""Builds a small synthetic metrics dataset inside a throwaway HOME.

Timestamps are offsets from a supplied `now` rather than fixed dates, so
the records keep landing in the same period windows however long from now
the suite is run. Boundary arithmetic itself is covered deterministically
by tests/test_period_filters.py with an injected clock; reproducing it
here against wall-clock time would make midnight a flaky failure.

Two properties this dataset has to keep, both learned the hard way:

1. EVERY period must be non-empty on EVERY weekday. "desde-segunda" is
   calendar-anchored (periods.py: today - now.weekday() days .. now), so
   on a MONDAY its window is today alone. An earlier version of this file
   claimed its records "sit hours away from every window edge on
   purpose" - false, and the false claim is how this survived: that edge
   is a CALENDAR edge, and it jumps a full day once a week. Every record
   here ended at least a day in the past, so the suite would have gone
   red every Monday. The `today-*` records below exist for that, and
   there are TWO of them because the trend chart returns nothing at all
   below two distinct time buckets, which would break the harness's
   non-vacuity guards just as thoroughly as an empty period.
2. All five periods must hold DIFFERENT sets. A fixture whose records
   cannot tell "ontem" from "desde-segunda", or "15-dias" from "mes",
   turns the harness's five-period loop into three real assertions and
   fifteen decorative ones - widening a window by days then goes
   undetected. Each record below is the sole discriminator of one
   boundary; deleting any one of them collapses a pair of periods:

       today-a / today-b  today only          ontem | desde-segunda
       long-1, yest-1     yesterday           desde-segunda (Mon) | ontem
       days4-1            4 days back         ontem | desde-segunda
       week-1             9 days back         desde-segunda | 15-dias
       days21-1           21 days back        15-dias | mes
       old-1              ~50 days back       mes | tudo
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))
import metrics_core as mr

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")


def _at(now: datetime, days_ago: int, hour: int) -> str:
    return (
        (now - timedelta(days=days_ago))
        .replace(hour=hour, minute=0, second=0, microsecond=0)
        .isoformat()
    )


def _today_at(now: datetime, fraction: float) -> str:
    """A moment `fraction` of the way from this midnight to `now`.

    Anything built from this is inside today by construction, at any hour
    of any day - which is what "desde-segunda" needs on a Monday. A naive
    `now - 1h` would fall into YESTERDAY whenever the suite runs before
    01:00, which is the same class of bug as the one this fixes.

    The one case this cannot cover: two moments a fraction apart are only
    distinguishable to the trend chart's finest bucket (a minute) once
    roughly a minute and a half has passed since midnight. A run started
    inside the first two minutes of a Monday would see the chart's
    non-vacuity guard fail. Stated rather than papered over - there is no
    sub-minute bucket to move to.
    """
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    moment = start_of_today + (now - start_of_today) * fraction
    return moment.replace(microsecond=0).isoformat()


def _record(
    session_id: str,
    project: str,
    start: str,
    end: str,
    tokens: tuple[int, int, int, int],
    by_model: dict[str, tuple[int, int, int, int]] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "session_id": session_id,
        "project_path": project,
        "timestamp_start": start,
        "timestamp_end": end,
        "duration_seconds": 3600,
        "turn_count": 4,
        "tool_call_count": 7,
    }
    record.update(dict(zip(TOKEN_FIELDS, tokens, strict=True)))
    if by_model is not None:
        # Deliberately PARTIAL, and only on some records: that is the shape
        # the real history has after the 2026-09 backfill, and it is what
        # makes the chart's "sem modelo" band reachable from the fixture.
        record["tokens_by_model"] = {
            model: dict(zip(TOKEN_FIELDS, amounts, strict=True))
            for model, amounts in by_model.items()
        }
    return record


def build_fixture(home_dir: str, now: datetime) -> dict[str, Any]:
    records = [
        # The span-overlap case this entire filter design exists for: opened
        # a month ago, still working yesterday. A start-date filter would
        # wrongly drop it from "ontem".
        _record("long-1", "/projects/alpha", _at(now, 30, 9), _at(now, 1, 20), (10, 20, 900, 70)),
        # Yesterday only.
        _record("yest-1", "/projects/beta", _at(now, 1, 10), _at(now, 1, 11), (1, 2, 90, 7)),
        # Today, start to finish. These two are what keeps "desde-segunda"
        # non-empty on a Monday, when its window is today and nothing else.
        # Two records, not one, and ending far enough apart to land in
        # different trend buckets: the chart renders nothing below two
        # buckets, so a single record would leave Monday's chart empty and
        # the harness's "names at least one project" guard unsatisfiable.
        # Distinct projects from every other record, so a chart pointed at
        # the wrong window is caught by the project NAMES alone.
        _record(
            "today-a",
            "/projects/delta",
            _today_at(now, 0.05),
            _today_at(now, 0.25),
            (2, 3, 120, 9),
            by_model={
                "claude-opus-5": (1, 2, 60, 4),
                "claude-sonnet-5": (1, 1, 30, 2),
            },
        ),
        _record(
            "today-b",
            "/projects/epsilon",
            _today_at(now, 0.5),
            _today_at(now, 0.9),
            (4, 6, 150, 11),
            by_model={"claude-opus-5": (2, 3, 75, 5)},
        ),
        # Four days back: inside "desde-segunda" from Friday on, and the
        # nearest thing outside "ontem". Widening "ontem" by even a couple
        # of days pulls this in and changes the visible session ids, which
        # is precisely the injection the old fixture could not feel.
        _record("days4-1", "/projects/zeta", _at(now, 4, 10), _at(now, 4, 12), (2, 2, 140, 10)),
        # Inside the last 15 days but not yesterday.
        _record("week-1", "/projects/beta", _at(now, 9, 10), _at(now, 9, 12), (3, 4, 200, 20)),
        # Twenty-one days back: six days clear of the 14-day edge and eight
        # clear of the 29-day one, so it separates "15-dias" from "mes" -
        # the pair that held identical sets before, always.
        _record("days21-1", "/projects/eta", _at(now, 21, 10), _at(now, 21, 12), (6, 8, 300, 25)),
        # Older than every window except "tudo".
        _record("old-1", "/projects/gamma", _at(now, 50, 10), _at(now, 49, 10), (5, 5, 500, 40)),
    ]

    metrics_dir = Path(home_dir) / ".claude" / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    with (metrics_dir / "sessions.jsonl").open("w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    # An empty subagents file, not a missing one: load_records tolerates
    # absence, but an empty file is what a real install actually looks like
    # before any subagent has run.
    (metrics_dir / "subagents.jsonl").touch()

    # Expected values come from metrics_core - the tested data layer - with
    # no help from the rendering code. The page then has to reproduce them
    # by being DRIVEN, which is the whole point.
    expected: dict[str, dict[str, Any]] = {}
    for period in mr.PERIODS:
        window = mr.resolve_period(period.id, now)
        scoped = mr.filter_sessions_by_window(records, window)
        expected[period.id] = {
            "session_ids": {r["session_id"] for r in scoped},
            "token_total": sum(mr.record_token_total(r) for r in scoped),
        }
    return {"expected": expected, "records": records}
