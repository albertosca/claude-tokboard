from datetime import datetime, timedelta
from typing import Any

import metrics_core as mr
import pytest


def at(**offset: int) -> datetime:
    """A local-zone timestamp offset from a fixed base moment."""
    return datetime(2026, 3, 7, 12, 0).astimezone() + timedelta(**offset)


# --- choose_granularity ----------------------------------------------


def test_choose_granularity_defaults_to_day_below_two_points():
    # A span needs two points to exist at all.
    assert mr.choose_granularity([]) == "day"
    assert mr.choose_granularity([at()]) == "day"


def test_choose_granularity_picks_each_bucket_by_span():
    assert mr.choose_granularity([at(), at(hours=1)]) == "minute"
    assert mr.choose_granularity([at(), at(hours=10)]) == "hour"
    assert mr.choose_granularity([at(), at(days=10)]) == "day"
    assert mr.choose_granularity([at(), at(days=100)]) == "week"
    assert mr.choose_granularity([at(), at(days=700)]) == "month"
    assert mr.choose_granularity([at(), at(days=2000)]) == "year"


def test_choose_granularity_thresholds_are_pinned_from_both_sides():
    # Each comparison is <=, so exactly-at-the-edge stays in the finer
    # bucket rather than falling through to the coarser one - and one
    # minute past the edge must actually cross into the coarser bucket
    # (otherwise the threshold could be widened without any test
    # failing, since both arcs would still execute either way).
    assert mr.choose_granularity([at(), at(hours=2)]) == "minute"
    assert mr.choose_granularity([at(), at(hours=2, minutes=1)]) == "hour"
    assert mr.choose_granularity([at(), at(hours=48)]) == "hour"
    assert mr.choose_granularity([at(), at(hours=48, minutes=1)]) == "day"
    assert mr.choose_granularity([at(), at(days=45)]) == "day"
    assert mr.choose_granularity([at(), at(days=45, minutes=1)]) == "week"
    assert mr.choose_granularity([at(), at(days=365)]) == "week"
    assert mr.choose_granularity([at(), at(days=365, minutes=1)]) == "month"
    assert mr.choose_granularity([at(), at(days=365 * 3)]) == "month"
    assert mr.choose_granularity([at(), at(days=365 * 3, minutes=1)]) == "year"


def test_choose_granularity_uses_the_full_span_not_input_order():
    # max - min, so an unsorted list gives the same answer.
    unsorted_stamps = [at(days=100), at(), at(days=50)]
    assert mr.choose_granularity(unsorted_stamps) == "week"


# --- GRANULARITY_CONFIG ----------------------------------------------


def test_every_key_fmt_round_trips_through_its_own_parse_fmt():
    # The module's own comment claims parse_fmt "must round-trip key_fmt
    # exactly" - this asserts that contract rather than trusting it,
    # including the week entry's deliberate literal "-1"/"%u" pairing.
    moment = datetime(2026, 3, 7, 14, 5).astimezone()
    for granularity, config in mr.GRANULARITY_CONFIG.items():
        key = moment.strftime(config["key_fmt"])
        parsed = datetime.strptime(key, config["parse_fmt"])
        assert parsed.strftime(config["key_fmt"]) == key, granularity


# --- aggregate_by_day ------------------------------------------------


def record(timestamp_start: str, marker: str) -> dict[str, Any]:
    return {"timestamp_start": timestamp_start, "marker": marker}


def test_aggregate_by_day_buckets_records_and_reports_granularity():
    granularity, buckets = mr.aggregate_by_day(
        [
            record(at().isoformat(), "a"),
            record(at(minutes=5).isoformat(), "b"),
            record(at(days=10).isoformat(), "c"),
        ]
    )
    assert granularity == "day"
    assert [b["day"] for b in buckets] == ["2026-03-07", "2026-03-17"]
    assert len(buckets) == 2
    first, second = buckets
    assert [r["marker"] for r in first["records"]] == ["a", "b"]
    assert [r["marker"] for r in second["records"]] == ["c"]


def test_aggregate_by_day_uses_minute_granularity_for_a_short_span():
    # Pins that aggregate_by_day really calls choose_granularity (not a
    # hardcoded "day") AND that the returned bucket keys use the minute
    # key_fmt from GRANULARITY_CONFIG, not day resolution.
    granularity, buckets = mr.aggregate_by_day(
        [
            record(at().isoformat(), "a"),
            record(at(minutes=30).isoformat(), "b"),
        ]
    )
    assert granularity == "minute"
    assert [b["day"] for b in buckets] == ["2026-03-07T12:00", "2026-03-07T12:30"]


def test_aggregate_by_day_skips_records_with_no_timestamp():
    _granularity, buckets = mr.aggregate_by_day(
        [record(at().isoformat(), "kept"), {"marker": "no timestamp"}, record("", "empty")]
    )
    assert sum(len(b["records"]) for b in buckets) == 1
    assert buckets[0]["records"][0]["marker"] == "kept"


