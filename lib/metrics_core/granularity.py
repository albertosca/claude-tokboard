"""Adaptive time-bucket granularity (minute/hour/day/week/month/year)
for the trend charts in both dashboard entry points."""

from datetime import datetime, timedelta

# Trend chart granularity, chosen by the data's own span (2026-07-21) -
# a single day of activity read as a wall of near-empty day-bars; a
# multi-year history would read as an unreadable wall of day-bars the
# other way. key_fmt buckets timestamps into that granularity; parse_fmt
# reads a bucket key back into a datetime (must round-trip key_fmt
# exactly); tick_fmt is what's shown on the x-axis.
GRANULARITY_CONFIG = {
    "minute": {"key_fmt": "%Y-%m-%dT%H:%M", "parse_fmt": "%Y-%m-%dT%H:%M", "tick_fmt": "%H:%M"},
    "hour": {"key_fmt": "%Y-%m-%dT%H", "parse_fmt": "%Y-%m-%dT%H", "tick_fmt": "%d/%m %Hh"},
    "day": {"key_fmt": "%Y-%m-%d", "parse_fmt": "%Y-%m-%d", "tick_fmt": "%d/%m"},
    # ISO-calendar key ("%G-W%V") sorts correctly as a plain string, but
    # can't round-trip through strptime without a weekday directive - the
    # literal "-1" is constant text (not a format code), so every
    # timestamp in the same ISO week produces the identical key, while
    # parse_fmt's "%u" reads that literal "1" back as Monday for tick
    # formatting.
    "week": {"key_fmt": "%G-W%V-1", "parse_fmt": "%G-W%V-%u", "tick_fmt": "S%V/%y"},
    "month": {"key_fmt": "%Y-%m", "parse_fmt": "%Y-%m", "tick_fmt": "%m/%Y"},
    "year": {"key_fmt": "%Y", "parse_fmt": "%Y", "tick_fmt": "%Y"},
}


def choose_granularity(timestamps):
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


def aggregate_by_day(records):
    dated = []
    timestamps = []
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
    buckets = {}
    bucket_order = []
    for dt, r in dated:
        key = dt.strftime(key_fmt)
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(r)
    bucket_order.sort()
    return granularity, [{"day": key, "records": buckets[key]} for key in bucket_order]
