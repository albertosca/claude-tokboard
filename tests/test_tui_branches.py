"""The branches panel on the TUI, and its agreement with the HTML page."""

from __future__ import annotations

import asyncio
import html
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from rich.text import Text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_tui_state import seeded_tui_home  # noqa: E402

import dashboard_html  # noqa: E402
import dashboard_tui as tui  # noqa: E402

PROJECT = "/Users/username/my-api"


def _view(lang: str = "en") -> dict[str, Any]:
    breakdown = [
        {
            "group": PROJECT,
            "total": 100,
            "branches": [
                {"branch": "main", "sessions": 3, "total": 75},
                {"branch": "HEAD", "sessions": 1, "total": 25},
            ],
        }
    ]
    return mr.describe_branches(breakdown, lang)


def _panel_text(view: dict[str, Any]) -> str:
    rendered = tui.BranchesPanel(view).render()
    assert isinstance(rendered, Text)
    return rendered.plain


def test_the_panel_prints_the_same_numbers_the_html_page_does() -> None:
    view = _view()

    text = _panel_text(view)
    page = html.unescape(dashboard_html.render_branches(view, "tudo"))

    shared = [view["heading"], view["note"], view["projects"][0]["project"]]
    for row in view["projects"][0]["rows"]:
        shared += [row["branch"], row["sessions"], row["tokens"], row["share"]]
    for fragment in shared:
        assert fragment in text, fragment
        assert fragment in page, fragment


def test_the_branch_column_is_as_wide_as_the_longest_name() -> None:
    view = _view()
    view["projects"][0]["rows"][0]["branch"] = "feature/" + "w" * 40

    lines = _panel_text(view).splitlines()
    rows = [line for line in lines if "75%" in line or "25%" in line]

    assert rows[0].index("3") == rows[1].index("1")


def _session(project: str, branch: str, start: str) -> dict[str, Any]:
    return {
        "session_id": f"{branch}-{start}",
        "project_path": project,
        "git_branch": branch,
        "timestamp_start": start,
        "timestamp_end": start,
        "input_tokens": 10,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def test_the_period_narrows_the_branches_and_all_of_history_keeps_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    base = tui.load_base_data(arguments, metrics_core_module=mr)
    now = datetime.now().astimezone()
    old = (now - timedelta(days=365)).isoformat()
    base["records"] = [_session(PROJECT, "main", old), _session(PROJECT, "dev", old)]
    base["chart_records"] = []
    base["daily_chart_records"] = []

    everything = tui.apply_period(base, "tudo", mr, now=now)
    yesterday = tui.apply_period(base, "ontem", mr, now=now)

    assert everything["branches"]["empty"] is False
    assert yesterday["branches"]["empty"] is True


def test_the_app_shows_the_panel_on_the_overview_tab_and_omits_it_when_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = tui.load_data(arguments, metrics_core_module=mr)

    async def drive(view: dict[str, Any]) -> int:
        shown = dict(data)
        shown["branches"] = view
        app = tui.MetricsTUIApp(mr, shown, arguments=arguments)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            return len(app.query(tui.BranchesPanel))

    assert asyncio.run(drive(_view())) == 1
    assert asyncio.run(drive(mr.describe_branches([], "en"))) == 0
