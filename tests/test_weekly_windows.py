"""The weekly-limit meter: windows, the observed range and its state."""

from datetime import datetime, timezone
from typing import Any

from metrics_core import weekly_windows

WEEK = 7 * 24 * 3600
ANCHOR = 1_800_000_000  # a synthetic weekly reset, epoch seconds


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _now(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def _call(epoch: float, five_minute: int = 0, one_hour: int = 0, **extra: Any) -> dict[str, Any]:
    return {
        "message_id": f"message-{epoch}",
        "timestamp": _iso(epoch),
        "cache_5m": five_minute,
        "cache_1h": one_hour,
        **extra,
    }


def _hit(epoch: float, resets_at: Any, kind: str = "seven_day") -> dict[str, Any]:
    return {
        "timestamp": _iso(epoch),
        "rate_limit_type": kind,
        "resets_at": resets_at,
        "status": "rejected",
    }


def _week_with_rejection(end: int, usage: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Calls filling a window up to `usage`, then a rejection at the window's last hour."""
    start = end - WEEK
    calls = [_call(start + 3600, five_minute=usage // 2, one_hour=usage - usage // 2)]
    return calls, _hit(end - 3600, end)


def test_no_calls_is_the_no_data_state() -> None:
    meter = weekly_windows.compute_weekly_meter([], [], _now(ANCHOR))

    assert meter["state"] == "no_data"
    assert meter["windows"] == []
    assert meter["current"] is None
    assert meter["range"] is None


def test_calls_without_any_rejection_have_no_anchor_to_place_a_window() -> None:
    meter = weekly_windows.compute_weekly_meter([_call(ANCHOR - 100, 5)], [], _now(ANCHOR))

    assert meter["state"] == "no_anchor"
    assert meter["windows"] == []


def test_a_hit_without_a_usable_reset_time_never_anchors_a_window() -> None:
    hits = [_hit(ANCHOR - 10, None), _hit(ANCHOR - 9, "soon"), _hit(ANCHOR - 8, True)]

    meter = weekly_windows.compute_weekly_meter([_call(ANCHOR - 100, 5)], hits, _now(ANCHOR))

    assert meter["state"] == "no_anchor"


def test_a_rejection_places_a_real_window_and_sums_both_tiers() -> None:
    calls = [_call(ANCHOR - WEEK + 10, 30, 70), _call(ANCHOR - 10, 5, 0)]
    hits = [_hit(ANCHOR - 5, ANCHOR)]

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(ANCHOR + 100))

    closed = next(window for window in meter["windows"] if window["end"] == ANCHOR)
    assert (closed["cache_5m"], closed["cache_1h"], closed["accumulated"]) == (35, 70, 105)
    assert closed["rejected"] is True
    assert closed["inferred"] is False
    assert closed["first_rejection_accumulated"] == 105


def test_the_window_in_progress_is_inferred_by_rolling_the_anchor_forward() -> None:
    calls = [_call(ANCHOR - 100, 1), _call(ANCHOR + 3600, 40, 2)]
    hits = [_hit(ANCHOR - 50, ANCHOR)]

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(ANCHOR + 7200))

    current = meter["current"]
    assert current["end"] == ANCHOR + WEEK
    assert current["inferred"] is True
    assert current["accumulated"] == 42


def test_a_window_that_starts_before_the_first_stored_call_is_partial() -> None:
    calls = [_call(ANCHOR - 3600, 10)]
    hits = [_hit(ANCHOR - 5, ANCHOR)]

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(ANCHOR + 100))

    closed = next(window for window in meter["windows"] if window["end"] == ANCHOR)
    assert closed["covered"] is False


def test_the_range_is_the_min_and_max_at_first_rejection_of_covered_closed_windows() -> None:
    calls: list[dict[str, Any]] = [_call(ANCHOR - 3 * WEEK - 10, 1)]
    hits: list[dict[str, Any]] = []
    for end, usage in ((ANCHOR - WEEK, 400), (ANCHOR, 900)):
        week_calls, hit = _week_with_rejection(end, usage)
        calls += week_calls
        hits.append(hit)

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(ANCHOR + 100))

    assert meter["range"] == {"minimum": 400, "maximum": 900, "points": 2}
    assert meter["state"] == "hypothesis"


def test_one_point_is_not_enough_to_estimate_a_range() -> None:
    calls: list[dict[str, Any]] = [_call(ANCHOR - 2 * WEEK, 1)]
    week_calls, hit = _week_with_rejection(ANCHOR, 500)

    meter = weekly_windows.compute_weekly_meter(calls + week_calls, [hit], _now(ANCHOR + 100))

    assert meter["range"] is None
    assert meter["state"] == "insufficient"


def test_a_partial_window_never_feeds_the_range() -> None:
    first_calls, first_hit = _week_with_rejection(ANCHOR - WEEK, 400)
    second_calls, second_hit = _week_with_rejection(ANCHOR, 900)
    # The earliest stored call sits inside the first window: that window is partial.
    first_calls[0]["timestamp"] = _iso(ANCHOR - WEEK - WEEK + 3 * 24 * 3600)

    meter = weekly_windows.compute_weekly_meter(
        first_calls + second_calls, [first_hit, second_hit], _now(ANCHOR + 100)
    )

    assert meter["range"] is None
    assert meter["state"] == "insufficient"


