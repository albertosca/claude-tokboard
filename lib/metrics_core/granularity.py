"""Adaptive time-bucket granularity (minute/hour/day/week/month/year)
for the trend charts in both dashboard entry points."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Trend chart granularity, chosen by the data's own span (2026-07-21) -
# a single day of activity read as a wall of near-empty day-bars; a
# multi-year history would read as an unreadable wall of day-bars the
# other way. key_fmt buckets timestamps into that granularity; parse_fmt
# reads a bucket key back into a datetime (must round-trip key_fmt
# exactly). The x-axis tick text is no longer stored here - it comes from
# i18n.tick_label, which picks the format by language rather than a
# single locale-blind strftime string.
GRANULARITY_CONFIG = {
    "minute": {"key_fmt": "%Y-%m-%dT%H:%M", "parse_fmt": "%Y-%m-%dT%H:%M"},
    "hour": {"key_fmt": "%Y-%m-%dT%H", "parse_fmt": "%Y-%m-%dT%H"},
    "day": {"key_fmt": "%Y-%m-%d", "parse_fmt": "%Y-%m-%d"},
    # ISO-calendar key ("%G-W%V") sorts correctly as a plain string, but
    # can't round-trip through strptime without a weekday directive - the
    # literal "-1" is constant text (not a format code), so every
    # timestamp in the same ISO week produces the identical key, while
    # parse_fmt's "%u" reads that literal "1" back as Monday for tick
    # formatting.
    "week": {"key_fmt": "%G-W%V-1", "parse_fmt": "%G-W%V-%u"},
    "month": {"key_fmt": "%Y-%m", "parse_fmt": "%Y-%m"},
    "year": {"key_fmt": "%Y", "parse_fmt": "%Y"},
}


def choose_granularity(timestamps: list[datetime]) -> str:
    if len(timestamps) < 2:
        return "day"
    span = max(timestamps) - min(timestamps)
    if span <= timedelta(hours=2):
        return "minute"
    if span <= timedelta(hours=48):
        return "hour"
    if span <= timedelta(days=45):
        return "day"
    if span <= timedelta(days=365):
        return "week"
    if span <= timedelta(days=365 * 3):
        return "month"
    return "year"


def _bucket_end(granularity: str, start: datetime) -> datetime:
    """The moment a bucket of this granularity, starting at `start`, ends."""
    if granularity == "minute":
        return start + timedelta(minutes=1)
    if granularity == "hour":
        return start + timedelta(hours=1)
    if granularity == "day":
        return start + timedelta(days=1)
    if granularity == "week":
        return start + timedelta(days=7)
    if granularity == "month":
        return (
            start.replace(year=start.year + 1, month=1)
            if start.month == 12
            else start.replace(month=start.month + 1)
        )
    return start.replace(year=start.year + 1)  # year


def partial_bucket_fraction(granularity: str, bucket_key: str, now: datetime) -> float | None:
    """How far into an in-progress bucket `now` falls (0..1), or None if
    the bucket has already ended.

    Backlog item, 2026-09-02: a trend chart's last bar is always a
    fraction of its bucket compared to the complete ones beside it, and
    reads as a decline even when it isn't one - the newest bucket is
    simply still running. Only ever meaningful for the LAST bucket a
    caller has: every earlier one has necessarily already ended, by
    construction of how aggregate_by_day only ever buckets records up to
    the present moment.

    `now` may be timezone-aware (this repo's convention is
    `datetime.now().astimezone()`) - stripped to naive local wall-clock
    time before comparing, since every key_fmt/parse_fmt pair in
    GRANULARITY_CONFIG round-trips naive local time (bucket keys are
    produced by strftime-ing an aware moment with formats that carry no
    UTC offset).
    """
    if now.tzinfo is not None:
        now = now.replace(tzinfo=None)
    start = datetime.strptime(bucket_key, GRANULARITY_CONFIG[granularity]["parse_fmt"])
    end = _bucket_end(granularity, start)
    if now >= end:
        return None
    if now <= start:
        return 0.0
    return (now - start).total_seconds() / (end - start).total_seconds()


def aggregate_by_day(
    records: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    dated: list[tuple[datetime, dict[str, Any]]] = []
    timestamps: list[datetime] = []
    for r in records:
        timestamp_start = r.get("timestamp_start")
        if not timestamp_start:
            continue
        try:
            dt = datetime.fromisoformat(timestamp_start.replace("Z", "+00:00")).astimezone()
        except (ValueError, AttributeError, TypeError):
            continue
        dated.append((dt, r))
        timestamps.append(dt)

    granularity = choose_granularity(timestamps)
    key_fmt = GRANULARITY_CONFIG[granularity]["key_fmt"]
    buckets: dict[str, list[dict[str, Any]]] = {}
    bucket_order: list[str] = []
    for dt, r in dated:
        key = dt.strftime(key_fmt)
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(r)
    bucket_order.sort()
    return granularity, [{"day": key, "records": buckets[key]} for key in bucket_order]
