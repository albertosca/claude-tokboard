"""tools/make-demo-assets.py's stage_calls gives the README's screenshot a
weekly-limit meter with something in it.

Without it the throwaway HOME has no calls.jsonl, and the page prints the
empty state ("No calls stored yet") under the very panel the README's pitch
describes. tools/ is OUTSIDE the 100% coverage gate; this file exists for
correctness, and must never be added to pyproject.toml's addopts.
"""

import importlib.util
import json
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from metrics_core import compute_weekly_meter, read_calls, read_limit_hits

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "tools" / "make-demo-assets.py"
# A Monday just after the weekly reset, a Friday evening, and a moment right
# after the reset (the open window has almost no room for calls).
MOMENTS = (
    datetime(2026, 10, 26, 18, 45, tzinfo=timezone.utc),
    datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc),
    datetime(2026, 11, 2, 5, 10, tzinfo=timezone.utc),
)


@pytest.fixture
def tool() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("make_demo_assets_calls", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stage(tool: types.ModuleType, home: Path, now: datetime) -> tuple[Path, Path]:
    metrics = home / ".claude" / "metrics"
    metrics.mkdir(parents=True)
    tool.stage_calls(home, now)
    return metrics / "calls.jsonl", metrics / "limit-hits.jsonl"


@pytest.mark.parametrize("now", MOMENTS)
def test_the_meter_has_a_range_and_an_open_window(
    tool: types.ModuleType, tmp_path: Path, now: datetime
) -> None:
    calls_path, hits_path = _stage(tool, tmp_path / "home", now)

    meter = compute_weekly_meter(read_calls(calls_path), read_limit_hits(hits_path), now)

    assert meter["state"] == "hypothesis"
    assert meter["range"]["points"] == 3
    assert meter["current"] is not None
    assert meter["current"]["accumulated"] > 0


def test_every_call_is_in_the_past(tool: types.ModuleType, tmp_path: Path) -> None:
    now = MOMENTS[2]
    calls_path, _ = _stage(tool, tmp_path / "home", now)

    stamps = [datetime.fromisoformat(call["timestamp"]) for call in read_calls(calls_path)]

    assert stamps
    assert max(stamps) <= now - timedelta(minutes=1)


def test_the_meter_never_exceeds_the_fixtures_own_cache_creation(
    tool: types.ModuleType, tmp_path: Path
) -> None:
    # The quota line of the same page sums the fixture's sessions. A window
    # holding more cache creation than the whole history would make the
    # screenshot contradict itself.
    home = tmp_path / "home"
    now = MOMENTS[0]
    tool.seed_home(home)
    metrics = home / ".claude" / "metrics"
    sessions = [json.loads(line) for line in (metrics / "sessions.jsonl").read_text().splitlines()]
    history = sum(session.get("cache_creation_tokens", 0) for session in sessions)
    tool.stage_calls(home, now)

    created = sum(
        call["cache_5m"] + call["cache_1h"] for call in read_calls(metrics / "calls.jsonl")
    )

    assert 0 < created <= history


def test_staging_twice_gives_the_same_bytes(tool: types.ModuleType, tmp_path: Path) -> None:
    first = _stage(tool, tmp_path / "first", MOMENTS[0])
    second = _stage(tool, tmp_path / "second", MOMENTS[0])

    assert first[0].read_bytes() == second[0].read_bytes()
    assert first[1].read_bytes() == second[1].read_bytes()


def test_it_refuses_to_write_outside_the_throwaway_home(
    tool: types.ModuleType, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    (home / ".claude" / "metrics").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (home / ".claude" / "metrics" / "calls.jsonl").symlink_to(outside / "calls.jsonl")

    with pytest.raises(SystemExit, match="outside the throwaway HOME"):
        tool.stage_calls(home, MOMENTS[0])


def test_the_showcase_gives_sessions_branches_and_one_project_two(
    tool: types.ModuleType, tmp_path: Path
) -> None:
    # The README sells a per-branch panel, a branch filter and a "By branch"
    # chart; the shared fixture has no git_branch, so without this the branch
    # column is empty and the panel (2+ branches in one project) never shows.
    home = tmp_path / "home"
    tool.seed_home(home)
    tool.stage_showcase(home, MOMENTS[0])
    sessions = [
        json.loads(line)
        for line in (home / ".claude" / "metrics" / "sessions.jsonl").read_text().splitlines()
    ]

    branches_by_project: dict[str, set[str]] = {}
    for session in sessions:
        assert session["git_branch"]
        branches_by_project.setdefault(session["project_path"], set()).add(session["git_branch"])

    assert len({branch for names in branches_by_project.values() for branch in names}) >= 3
    assert any(len(names) >= 2 for names in branches_by_project.values())


def test_the_recording_cycles_through_all_four_chart_slicings(tool: types.ModuleType) -> None:
    # project -> token type -> model -> branch takes three presses of "t";
    # the README says four slicings, and the GIF must show them all.
    assert [keys for keys, _ in tool.BEATS].count(["t"]) == 3
