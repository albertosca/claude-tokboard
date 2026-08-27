from datetime import datetime, timezone

import metrics_core as mr

# --- format_tokens ---------------------------------------------------


def test_format_tokens_none_is_a_dash():
    assert mr.format_tokens(None) == "-"


def test_format_tokens_under_a_thousand_is_plain():
    assert mr.format_tokens(0) == "0"
    assert mr.format_tokens(999) == "999"


def test_format_tokens_scales_through_every_suffix():
    assert mr.format_tokens(1_500) == "1.5k"
    assert mr.format_tokens(2_400_000) == "2.4M"
    assert mr.format_tokens(3_700_000_000) == "3.7B"
    assert mr.format_tokens(1_900_000_000_000) == "1.9T"


def test_format_tokens_boundaries_pick_the_larger_unit():
    # Each threshold is >=, so exactly-at-the-boundary reads as 1.0 of
    # the bigger unit rather than 1000 of the smaller one.
    assert mr.format_tokens(1_000) == "1.0k"
    assert mr.format_tokens(1_000_000) == "1.0M"
    assert mr.format_tokens(1_000_000_000) == "1.0B"
    assert mr.format_tokens(1_000_000_000_000) == "1.0T"


def test_format_tokens_uses_magnitude_so_negatives_scale_too():
    # The comparisons are on abs(n) but the formatting divides n itself.
    assert mr.format_tokens(-1_500) == "-1.5k"
    assert mr.format_tokens(-999) == "-999"


def test_format_tokens_truncates_a_float_toward_zero():
    assert mr.format_tokens(1500.9) == "1.5k"
    # Under rounding, 999.9 would become 1000 -> "1.0k" - this only
    # passes under truncation, which discriminates int(n) from round(n).
    assert mr.format_tokens(999.9) == "999"


# --- format_duration -------------------------------------------------


def test_format_duration_none_is_a_dash():
    assert mr.format_duration(None) == "-"


def test_format_duration_under_a_minute_is_seconds():
    assert mr.format_duration(0) == "0s"
    assert mr.format_duration(59) == "59s"


def test_format_duration_at_a_minute_switches_units():
    assert mr.format_duration(60) == "1min"


def test_format_duration_rounds_minutes_to_whole_numbers():
    # 90s is 1.5min, and the format spec is .0f - Python rounds half to
    # even, so this is 2min, not 1min.
    assert mr.format_duration(90) == "2min"
    assert mr.format_duration(3_540) == "59min"


def test_format_duration_at_an_hour_switches_again_and_pads_minutes():
    assert mr.format_duration(3_600) == "1h00min"
    assert mr.format_duration(3_660) == "1h01min"
    assert mr.format_duration(7_200) == "2h00min"


# --- format_date -----------------------------------------------------


def test_format_date_empty_or_none_is_a_dash():
    assert mr.format_date(None) == "-"
    assert mr.format_date("") == "-"


def test_format_date_unparseable_is_a_dash():
    assert mr.format_date("not a timestamp") == "-"


def test_format_date_renders_day_month_and_local_time():
    # Built from the local zone so this asserts a real formatted value
    # without hardcoding the machine's UTC offset.
    moment = datetime(2026, 3, 7, 14, 5).astimezone()
    assert mr.format_date(moment.isoformat()) == "07/03 14:05"


def test_format_date_accepts_a_trailing_z_and_converts_to_local():
    # The Z suffix is replaced with +00:00 before parsing, so the result
    # is that UTC instant expressed in the local zone.
    utc_moment = datetime(2026, 3, 7, 14, 5, tzinfo=timezone.utc)
    expected = utc_moment.astimezone().strftime("%d/%m %H:%M")
    assert mr.format_date("2026-03-07T14:05:00Z") == expected