def test_aggregate_by_day_skips_unparseable_timestamps():
    _granularity, buckets = mr.aggregate_by_day(
        [record(at().isoformat(), "kept"), record("not a timestamp", "dropped")]
    )
    assert sum(len(b["records"]) for b in buckets) == 1
    assert buckets[0]["records"][0]["marker"] == "kept"


def test_aggregate_by_day_on_no_usable_records_is_empty_and_defaults_to_day():
    granularity, buckets = mr.aggregate_by_day([])
    assert granularity == "day"
    assert buckets == []


def test_aggregate_by_day_sorts_buckets_even_when_input_is_reversed():
    _granularity, buckets = mr.aggregate_by_day(
        [record(at(days=10).isoformat(), "later"), record(at().isoformat(), "earlier")]
    )
    assert [r["records"][0]["marker"] for r in buckets] == ["earlier", "later"]


# --- partial_bucket_fraction -------------------------------------------


def test_partial_bucket_fraction_is_none_once_the_bucket_has_fully_elapsed():
    # Bucket "2026-03-07" (day granularity) spans 2026-03-07 00:00 ->
    # 2026-03-08 00:00. A `now` past that instant is fully elapsed.
    assert mr.partial_bucket_fraction("day", "2026-03-07", at(hours=13)) is None


def test_partial_bucket_fraction_is_none_exactly_at_the_bucket_end():
    # The end instant itself belongs to the NEXT bucket, not this one.
    assert mr.partial_bucket_fraction("day", "2026-03-07", at(hours=12)) is None


def test_partial_bucket_fraction_is_zero_at_or_before_the_start():
    assert mr.partial_bucket_fraction("day", "2026-03-07", at(hours=-12)) == 0.0
    # Before the bucket even starts clamps to 0 too, not a negative value.
    assert mr.partial_bucket_fraction("day", "2026-03-07", at(days=-5)) == 0.0


def test_partial_bucket_fraction_is_the_elapsed_share_mid_bucket():
    # Bucket is the day 2026-03-07 00:00 -> 2026-03-08 00:00 (24h); `at()`
    # is 2026-03-07 12:00, exactly halfway in.
    assert mr.partial_bucket_fraction("day", "2026-03-07", at()) == 0.5


def test_partial_bucket_fraction_strips_a_timezone_aware_now():
    # `at()` is already timezone-aware (.astimezone()) - this is the
    # normal call shape from both dashboards, and must give the same
    # answer as a naive local wall-clock moment.
    aware_answer = mr.partial_bucket_fraction("day", "2026-03-07", at())
    naive_answer = mr.partial_bucket_fraction("day", "2026-03-07", at().replace(tzinfo=None))
    assert aware_answer == naive_answer == 0.5


def test_partial_bucket_fraction_covers_every_granularitys_bucket_length():
    # One fixed elapsed offset against each granularity's own bucket
    # length, so every _bucket_end branch - including the December ->
    # January month rollover and a leap year - is exercised with a
    # verified (computed by running datetime arithmetic, not by hand),
    # assertable fraction rather than just "some float in (0, 1)".
    cases = [
        ("minute", "2026-03-07T12:00", timedelta(seconds=30), 60),
        ("hour", "2026-03-07T12", timedelta(minutes=30), 60 * 60),
        ("day", "2026-03-07", timedelta(hours=12), 24 * 60 * 60),
    ]
    for granularity, key, elapsed, bucket_seconds in cases:
        start = datetime.strptime(key, mr.GRANULARITY_CONFIG[granularity]["parse_fmt"])
        now = start + elapsed
        expected = elapsed.total_seconds() / bucket_seconds
        assert mr.partial_bucket_fraction(granularity, key, now) == pytest.approx(expected)

    # week: 7 days. 2026-08-31 is a confirmed Monday (BACKLOG.md's own
    # 2026-09-02 entry: "2026-W36-1 holds 3 days with data (08-31, 09-01,
    # 09-02)", and ISO weeks always start Monday).
    week_start = datetime(2026, 8, 31)
    week_key = week_start.strftime(mr.GRANULARITY_CONFIG["week"]["key_fmt"])
    assert mr.partial_bucket_fraction(
        "week", week_key, week_start + timedelta(days=1, hours=12)
    ) == pytest.approx(1.5 / 7)

    # month, not spanning a year boundary (March 2026 has 31 days)
    assert mr.partial_bucket_fraction("month", "2026-03", datetime(2026, 3, 16)) == pytest.approx(
        15 / 31
    )

    # month, December -> January rollover - the one non-uniform branch of
    # _bucket_end (December has 31 days)
    assert mr.partial_bucket_fraction("month", "2025-12", datetime(2025, 12, 16)) == pytest.approx(
        15 / 31
    )

    # year: 2026 is not a leap year (365 days), 2028 is (366 days)
    assert mr.partial_bucket_fraction("year", "2026", datetime(2026, 7, 2, 12)) == pytest.approx(
        0.5
    )
    assert mr.partial_bucket_fraction("year", "2028", datetime(2028, 7, 1)) == pytest.approx(
        182 / 366
    )
