"""The causes as strings, and the loader that feeds both dashboards."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cache_causes_fixtures import causes_for
from metrics_core import cache_causes, cache_causes_view, calls_store, weekly_meter_view


def test_rows_carry_amounts_with_their_share_of_the_window() -> None:
    view = cache_causes_view.describe_cache_causes(causes_for(), "en")

    row = view["windows"][0]
    assert row["total"] == "180.0k"
    assert row["cold_start"] == "50.0k (28%)"
    assert row["rewrite"] == "130.0k (72%)"
    assert row["incremental"] == "0 (0%)"
    assert view["empty"] is False


def test_the_alert_names_the_real_share_and_is_empty_otherwise() -> None:
    alerting = cache_causes_view.describe_cache_causes(causes_for(tier="cache_5m"), "en")
    quiet = cache_causes_view.describe_cache_causes(causes_for(), "en")

    assert "100%" in alerting["alert"]
    assert quiet["alert"] == ""
    assert alerting["windows"][0]["main_short"] == "100%"
    assert quiet["windows"][0]["main_short"] == "0%"
    assert quiet["windows"][0]["subagent_short"] == "none"


def test_size_bins_are_labelled_from_the_constants() -> None:
    view = cache_causes_view.describe_cache_causes(causes_for(), "en")

    bounds = cache_causes.SUBAGENT_SIZE_BOUNDS
    assert [row["size"] for row in view["sizes"]] == [
        f"up to {bounds[0]}",
        f"{bounds[0] + 1} to {bounds[1]}",
        f"over {bounds[1]}",
    ]


def test_a_flagged_jump_is_spelled_out_with_both_versions() -> None:
    view = cache_causes_view.describe_cache_causes(causes_for(jump=True), "pt")

    assert "2.0.1 (60.0k)" in view["first_jump"]
    assert "2.0.2 (80.0k)" in view["first_jump"]
    assert "20%" in view["first_jump"]
    assert cache_causes_view.describe_cache_causes(causes_for(), "pt")["first_jump"] == ""


def test_no_windows_is_the_empty_state() -> None:
    causes = cache_causes.compute_cache_causes([], {"windows": [], "current": None})

    assert cache_causes_view.describe_cache_causes(causes, "en")["empty"] is True


def test_only_the_recent_windows_are_shown() -> None:
    causes: dict[str, Any] = causes_for()
    causes["windows"] = causes["windows"] * (cache_causes_view.RECENT_CAUSE_WINDOWS + 3)

    view = cache_causes_view.describe_cache_causes(causes, "en")

    assert len(view["windows"]) == cache_causes_view.RECENT_CAUSE_WINDOWS


def test_a_window_without_calls_has_zero_shares_not_a_division_error() -> None:
    causes: dict[str, Any] = causes_for()
    for window in causes["windows"]:
        window["causes"] = dict.fromkeys(cache_causes.CAUSES, 0)
        window["total"] = 0
        window["covered"] = False
        window["open"] = False

    row = cache_causes_view.describe_cache_causes(causes, "en")["windows"][0]

    assert row["rewrite"] == "0 (0%)"
    assert "partial" in row["window"]


def test_the_loader_returns_the_meter_and_its_causes_from_one_read(tmp_path: Path) -> None:
    metrics = tmp_path / "metrics"
    calls_store.append_rows(
        metrics / "calls.jsonl",
        [
            {
                "message_id": "message-1",
                "conversation": "conversation-1",
                "timestamp": "2026-10-01T10:00:00.000Z",
                "sidechain": 0,
                "cache_1h": 5,
            }
        ],
    )
    calls_store.append_rows(
        metrics / "limit-hits.jsonl",
        [
            {
                "timestamp": "2026-10-02T10:00:00.000Z",
                "rate_limit_type": "seven_day",
                "resets_at": 1_791_000_000,
            }
        ],
    )

    view = weekly_meter_view.load_weekly_meter_view(
        metrics, "en", datetime(2026, 10, 3, tzinfo=timezone.utc)
    )

    assert view["causes"]["heading"] == "Where the cache creation came from"
    assert view["causes"]["empty"] is False


def test_run_and_conversation_counts_are_plain_numbers_not_tokens() -> None:
    causes: dict[str, Any] = causes_for(jump=True)
    causes["subagent_sizes"][0]["runs"] = 1_234
    causes["first_requests"]["versions"][0]["conversations"] = 1_234

    view = cache_causes_view.describe_cache_causes(causes, "en")

    assert view["sizes"][0]["runs"] == "1234"
    assert view["first_requests"][0]["conversations"] == "1234"


def test_a_view_with_no_windows_but_other_tables_is_not_empty() -> None:
    causes: dict[str, Any] = causes_for(jump=True)
    causes["windows"] = []

    view = cache_causes_view.describe_cache_causes(causes, "en")

    assert view["empty"] is False
    assert view["windows"] == []
