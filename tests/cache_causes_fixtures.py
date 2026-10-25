"""A computed causes dict for the view and renderer tests: placeholder numbers only."""

from typing import Any

from metrics_core import cache_causes
from weekly_meter_fixtures import ANCHOR, WEEK, iso

START = ANCHOR - 100_000


def _row(conversation: str, offset: float, created: int, read: int, **extra: Any) -> dict[str, Any]:
    row = {
        "message_id": f"{conversation}-{offset}",
        "conversation": conversation,
        "timestamp": iso(START + offset),
        "sidechain": 0,
        "input": 1,
        "cache_read": read,
        "cache_5m": 0,
        "cache_1h": created,
    }
    row.update(extra)
    return row


def causes_for(tier: str = "cache_1h", jump: bool = False) -> dict[str, Any]:
    """One open window: a 50k cold start and a 130k rewrite after an hour's pause."""
    calls = [_row("main", 0, 50_000, 0), _row("main", 7_200, 130_000, 10)]
    for call in calls:
        call[tier] = call.pop("cache_1h")
    if jump:
        for index in range(5):
            calls.append(_row(f"old-{index}", 10, 60_000, 0, claude_code_version="2.0.1"))
            calls.append(_row(f"new-{index}", 20, 80_000, 0, claude_code_version="2.0.2"))
    window = {"start": START - 10, "end": START + WEEK, "covered": True}
    meter = {"windows": [window], "current": window}
    return cache_causes.compute_cache_causes(calls, meter)
