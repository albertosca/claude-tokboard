from datetime import datetime, timedelta
from typing import Any

import metrics_core as mr


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


def test_choose_granularity_boundaries_are_inclusive():
    # Each comparison is <=, so exactly-at-the-edge stays in the finer
    # bucket rather than falling through to the coarser one.
    assert mr.choose_granularity([at(), at(hours=2)]) == "minute"
    assert mr.choose_granularity([at(), at(hours=48)]) == "hour"
    assert mr.choose_granularity([at(), at(days=45)]) == "day"
    assert mr.choose_granularity([at(), at(days=365)]) == "week"


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
    assert [b["day"] for b in buckets] == sorted(b["day"] for b in buckets)
    assert len(buckets) == 2
    first, second = buckets
    assert [r["marker"] for r in first["records"]] == ["a", "b"]
    assert [r["marker"] for r in second["records"]] == ["c"]


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
