"""The display model both dashboards render the weekly meter from."""

from datetime import datetime, timezone

from metrics_core import weekly_meter_view, weekly_windows
from weekly_meter_fixtures import ANCHOR, WEEK
from weekly_meter_fixtures import call as _call
from weekly_meter_fixtures import hit as _hit
from weekly_meter_fixtures import meter_for as _meter


def test_no_data_renders_an_empty_state_with_a_reason() -> None:
    meter = weekly_windows.compute_weekly_meter(
        [], [], datetime.fromtimestamp(ANCHOR, tz=timezone.utc)
    )

    view = weekly_meter_view.describe_weekly_meter(meter, "en")

    assert view["empty"] is True
    assert view["status"] == "No calls stored yet"
    assert view["rows"] == []


def test_a_hypothesis_view_carries_value_range_tiers_and_caveat() -> None:
    view = weekly_meter_view.describe_weekly_meter(_meter([4_000, 9_000], current=3_000), "en")

    assert view["empty"] is False
    assert view["status"] == "Hypothesis, 2 points"
    assert view["value_text"] == "3.0k"
    assert view["tiers"] == "5-minute 1.0k · 1-hour 2.0k"
    assert view["range"] == "Accumulated at the first rejection of past weeks: 4.0k to 9.0k"
    assert view["value"] == 3_000
    assert view["scale_maximum"] >= 9_000
    assert 0 < view["fill_percent"] < view["range_start_percent"] < view["range_end_percent"] <= 100
    assert "this machine" in view["caveat"]
    assert "Last 5 hours" in view["five_hour"]


def test_one_point_says_the_cap_cannot_be_estimated_and_draws_no_range() -> None:
    view = weekly_meter_view.describe_weekly_meter(_meter([4_000], current=500), "en")

    assert view["status"] == "Not enough data to estimate the cap"
    assert view["range"] is None
    assert view["range_start_percent"] is None


def test_the_singular_point_label() -> None:
    assert weekly_meter_view.points_label(1, "en") == "Hypothesis, 1 point"
    assert weekly_meter_view.points_label(3, "pt") == "Hipótese, 3 pontos"


def test_a_contradicted_range_is_named() -> None:
    view = weekly_meter_view.describe_weekly_meter(_meter([4_000, 9_000], current=50_000), "en")

    assert view["status"].startswith("Contradicted")


def test_portuguese_strings_replace_the_english_ones() -> None:
    view = weekly_meter_view.describe_weekly_meter(_meter([4_000, 9_000], current=3_000), "pt")

    assert view["status"] == "Hipótese, 2 pontos"
    assert "Limite inferior" in view["caveat"]
    assert view["heading"] == "Medidor do limite semanal (hipótese)"


def test_rows_list_recent_windows_newest_first_with_their_outcome() -> None:
    view = weekly_meter_view.describe_weekly_meter(_meter([4_000, 9_000], current=3_000), "en")

    outcomes = [row["outcome"] for row in view["rows"]]
    assert outcomes[0] == "In progress"
    assert "Rejected at 9.0k" in outcomes[1]
    assert "Rejected at 4.0k" in outcomes[2]
    assert all(set(row) == {"window", "accumulated", "tiers", "outcome"} for row in view["rows"])


def test_the_table_keeps_only_the_most_recent_windows() -> None:
    view = weekly_meter_view.describe_weekly_meter(_meter([4_000] * 12, current=10), "en")

    assert len(view["rows"]) == weekly_meter_view.RECENT_WINDOWS


def test_a_partial_week_is_marked_as_partial_data() -> None:
    calls = [_call(ANCHOR - 2 * 24 * 3600, 10)]
    now = datetime.fromtimestamp(ANCHOR + 100, tz=timezone.utc)

    view = weekly_meter_view.describe_weekly_meter(
        weekly_windows.compute_weekly_meter(calls, [_hit(ANCHOR - 100, ANCHOR)], now), "en"
    )

    assert any(row["outcome"].endswith("· partial data") for row in view["rows"])


def test_a_window_with_no_rejection_reads_as_clear() -> None:
    calls = [_call(ANCHOR - 3 * WEEK, 1), _call(ANCHOR - WEEK + 100, 6)]
    hits = [_hit(ANCHOR - 100, ANCHOR)]
    now = datetime.fromtimestamp(ANCHOR + WEEK + 100, tz=timezone.utc)

    view = weekly_meter_view.describe_weekly_meter(
        weekly_windows.compute_weekly_meter(calls, hits, now), "en"
    )

    assert "No rejection" in [row["outcome"] for row in view["rows"]]
