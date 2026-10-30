"""The cache-creation causes panel on the HTML dashboard."""

from typing import Any

from cache_causes_fixtures import causes_for
from metrics_core import cache_causes_view, weekly_meter_view
from weekly_meter_fixtures import meter_for

import dashboard_html
import dashboard_html_assets


def _view(lang: str = "en", jump: bool = False, tier: str = "cache_1h") -> dict[str, Any]:
    return cache_causes_view.describe_cache_causes(causes_for(tier, jump), lang)


def test_the_panel_is_a_labelled_section_with_six_captioned_tables() -> None:
    page = dashboard_html.render_cache_causes(_view(jump=True))

    assert 'aria-labelledby="cache-causes-heading"' in page
    assert 'id="cache-causes-heading"' in page
    assert page.count("<caption") == 6
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
        {
            "windows": [],
            "tier_alert": False,
            "subagent_sizes": [],
            "first_requests": first,
            "first_by_project": [],
            "idle_sessions": [],
            "short_by_project": [],
        },
        "en",
    )

    assert dashboard_html.render_cache_causes(empty) == ""


def test_text_is_escaped() -> None:
    view = _view()
    view["note"] = "<script>alert(1)</script>"

    page = dashboard_html.render_cache_causes(view)

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


def test_render_html_places_the_panel_after_the_meter_inside_the_hypotheses_tab() -> None:
    from test_dashboard_html_page import _empty_render_html_kwargs

    weekly = weekly_meter_view.describe_weekly_meter(meter_for([4_000, 9_000], 3_000), "en")
    weekly["causes"] = _view()
    page = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="en", weekly_meter=weekly)

    assert page.index('id="tab-hypotheses"') < page.index('class="weekly-meter"')
    assert page.index('class="weekly-meter"') < page.index('id="cache-causes-heading"')


def test_a_meter_view_without_causes_renders_no_causes_panel() -> None:
    from test_dashboard_html_page import _empty_render_html_kwargs

    weekly = weekly_meter_view.describe_weekly_meter(meter_for([4_000, 9_000], 3_000), "en")
    page = dashboard_html.render_html(**_empty_render_html_kwargs(), lang="en", weekly_meter=weekly)

    assert 'id="cache-causes-heading"' not in page


def test_each_table_scrolls_inside_its_own_labelled_region() -> None:
    page = dashboard_html.render_cache_causes(_view(jump=True))

    assert page.count('class="table-scroll" role="region"') == 6
    assert page.count('tabindex="0"') == 6
    assert "table-scroll { overflow-x: auto" in dashboard_html_assets.STYLE


def test_no_windows_table_when_there_are_no_windows() -> None:
    causes: dict[str, Any] = causes_for(jump=True)
    causes["windows"] = []
    view = cache_causes_view.describe_cache_causes(causes, "en")

    page = dashboard_html.render_cache_causes(view)

    assert page.count("<caption") == 5


def test_the_card_opens_with_the_headline_and_a_bar_per_window() -> None:
    view = _view()
    page = dashboard_html.render_cache_causes(view)

    assert f'<p class="card-headline">{view["headline"]}</p>' in page
    assert page.index("card-headline") < page.index("cause-bar")
    assert page.count('class="cause-bar"') == len(view["bars"])
    assert f'aria-label="{view["bars"][0]["alt"]}"' in page


def test_every_table_sits_in_a_closed_named_disclosure() -> None:
    page = dashboard_html.render_cache_causes(_view(jump=True))

    assert "<details open" not in page
    assert page.count("<details") == page.count("<table") + 1  # the six tables and the terms
    assert "<summary>Subagent runs by number of calls (all stored history) (3)</summary>" in page
    assert page.count('<caption class="visually-hidden">') == 6


def test_the_definitions_note_is_behind_a_disclosure() -> None:
    view = _view()
    page = dashboard_html.render_cache_causes(view)

    assert f"<summary>{view['terms_title']}</summary>" in page
    assert page.index(f"<summary>{view['terms_title']}") < page.index(view["note"][:20])


def test_the_alerts_share_one_attention_strip() -> None:
    view = _view(tier="cache_5m")
    view["first_jump"] = _view(jump=True)["first_jump"]
    page = dashboard_html.render_cache_causes(view)

    assert page.count('class="causes-attention"') == 1
    assert page.count("⚠") == 2
    assert "causes-attention" not in dashboard_html.render_cache_causes(_view())


def test_a_window_with_no_creation_draws_no_headline() -> None:
    view = _view()
    view["headline"] = ""

    assert "card-headline" not in dashboard_html.render_cache_causes(view)


def test_markup_in_a_label_is_escaped_in_bars_and_summaries() -> None:
    view = _view()
    view["bars"][0]["window"] = "<img src=x onerror=alert(1)>"
    view["bars"][0]["alt"] = '"><script>alert(1)</script>'
    view["titles"]["sizes"] = "<b>sizes</b>"
    view["cause_labels"]["rewrite"] = "<i>rewrite</i>"
    page = dashboard_html.render_cache_causes(view)

    for injected in ("<img src=x", "<script>alert", "<b>sizes</b>", "<i>rewrite</i>"):
        assert injected not in page


def test_the_stylesheet_styles_the_new_card_parts_without_the_series_colors() -> None:
    style = dashboard_html_assets.STYLE
    for selector in (
        ".card-headline",
        ".meter-details",
        ".cause-bar",
        ".cause-legend",
        ".causes-attention",
    ):
        assert selector in style
    bar_rules = "".join(
        rule for rule in style.split("}") if ".cause-seg" in rule or ".cause-bar" in rule
    )
    assert "series-" not in bar_rules
