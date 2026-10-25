"""The cache-creation causes panel on the HTML dashboard."""

from typing import Any

from cache_causes_fixtures import causes_for
from metrics_core import cache_causes_view, weekly_meter_view
from weekly_meter_fixtures import meter_for

import dashboard_html
import dashboard_html_assets


def _view(lang: str = "en", jump: bool = False, tier: str = "cache_1h") -> dict[str, Any]:
    return cache_causes_view.describe_cache_causes(causes_for(tier, jump), lang)


def test_the_panel_is_a_labelled_section_with_three_captioned_tables() -> None:
    page = dashboard_html.render_cache_causes(_view(jump=True))

    assert 'aria-labelledby="cache-causes-heading"' in page
    assert 'id="cache-causes-heading"' in page
    assert page.count("<caption>") == 3
    assert page.count('<th scope="col">') >= 10


def test_the_windows_table_carries_the_view_rows() -> None:
    view = _view()
    page = dashboard_html.render_cache_causes(view)

    for key in ("total", "cold_start", "rewrite", "incremental", "after_idle"):
        assert f"<td>{view['windows'][0][key]}</td>" in page


def test_the_panel_is_in_portuguese_when_asked() -> None:
    page = dashboard_html.render_cache_causes(_view("pt"))

    assert "Partida a frio" in page
    assert "Cold start" not in page


def test_the_tier_alert_is_text_not_only_style() -> None:
    page = dashboard_html.render_cache_causes(_view(tier="cache_5m"))

    assert page.count("causes-alert") == 1
    assert "⚠" in page
    assert "causes-alert" not in dashboard_html.render_cache_causes(_view())


def test_the_version_jump_is_text_not_only_style() -> None:
    page = dashboard_html.render_cache_causes(_view(jump=True))

    assert page.count("causes-alert") == 1
    assert "from 2.0.1 (60.0k) to 2.0.2 (80.0k)" in page


def test_an_empty_view_renders_nothing() -> None:
    first: dict[str, Any] = {"versions": [], "jump": None}
    empty = cache_causes_view.describe_cache_causes(
        {"windows": [], "tier_alert": False, "subagent_sizes": [], "first_requests": first}, "en"
    )

    assert dashboard_html.render_cache_causes(empty) == ""


def test_text_is_escaped() -> None:
    view = _view()
    view["note"] = "<script>alert(1)</script>"

    page = dashboard_html.render_cache_causes(view)

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_render_html_places_the_panel_after_the_meter() -> None:
    from test_dashboard_html_page import _empty_render_html_kwargs

    weekly = weekly_meter_view.describe_weekly_meter(meter_for([4_000, 9_000], 3_000), "en")
    weekly["causes"] = _view()
    page = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="en", weekly_meter=weekly)

    assert page.index('class="weekly-meter"') < page.index('id="cache-causes-heading"')
    assert page.index('id="cache-causes-heading"') < page.index('class="main-row"')


def test_a_meter_view_without_causes_renders_no_causes_panel() -> None:
    from test_dashboard_html_page import _empty_render_html_kwargs

    weekly = weekly_meter_view.describe_weekly_meter(meter_for([4_000, 9_000], 3_000), "en")
    page = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="en", weekly_meter=weekly)

    assert 'id="cache-causes-heading"' not in page


def test_each_table_scrolls_inside_its_own_labelled_region() -> None:
    page = dashboard_html.render_cache_causes(_view(jump=True))

    assert page.count('class="table-scroll" role="region"') == 3
    assert page.count('tabindex="0"') == 3
    assert "table-scroll { overflow-x: auto" in dashboard_html_assets.STYLE
