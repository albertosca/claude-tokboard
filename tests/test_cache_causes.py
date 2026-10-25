"""Why the cache was written: cold start, rewrite or incremental."""

from typing import Any

from metrics_core import cache_causes
from weekly_meter_fixtures import ANCHOR, WEEK, iso

START = ANCHOR - 100_000


def _call(
    conversation: str,
    offset: float,
    *,
    created: int = 0,
    read: int = 0,
    tier: str = "cache_1h",
    sidechain: int = 0,
    version: str | None = "2.0.1",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "message_id": f"{conversation}-{offset}",
        "conversation": conversation,
        "timestamp": iso(START + offset),
        "sidechain": sidechain,
        "input": 1,
        "cache_read": read,
        "cache_5m": 0,
        "cache_1h": 0,
    }
    row[tier] = created
    if version:
        row["claude_code_version"] = version
    return row


def _meter(window_start: float = START - 10, window_end: float = START + WEEK) -> dict[str, Any]:
    window = {"start": window_start, "end": window_end, "covered": True}
    return {"windows": [window], "current": window}


def _causes(calls: list[dict[str, Any]]) -> dict[str, Any]:
    return cache_causes.compute_cache_causes(calls, _meter())


def test_first_call_is_a_cold_start_big_write_later_is_a_rewrite_small_is_incremental() -> None:
    calls = [
        _call("main", 0, created=50_000),
        _call("main", 60, created=100, read=50_000),
        _call("main", 7_200, created=60_000, read=1_000),
    ]

    window = _causes(calls)["windows"][0]

    assert window["causes"] == {"cold_start": 50_000, "rewrite": 60_000, "incremental": 100}


def test_a_rewrite_after_the_main_idle_limit_is_counted_as_after_idle() -> None:
    quick = [_call("main", 0, created=10), _call("main", 60, created=40_000, read=2)]
    slow = [_call("main", 0, created=10), _call("main", 5_000, created=40_000, read=2)]

    assert _causes(quick)["windows"][0]["rewrite_after_idle"] == 0
    assert _causes(slow)["windows"][0]["rewrite_after_idle"] == 40_000


def test_a_subagent_gap_over_five_minutes_is_idle_but_a_main_gap_of_that_size_is_not() -> None:
    gap = [
        _call("run", 0, created=10, sidechain=1),
        _call("run", 400, created=40_000, read=2, sidechain=1),
    ]
    main = [_call("m", 0, created=10), _call("m", 400, created=40_000, read=2)]

    assert _causes(gap)["windows"][0]["rewrite_after_idle"] == 40_000
    assert _causes(main)["windows"][0]["rewrite_after_idle"] == 0


def test_calls_outside_the_window_or_without_a_readable_time_are_left_out() -> None:
    outside = _call("main", WEEK + 10, created=999)
    undated = {**_call("main", 0, created=7), "timestamp": "not a date"}
    inside = _call("main", 5, created=3)

    window = _causes([outside, undated, inside])["windows"][0]

    assert window["total"] == 3


def test_the_tier_alert_needs_a_main_five_minute_share_over_a_real_sample() -> None:
    big = cache_causes.MINIMUM_TIER_SAMPLE
    mostly_short = [_call("main", 0, created=big, tier="cache_5m")]
    mostly_long = [_call("main", 0, created=big, tier="cache_1h")]
    tiny = [_call("main", 0, created=10, tier="cache_5m")]
    only_subagent = [_call("run", 0, created=big, tier="cache_5m", sidechain=1)]

    assert _causes(mostly_short)["tier_alert"] is True
    assert _causes(mostly_long)["tier_alert"] is False
    assert _causes(tiny)["tier_alert"] is False
    assert _causes(only_subagent)["tier_alert"] is False
    assert _causes([])["tier_alert"] is False


