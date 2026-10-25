"""Synthetic windows for the weekly-meter tests: placeholder numbers only."""

from datetime import datetime, timezone
from typing import Any

from metrics_core import weekly_windows

WEEK = 7 * 24 * 3600
ANCHOR = 1_800_000_000


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def call(epoch: float, five_minute: int, one_hour: int = 0) -> dict[str, Any]:
    return {
        "message_id": f"message-{epoch}",
        "timestamp": iso(epoch),
        "cache_5m": five_minute,
        "cache_1h": one_hour,
    }


def hit(epoch: float, resets_at: int) -> dict[str, Any]:
    return {
        "timestamp": iso(epoch),
        "rate_limit_type": "seven_day",
        "resets_at": resets_at,
        "status": "rejected",
    }


def meter_for(usages: list[int], current: int = 0) -> dict[str, Any]:
    """A meter with one rejected, fully covered week per entry of `usages`."""
    calls = [call(ANCHOR - (len(usages) + 2) * WEEK, 1)]
    hits = []
    for position, usage in enumerate(usages):
        end = ANCHOR - (len(usages) - 1 - position) * WEEK
        calls.append(call(end - WEEK + 3600, usage // 2, usage - usage // 2))
        hits.append(hit(end - 3600, end))
    calls.append(call(ANCHOR + 3600, current // 3, current - current // 3))
    now = datetime.fromtimestamp(ANCHOR + 7200, tz=timezone.utc)
    return weekly_windows.compute_weekly_meter(calls, hits, now)
