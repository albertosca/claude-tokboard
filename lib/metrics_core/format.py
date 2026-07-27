"""Number/date formatting helpers shared by both dashboard entry points."""
from datetime import datetime


def format_tokens(n):
    if n is None:
        return "-"
    n = int(n)
    if abs(n) >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.1f}T"
    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_duration(seconds):
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.0f}min"
    hours = int(minutes // 60)
    remaining_minutes = int(minutes % 60)
    return f"{hours}h{remaining_minutes:02d}min"


def format_date(timestamp_start):
    if not timestamp_start:
        return "-"
    try:
        dt = datetime.fromisoformat(timestamp_start.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError, TypeError):
        return "-"
    return dt.strftime("%d/%m %H:%M")
