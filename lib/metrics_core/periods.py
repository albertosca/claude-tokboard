"""Named period windows for scoping every dashboard surface to the same
stretch of time. Pure date arithmetic, no record handling - the filters
that apply these windows live in records.py."""

import collections
from datetime import datetime, timedelta

Period = collections.namedtuple("Period", ("id", "label"))

# Order is display order: narrowest first, "everything" last.
PERIODS = (
    Period("ontem", "ontem"),
    Period("desde-segunda", "desde segunda"),
    Period("15-dias", "15 dias"),
    Period("mes", "último mês"),
    Period("tudo", "tudo"),
)

DEFAULT_PERIOD_ID = "tudo"

_VALID_IDS = {p.id for p in PERIODS}


def _start_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def resolve_period(period_id: str, now: datetime) -> tuple[datetime, datetime] | None:
    # Returns (start, end) with end EXCLUSIVE, or None for "tudo" - None
    # rather than a very wide window so callers can skip filtering
    # entirely instead of comparing every record against a sentinel date.
    #
    # `now` is a parameter rather than read from the clock so this is
    # testable and so one dashboard render uses a single consistent
    # instant across every surface. A render that called datetime.now()
    # per surface could straddle midnight and put two panels in different
    # days - the exact disagreement this phase exists to prevent.
    if period_id not in _VALID_IDS:
        raise ValueError(f"unknown period: {period_id!r}")
    if period_id == "tudo":
        return None
    today = _start_of_day(now)
    if period_id == "ontem":
        # A closed day: yesterday only, today excluded.
        return today - timedelta(days=1), today
    if period_id == "desde-segunda":
        # Calendar-anchored, not "7 days back" - on a Monday this is
        # that morning, so the number of days covered varies by weekday.
        return today - timedelta(days=now.weekday()), now
    if period_id == "15-dias":
        # Today counts as one of the fifteen, so we reach back fourteen.
        return today - timedelta(days=14), now
    # "mes": 30 calendar days including today, NOT the calendar month -
    # a month boundary would make the window collapse to a day or two
    # every time the month turns over.
    return today - timedelta(days=29), now
