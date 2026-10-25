"""The weekly-limit panel on the TUI, and its agreement with the HTML page."""

from __future__ import annotations

import asyncio
import html
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from rich.text import Text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_tui_state import seeded_tui_home  # noqa: E402
from weekly_meter_fixtures import meter_for  # noqa: E402

import dashboard_html  # noqa: E402
import dashboard_tui as tui  # noqa: E402


def _view(usages: list[int], current: int = 0, lang: str = "en") -> dict[str, Any]:
    return mr.describe_weekly_meter(meter_for(usages, current), lang)


def _panel_text(view: dict[str, Any]) -> str:
    rendered = tui.WeeklyMeterPanel(view, mr).render()
    assert isinstance(rendered, Text)
    return rendered.plain


def test_the_panel_prints_the_same_numbers_the_html_page_does() -> None:
    view = _view([4_000, 9_000], current=3_000)

    text = _panel_text(view)
    page = html.unescape(dashboard_html.render_weekly_meter(view))

    shared = [view["value_text"], view["tiers"], view["status"], view["range"], view["five_hour"]]
    for row in view["rows"]:
        shared += [row["window"], row["accumulated"], row["tiers"], row["outcome"]]
    for fragment in shared:
        assert fragment in text, fragment
        assert fragment in page, fragment


def test_the_range_is_drawn_with_shaded_cells_and_spelled_out() -> None:
    view = _view([4_000, 9_000], current=3_000)

    bar = tui.meter_bar(view)

    assert len(bar) == tui.METER_BAR_CELLS
    assert "▓" in bar or "▒" in bar
    assert view["range"] in _panel_text(view)


def test_without_a_range_the_bar_has_no_shaded_band() -> None:
    view = _view([4_000], current=500)

    assert not set(tui.meter_bar(view)) & {"▓", "▒"}
    assert "Not enough data to estimate the cap" in _panel_text(view)


def test_the_empty_state_is_text_only() -> None:
    empty = mr.describe_weekly_meter(
        mr.compute_weekly_meter([], [], datetime.fromtimestamp(0, tz=timezone.utc)), "pt"
    )

    text = _panel_text(empty)

    assert "Nenhuma chamada registrada ainda" in text
    assert "█" not in text


def test_the_lower_bound_caveat_is_on_the_panel() -> None:
    assert "only the Claude Code sessions on this machine" in _panel_text(_view([4_000, 9_000]))


def test_the_app_shows_the_panel_on_the_overview_tab_and_omits_it_without_a_view(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = tui.load_data(arguments, metrics_core_module=mr)
    assert data["weekly_meter"]["empty"] is True

    async def drive(with_view: bool) -> int:
        shown = dict(data)
        if not with_view:
            shown["weekly_meter"] = None
        app = tui.MetricsTUIApp(mr, shown, arguments=arguments)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            return len(app.query(tui.WeeklyMeterPanel))

    assert asyncio.run(drive(True)) == 1
    assert asyncio.run(drive(False)) == 0


def test_cycling_the_period_keeps_the_same_weekly_view(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(arguments, metrics_core_module=mr)

    first = tui.apply_period(base, mr.PERIODS[0].id, mr)
    second = tui.apply_period(base, mr.PERIODS[1].id, mr)

    assert first["weekly_meter"] is second["weekly_meter"] is base["weekly_meter"]


def test_the_panel_prints_the_causes_the_html_page_does() -> None:
    from cache_causes_fixtures import causes_for
    from metrics_core import cache_causes_view

    view = _view([4_000, 9_000], current=3_000)
    view["causes"] = cache_causes_view.describe_cache_causes(causes_for(jump=True), "en")

    text = _panel_text(view)
    page = html.unescape(dashboard_html.render_cache_causes(view["causes"]))

    shared = [view["causes"]["heading"], view["causes"]["first_jump"]]
    for row in view["causes"]["windows"]:
        shared += [row["window"], row["total"], row["cold_start"], row["rewrite"]]
        shared += [row["after_idle"], row["main_short"], row["subagent_short"]]
    for row in view["causes"]["sizes"]:
        shared += [row["size"], row["runs"], row["creation"], row["rewrite"]]
    for row in view["causes"]["first_requests"]:
        shared += [row["version"], row["conversations"], row["median"]]
    for fragment in shared:
        assert fragment in text, fragment
        assert fragment in page, fragment


def test_the_panel_without_causes_or_with_an_empty_one_adds_nothing() -> None:
    from cache_causes_fixtures import causes_for
    from metrics_core import cache_causes_view

    view = _view([4_000, 9_000], current=3_000)
    base = _panel_text(view)
    view["causes"] = cache_causes_view.describe_cache_causes(causes_for(), "en")
    view["causes"]["empty"] = True

    assert _panel_text(view) == base


def test_the_tier_alert_reaches_the_panel() -> None:
    from cache_causes_fixtures import causes_for
    from metrics_core import cache_causes_view

    view = _view([4_000, 9_000], current=3_000)
    view["causes"] = cache_causes_view.describe_cache_causes(causes_for("cache_5m"), "en")

    assert f"⚠ {view['causes']['alert']}" in _panel_text(view)


def test_the_window_column_is_as_wide_as_the_longest_label() -> None:
    view = _view([4_000, 9_000], current=3_000)
    view["rows"][0]["window"] = "W" * 50

    lines = _panel_text(view).splitlines()
    row_lines = [line for line in lines if line.startswith(("W" * 50, view["rows"][1]["window"]))]

    assert row_lines[0].index(view["rows"][0]["accumulated"]) == row_lines[1].index(
        view["rows"][1]["accumulated"]
    )