def test_a_covered_week_past_the_maximum_without_a_rejection_contradicts_the_range() -> None:
    calls: list[dict[str, Any]] = [_call(ANCHOR - 4 * WEEK - 10, 1)]
    hits: list[dict[str, Any]] = []
    for end, usage in ((ANCHOR - 2 * WEEK, 400), (ANCHOR - WEEK, 900)):
        week_calls, hit = _week_with_rejection(end, usage)
        calls += week_calls
        hits.append(hit)
    calls.append(_call(ANCHOR - 3600, 2000))  # a whole week at 2000, never rejected

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(ANCHOR + 100))

    assert meter["state"] == "contradicted"


def test_the_five_hour_window_reports_only_the_accumulated_value_and_rejections() -> None:
    now = ANCHOR
    calls = [_call(now - 3600, 10, 5), _call(now - 6 * 3600, 99, 99)]
    hits = [
        _hit(now - 7200, now - 3000, kind="five_hour"),
        _hit(now - 7100, now - 3000, kind="five_hour"),
        _hit(now - 100, now + 900, kind="five_hour"),
    ]

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(now))

    assert meter["five_hour"] == {"accumulated": 15, "rejected_windows": 2}
    assert "range" not in meter["five_hour"]


def test_calls_with_an_unreadable_timestamp_are_skipped() -> None:
    calls = [{"message_id": "m", "timestamp": "not a date", "cache_5m": 9, "cache_1h": 0}]

    meter = weekly_windows.compute_weekly_meter(calls, [_hit(ANCHOR, ANCHOR)], _now(ANCHOR))

    assert meter["state"] == "no_data"


def test_an_off_grid_real_window_replaces_the_inferred_one_it_overlaps() -> None:
    shifted = ANCHOR + 2 * 24 * 3600  # a reset that moved by two days
    calls = [_call(ANCHOR - 10, 1), _call(shifted - 10, 2)]
    hits = [_hit(ANCHOR - 5, ANCHOR), _hit(shifted - 5, shifted)]

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(shifted + 100))

    real = [window for window in meter["windows"] if not window["inferred"]]
    assert {window["end"] for window in real} == {ANCHOR, shifted}
    for window in meter["windows"]:
        if window["inferred"]:
            for anchor_window in real:
                assert (
                    window["end"] <= anchor_window["start"]
                    or window["start"] >= anchor_window["end"]
                )


def test_a_call_without_a_timestamp_is_skipped() -> None:
    calls = [{"message_id": "m", "cache_5m": 9}, _call(ANCHOR - 10, 4)]

    meter = weekly_windows.compute_weekly_meter(calls, [_hit(ANCHOR - 5, ANCHOR)], _now(ANCHOR))

    assert sum(window["accumulated"] for window in meter["windows"]) == 4


def test_a_reset_time_far_ahead_of_the_clock_still_yields_the_window_in_progress() -> None:
    far_reset = ANCHOR + 3 * WEEK  # a skewed clock: the recorded reset is weeks away
    calls = [_call(ANCHOR - 100, 7)]

    meter = weekly_windows.compute_weekly_meter(calls, [_hit(ANCHOR, far_reset)], _now(ANCHOR))

    assert meter["current"]["end"] == ANCHOR + WEEK
    assert meter["current"]["inferred"] is True
    assert meter["current"]["accumulated"] == 0
    assert sum(window["accumulated"] for window in meter["windows"]) == 7


def test_a_reset_time_in_milliseconds_is_unusable_not_a_crash() -> None:
    calls = [_call(ANCHOR - 100, 7)]

    meter = weekly_windows.compute_weekly_meter(calls, [_hit(ANCHOR, ANCHOR * 1000)], _now(ANCHOR))

    assert meter["state"] == "no_anchor"


def test_the_window_in_progress_never_contradicts_the_range() -> None:
    calls: list[dict[str, Any]] = [_call(ANCHOR - 4 * WEEK, 1)]
    hits: list[dict[str, Any]] = []
    for end in (ANCHOR - 2 * WEEK, ANCHOR - WEEK):
        week_calls, hit = _week_with_rejection(end, 100)
        calls += week_calls
        hits.append(hit)
    calls.append(_call(ANCHOR + 3 * 24 * 3600, 500))  # three days into the open week

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(ANCHOR + 4 * 24 * 3600))

    assert meter["current"]["accumulated"] == 500
    assert meter["state"] == "hypothesis"


def test_two_rejections_with_reset_times_a_minute_apart_are_one_week() -> None:
    calls: list[dict[str, Any]] = [_call(ANCHOR - 4 * WEEK, 1)]
    hits: list[dict[str, Any]] = []
    for end in (ANCHOR - WEEK, ANCHOR):
        week_calls, hit = _week_with_rejection(end, 100)
        calls += week_calls
        hits.append(hit)
    hits.append(_hit(ANCHOR - 3000, ANCHOR + 60))  # the same week, a jittered reset

    meter = weekly_windows.compute_weekly_meter(calls, hits, _now(ANCHOR + 100))

    assert meter["range"]["points"] == 2
