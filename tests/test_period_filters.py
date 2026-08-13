from datetime import datetime

import metrics_core as mr

NOW = datetime(2026, 8, 13, 15, 30, tzinfo=None).astimezone()
YESTERDAY = mr.resolve_period("ontem", NOW)


def stamp(year, month, day, hour=0):
    # Local-time ISO strings with a real offset, NOT "...Z". A UTC literal
    # would land on a different calendar day depending on the machine's
    # timezone, so these tests would pass in São Paulo and fail in Tokyo -
    # a suite whose colour depends on where it runs is not a suite.
    return datetime(year, month, day, hour, tzinfo=None).astimezone().isoformat()


def session(start, end):
    return {"timestamp_start": start, "timestamp_end": end, "output_tokens": 1}


def delta(when):
    # expand_records_to_daily_deltas stores the snapshot's END time in the
    # field named timestamp_start; these fixtures mirror that on purpose.
    return {"timestamp_start": when, "output_tokens": 1}


def test_a_long_session_that_worked_yesterday_is_included():
    # The whole point: opened three weeks ago, still running. A filter on
    # its start date alone would drop it and leave the session list empty
    # while the chart showed yesterday's work.
    records = [session(stamp(2026, 7, 22, 10), stamp(2026, 8, 13, 14))]
    assert mr.filter_sessions_by_window(records, YESTERDAY) == records


def test_a_session_that_ended_before_the_window_is_excluded():
    records = [session(stamp(2026, 8, 1, 10), stamp(2026, 8, 5, 11))]
    assert mr.filter_sessions_by_window(records, YESTERDAY) == []


def test_a_session_that_started_after_the_window_is_excluded():
    records = [session(stamp(2026, 8, 13, 9), stamp(2026, 8, 13, 10))]
    assert mr.filter_sessions_by_window(records, YESTERDAY) == []


def test_a_session_missing_its_end_falls_back_to_its_start():
    records = [session(stamp(2026, 8, 12, 10), None)]
    assert mr.filter_sessions_by_window(records, YESTERDAY) == records


def test_a_delta_is_judged_by_its_own_date_alone():
    inside = delta(stamp(2026, 8, 12, 23))
    outside = delta(stamp(2026, 8, 13, 9))
    assert mr.filter_deltas_by_window([inside, outside], YESTERDAY) == [inside]


def test_no_window_returns_everything():
    records = [session(stamp(2026, 1, 1), stamp(2026, 1, 2))]
    assert mr.filter_sessions_by_window(records, None) == records
    assert mr.filter_deltas_by_window(records, None) == records


def test_unparseable_dates_are_dropped_not_crashed_on():
    assert mr.filter_deltas_by_window([delta("not a date")], YESTERDAY) == []
    assert mr.filter_deltas_by_window([delta(None)], YESTERDAY) == []


def test_filtering_does_not_mutate_the_input():
    records = [session("2026-08-12T10:00:00Z", "2026-08-12T11:00:00Z")]
    snapshot = [dict(r) for r in records]
    mr.filter_sessions_by_window(records, YESTERDAY)
    assert records == snapshot
