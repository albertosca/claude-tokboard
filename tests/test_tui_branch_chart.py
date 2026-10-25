"""The TUI trend chart's fourth mode: tokens per day, sliced by git branch."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from rich.text import Text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_tui_state import seeded_tui_home  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def _record(day: str, branch: str | None, tokens: int, index: int = 0) -> dict[str, Any]:
    return {
        "session_id": f"s-{day}-{index}",
        "project_path": "/Users/username/my-api",
        "timestamp_start": f"{day}T10:00:00Z",
        "git_branch": branch,
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def _branch_chart_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    records: list[dict[str, Any]],
    lang: str = "en",
) -> str:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = dict(tui.load_data(arguments, metrics_core_module=mr))
    data["daily_chart_records"] = records
    data["chart_records"] = records
    data["branch_slots"] = mr.assign_branch_slots(records)
    data["lang"] = lang

    async def run() -> str:
        app = tui.MetricsTUIApp(mr, data, arguments=arguments)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            chart = app.query_one(tui.TrendChart)
            while chart.mode != "branch":
                await pilot.press("t")
                await pilot.pause()
            return cast(Text, chart.render()).plain

    return asyncio.run(run())


def _days() -> list[dict[str, Any]]:
    return [
        _record("2026-01-01", "main", 300),
        _record("2026-01-01", "feature/example", 100, index=1),
        _record("2026-01-02", "main", 50),
        _record("2026-01-02", None, 25, index=1),
    ]


def test_each_branch_band_is_named_in_the_legend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = _branch_chart_text(monkeypatch, tmp_path, _days())

    assert "Tokens per day (by branch)" in text
    assert "main" in text
    assert "feature/example" in text
    assert "(no branch)" in text


def test_the_title_is_in_portuguese_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = _branch_chart_text(monkeypatch, tmp_path, _days(), lang="pt")

    assert "Tokens por dia (por branch)" in text
    assert "(sem branch)" in text


def test_branches_past_the_slots_are_drawn_as_one_others_band(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    records = [
        _record("2026-01-01", f"feature/example-{number}", 100 + number, index=number)
        for number in range(10)
    ] + [_record("2026-01-02", "main", 5)]

    text = _branch_chart_text(monkeypatch, tmp_path, records)

    assert "others" in text
    assert "main" not in text


def test_a_window_with_no_tokens_draws_an_empty_chart_without_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    records = [_record("2026-01-01", "main", 0), _record("2026-01-02", "main", 0)]

    text = _branch_chart_text(monkeypatch, tmp_path, records)

    assert "Tokens per day (by branch)" in text