def test_tiers_are_split_between_main_and_subagents() -> None:
    calls = [
        _call("main", 0, created=100, tier="cache_1h"),
        _call("run", 0, created=40, tier="cache_5m", sidechain=1),
    ]

    window = _causes(calls)["windows"][0]

    assert window["main"] == {"cache_5m": 0, "cache_1h": 100}
    assert window["subagent"] == {"cache_5m": 40, "cache_1h": 0}


def test_subagent_runs_are_binned_by_call_count() -> None:
    small = [_call("small", offset, created=1, read=10, sidechain=1) for offset in range(3)]
    large = [_call("large", offset, created=1, read=10, sidechain=1) for offset in range(31)]
    medium = [_call("medium", offset, created=1, read=10, sidechain=1) for offset in range(11)]
    large[5]["cache_1h"] = 90  # a rewrite inside the long run: the cache served nothing
    large[5]["cache_read"] = 0

    sizes = _causes(small + medium + large)["subagent_sizes"]

    assert [(entry["bound"], entry["runs"]) for entry in sizes] == [(10, 1), (30, 1), (None, 1)]
    assert sizes[2]["rewrite"] == 90
    assert sizes[0]["creation"] == 3
    assert not _causes([_call("main", 0, created=5)])["subagent_sizes"][0]["runs"]


def _starts(version: str, size: int, count: int = 5) -> list[dict[str, Any]]:
    return [_call(f"{version}-{index}", 0, created=size, version=version) for index in range(count)]


def test_first_request_median_per_version_and_a_flagged_jump() -> None:
    calls = _starts("2.0.1", 60_000) + _starts("2.0.2", 62_000) + _starts("2.0.10", 80_000)

    first = _causes(calls)["first_requests"]

    assert [entry["version"] for entry in first["versions"]] == ["2.0.1", "2.0.2", "2.0.10"]
    assert first["versions"][2]["median"] == 80_001
    assert first["jump"] == {"from": "2.0.2", "to": "2.0.10"}


def test_a_small_rise_or_a_thin_version_does_not_flag_a_jump() -> None:
    steady = _starts("2.0.1", 60_000) + _starts("2.0.2", 65_000)
    thin = _starts("2.0.1", 60_000) + _starts("2.0.2", 120_000, count=2)

    assert _causes(steady)["first_requests"]["jump"] is None
    assert _causes(thin)["first_requests"]["jump"] is None
    assert [entry["version"] for entry in _causes(thin)["first_requests"]["versions"]] == ["2.0.1"]


def test_subagent_starts_and_unversioned_rows_never_feed_the_first_request_view() -> None:
    calls = [
        _call("run", 0, created=500_000, sidechain=1),
        *[_call(f"old-{index}", 0, created=70_000, version=None) for index in range(6)],
    ]

    assert _causes(calls)["first_requests"]["versions"] == []


def test_an_unparseable_version_sorts_before_real_ones() -> None:
    calls = _starts("dev-build", 10) + _starts("2.0.1", 10)

    versions = _causes(calls)["first_requests"]["versions"]

    assert [entry["version"] for entry in versions] == ["dev-build", "2.0.1"]


def test_a_call_with_no_timestamp_is_skipped_and_no_windows_means_no_alert() -> None:
    undated = {
        key: value for key, value in _call("main", 0, created=9).items() if key != "timestamp"
    }

    result = cache_causes.compute_cache_causes([undated], {"windows": [], "current": None})

    assert result["windows"] == []
    assert result["tier_alert"] is False


def test_early_growth_of_a_young_conversation_is_not_a_lost_cache() -> None:
    calls = [_call("main", 0, created=10_000), _call("main", 30, created=15_000, read=10_000)]

    window = _causes(calls)["windows"][0]

    assert window["causes"]["rewrite"] == 0
    assert window["causes"]["incremental"] == 15_000


def test_a_window_with_no_rejection_still_reports_sizes_and_first_requests() -> None:
    calls = [_call(f"main-{index}", 0, created=10_000) for index in range(5)]

    causes = cache_causes.compute_cache_causes(calls, {"windows": [], "current": None})

    assert causes["windows"] == []
    assert causes["first_requests"]["versions"][0]["version"] == "2.0.1"
