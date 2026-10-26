"""Weekly-limit windows and the observed range of the cache-creation meter.

The hypothesis (backlog, 2026-10-07 study): the weekly limit tracks cumulative
cache creation, the 5-minute tier plus the 1-hour tier. Nothing here states a
cap. The range is the minimum and maximum of the user's OWN accumulated value
at the first rejection of each closed, fully covered weekly window, and it is
labelled a hypothesis with its point count.

Inputs are the readers' output (`read_calls`, `read_limit_hits`), so a replayed
message is already counted once. Only aggregates leave this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .format import model_family

WEEK_SECONDS = 7 * 24 * 3600
FIVE_HOUR_SECONDS = 5 * 3600
# Fewer rejected windows than this and the panel says it cannot estimate a cap.
MINIMUM_RANGE_POINTS = 2
# A reset time past this (2100-01-01) is not epoch seconds: a millisecond
# value would send the window grid walking one week per iteration for good.
LATEST_USABLE_RESET = 4_102_444_800
# Two rejections whose reset times differ by less than this are one week.
RESET_JITTER_SECONDS = 3600


def _epoch(timestamp: Any) -> float | None:
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _usable_reset(value: Any) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 < value <= LATEST_USABLE_RESET
    ):
        return None
    return value


def _creation(call: dict[str, Any], field: str) -> int:
    value = call.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _placed_calls(calls: list[dict[str, Any]]) -> list[tuple[float, int, int, str]]:
    placed = []
    for call in calls:
        moment = _epoch(call.get("timestamp"))
        if moment is not None:
            model = call.get("model")
            family = model_family(model) if isinstance(model, str) else "other"
            placed.append(
                (moment, _creation(call, "cache_5m"), _creation(call, "cache_1h"), family)
            )
    return placed


def _weekly_hit_times(hits: list[dict[str, Any]]) -> dict[int, float]:
    """window end (epoch) -> time of the first rejection recorded for it."""
    first: dict[int, float] = {}
    for hit in hits:
        if hit.get("rate_limit_type") != "seven_day":
            continue
        end = _usable_reset(hit.get("resets_at"))
        moment = _epoch(hit.get("timestamp"))
        if end is None or moment is None:
            continue
        first[end] = min(moment, first.get(end, moment))
    merged: dict[int, float] = {}
    for end in sorted(first):
        anchor = next((kept for kept in merged if end - kept < RESET_JITTER_SECONDS), None)
        key = end if anchor is None else anchor
        merged[key] = min(first[end], merged.get(key, first[end]))
    return merged


def _window_ends(
    first_rejections: dict[int, float], earliest_call: float, now: float
) -> list[tuple[int, bool]]:
    """(end, inferred) for every window from the first stored call to now.

    Real ends come from rejections. The rest is the 7-day grid through the
    latest real end; a grid window that overlaps a real one is dropped, so a
    moved reset shows up as the real windows only.
    """
    grid_end = max(first_rejections)
    while grid_end - WEEK_SECONDS > now:
        grid_end -= WEEK_SECONDS
    while grid_end <= now:
        grid_end += WEEK_SECONDS
    ends = dict.fromkeys(first_rejections, False)
    candidate = grid_end
    while candidate > earliest_call:
        overlaps_real = any(
            candidate - WEEK_SECONDS < real and real - WEEK_SECONDS < candidate
            for real in first_rejections
        )
        if candidate not in ends and not overlaps_real:
            ends[candidate] = True
        candidate -= WEEK_SECONDS
    return sorted(ends.items())


def _five_hour(
    placed: list[tuple[float, int, int, str]], hits: list[dict[str, Any]], now: float
) -> dict[str, int]:
    accumulated = sum(
        five_minute + one_hour
        for moment, five_minute, one_hour, _ in placed
        if now - FIVE_HOUR_SECONDS <= moment <= now
    )
    rejected = set()
    for hit in hits:
        moment = _epoch(hit.get("timestamp"))
        end = _usable_reset(hit.get("resets_at"))
        if (
            hit.get("rate_limit_type") == "five_hour"
            and end is not None
            and moment is not None
            and now - WEEK_SECONDS <= moment <= now
        ):
            rejected.add(end)
    return {"accumulated": accumulated, "rejected_windows": len(rejected)}


def _by_family(calls: list[tuple[float, int, int, str]]) -> dict[str, int]:
    """Cache creation per model family inside one window, largest first.

    Kept per window so the per-model weights of the limit can be fitted again
    as windows accumulate (backlog 2026-10-07 #16); a family with no creation
    is left out.
    """
    totals: dict[str, int] = {}
    for _, five_minute, one_hour, family in calls:
        totals[family] = totals.get(family, 0) + five_minute + one_hour
    return dict(
        sorted(((family, total) for family, total in totals.items() if total), key=_largest_first)
    )


def _largest_first(item: tuple[str, int]) -> tuple[int, str]:
    return (-item[1], item[0])


def _build_window(
    placed: list[tuple[float, int, int, str]],
    end: int,
    inferred: bool,
    earliest_call: float,
    first_rejections: dict[int, float],
) -> dict[str, Any]:
    start = end - WEEK_SECONDS
    inside = [call for call in placed if start <= call[0] < end]
    window: dict[str, Any] = {
        "start": start,
        "end": end,
        "inferred": inferred,
        "covered": start >= earliest_call,
        "cache_5m": sum(call[1] for call in inside),
        "cache_1h": sum(call[2] for call in inside),
        "rejected": end in first_rejections,
        "first_rejection_accumulated": None,
        "families": _by_family(inside),
    }
    window["accumulated"] = window["cache_5m"] + window["cache_1h"]
    if end in first_rejections:
        window["first_rejection_accumulated"] = sum(
            call[1] + call[2] for call in inside if call[0] <= first_rejections[end]
        )
    return window


def compute_weekly_meter(
    calls: list[dict[str, Any]], hits: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    now_epoch = now.timestamp()
    placed = _placed_calls(calls)
    five_hour = _five_hour(placed, hits, now_epoch)
    empty: dict[str, Any] = {
        "windows": [],
        "current": None,
        "range": None,
        "state": "no_data",
        "five_hour": five_hour,
    }
    if not placed:
        return empty
    first_rejections = _weekly_hit_times(hits)
    if not first_rejections:
        return {**empty, "state": "no_anchor"}

    earliest_call = min(call[0] for call in placed)
    windows = [
        _build_window(placed, end, inferred, earliest_call, first_rejections)
        for end, inferred in _window_ends(first_rejections, earliest_call, now_epoch)
    ]
    points = [
        window["first_rejection_accumulated"]
        for window in windows
        if window["end"] <= now_epoch and window["covered"] and window["rejected"]
    ]
    observed = None
    if len(points) >= MINIMUM_RANGE_POINTS:
        observed = {"minimum": min(points), "maximum": max(points), "points": len(points)}
    state = "insufficient" if observed is None else "hypothesis"
    if observed is not None and any(
        window["end"] <= now_epoch
        and window["covered"]
        and not window["rejected"]
        and window["accumulated"] > observed["maximum"]
        for window in windows
    ):
        state = "contradicted"
    current = next(
        (window for window in windows if window["start"] <= now_epoch < window["end"]), None
    )
    return {
        "windows": list(reversed(windows)),
        "current": current,
        "range": observed,
        "state": state,
        "five_hour": five_hour,
    }
