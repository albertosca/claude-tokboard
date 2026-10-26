"""The weekly-limit panel on the HTML dashboard."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from metrics_core import calls_store, weekly_meter_view, weekly_windows
from weekly_meter_fixtures import meter_for

import dashboard_html


def _view(usages: list[int], current: int = 0, lang: str = "en") -> dict[str, Any]:
    return weekly_meter_view.describe_weekly_meter(meter_for(usages, current), lang)


def test_the_meter_is_a_labelled_role_meter_with_a_value() -> None:
    page = dashboard_html.render_weekly_meter(_view([4_000, 9_000], current=3_000))

    assert 'role="meter"' in page
    assert 'aria-label="Cache creation in the current weekly window"' in page
    assert 'aria-valuenow="3000"' in page
    assert 'aria-valuemin="0"' in page
    assert re.search(r'aria-valuemax="\d+"', page)
    assert 'aria-valuetext="3.0k. Hypothesis, 2 points"' in page


def test_the_range_is_spelled_out_in_text_not_only_drawn() -> None:
    page = dashboard_html.render_weekly_meter(_view([4_000, 9_000], current=3_000))

    assert "meter-range-band" in page
    assert "Accumulated at the first rejection of past weeks: 4.0k to 9.0k" in page


def test_without_a_range_there_is_no_band_and_the_cap_is_not_estimated() -> None:
    page = dashboard_html.render_weekly_meter(_view([4_000], current=500))

    assert "meter-range-band" not in page
    assert "Not enough data to estimate the cap" in page


def test_the_panel_names_the_lower_bound_and_lists_recent_windows() -> None:
    page = dashboard_html.render_weekly_meter(_view([4_000, 9_000], current=3_000))

    assert "only the Claude Code sessions on this machine are counted" in page
    assert '<th scope="col">By model family</th>' in page
    assert '<th scope="col">Outcome</th>' in page
    assert page.count("<tr>") == 1 + len(_view([4_000, 9_000], current=3_000)["rows"])
    assert "Rejected at 9.0k" in page


def test_the_empty_state_is_a_message_with_a_heading_and_no_meter() -> None:
    empty = weekly_meter_view.describe_weekly_meter(
        weekly_windows.compute_weekly_meter([], [], datetime.fromtimestamp(0, tz=timezone.utc)),
        "pt",
    )

    page = dashboard_html.render_weekly_meter(empty)

    assert "Nenhuma chamada registrada ainda" in page
    assert 'role="meter"' not in page
    assert "<h2" in page


def test_text_is_escaped() -> None:
    view = _view([4_000, 9_000], current=3_000)
    view["heading"] = "<script>alert(1)</script>"

    assert "<script>alert" not in dashboard_html.render_weekly_meter(view)


def test_portuguese_page_uses_portuguese_strings() -> None:
    page = dashboard_html.render_weekly_meter(_view([4_000, 9_000], current=3_000, lang="pt"))

    assert "Hipótese, 2 pontos" in page
    assert '<th scope="col">Por família de modelo</th>' in page
    assert '<th scope="col">Resultado</th>' in page


def test_loading_the_view_from_a_metrics_directory(tmp_path: Path) -> None:
    metrics = tmp_path / "metrics"
    stored = calls_store.call_row(
        {
            "type": "assistant",
            "sessionId": "session-1",
            "timestamp": "2026-10-01T10:00:00.000Z",
            "message": {
                "id": "message-1",
                "usage": {"cache_creation": {"ephemeral_5m_input_tokens": 5}},
            },
        },
        "session-1",
        0,
    )
    assert stored is not None
    calls_store.append_rows(metrics / "calls.jsonl", [stored])
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

    assert view["empty"] is False
    assert view["rows"]


def test_loading_from_an_empty_directory_is_the_empty_state(tmp_path: Path) -> None:
    view = weekly_meter_view.load_weekly_meter_view(
        tmp_path / "missing", "en", datetime(2026, 10, 3, tzinfo=timezone.utc)
    )

    assert view["empty"] is True


def test_the_page_carries_the_panel_only_when_given_a_view() -> None:
    from test_dashboard_html_page import _empty_render_html_kwargs

    without = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="en")
    with_panel = dashboard_html.render_html(
        **_empty_render_html_kwargs(), lang="en", weekly_meter=_view([4_000, 9_000], 3_000)
    )

    assert "weekly-meter" not in without.split("</style>", 1)[1]
    assert 'class="weekly-meter"' in with_panel
    assert with_panel.index('class="weekly-meter"') < with_panel.index('class="main-row"')
