"""The branch filter of the session tree on the TUI: the "b" key."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Tree

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_tui_branches import _session  # noqa: E402
from test_tui_state import seeded_tui_home  # noqa: E402

import dashboard_tui as tui  # noqa: E402

PROJECT = "/Users/username/my-api"
QUIET_PROJECT = "/Users/username/blog-site"
WHEN = "2026-08-10T10:00:00Z"


def _records() -> list[dict[str, Any]]:
    named = [("main-one", "main"), ("main-two", "main"), ("feature-one", "feature/example")]
    records = [_session(PROJECT, branch, WHEN) | {"session_id": name} for name, branch in named]
    records.append(_session(QUIET_PROJECT, "main", WHEN) | {"session_id": "quiet-one"})
    return records


def _drive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, presses: list[str], lang: str = "en"
) -> dict[str, Any]:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = dict(tui.load_data(arguments, metrics_core_module=mr))
    data["records"] = _records()
    data["subagents_by_session"] = {}
    data["lang"] = lang

    async def run() -> dict[str, Any]:
        app = tui.MetricsTUIApp(mr, data, arguments=arguments)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tree = app.query_one(Tree)
            groups = {str(node.label).split(" ")[0]: node for node in tree.root.children}
            outcome: dict[str, Any] = {"labels": [], "leaves": [], "notes": []}
            for target in presses:
                tree.move_cursor(groups[target.split(":")[0]])
                await pilot.press("b")
                await pilot.pause()
                node = groups[target.split(":")[0]]
                outcome["leaves"].append(
                    [(leaf.data or {})["session_id"] for leaf in node.children]
                )
                outcome["labels"].append(str(node.label))
                outcome["notes"] = [note.message for note in app._notifications]
            return outcome

    return asyncio.run(run())


def test_b_cycles_a_group_through_its_branches_and_back_to_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcome = _drive(monkeypatch, tmp_path, ["my-api"] * 3)

    first, second, third = outcome["leaves"]
    assert sorted(first) == ["main-one", "main-two"]
    assert second == ["feature-one"]
    assert sorted(third) == ["feature-one", "main-one", "main-two"]


def test_the_group_label_names_the_branch_and_counts_what_is_shown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcome = _drive(monkeypatch, tmp_path, ["my-api"] * 3)

    assert outcome["labels"][0].endswith("[main]")
    assert outcome["labels"][1].endswith("[feature/example]")
    assert "2 sessions" in outcome["labels"][0]
    assert "feature/example" in outcome["labels"][1]
    assert "3 sessions" in outcome["labels"][2]
    assert "feature/example" not in outcome["labels"][2]


def test_a_group_with_one_branch_is_left_alone_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcome = _drive(monkeypatch, tmp_path, ["blog-site"])

    assert outcome["leaves"] == [["quiet-one"]]
    assert any("one branch" in note for note in outcome["notes"])


def test_the_prompt_is_in_portuguese_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outcome = _drive(monkeypatch, tmp_path, ["blog-site"], lang="pt")

    assert any("um branch" in note for note in outcome["notes"])


def test_b_on_a_session_leaf_narrows_the_leafs_own_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = dict(tui.load_data(arguments, metrics_core_module=mr))
    data["records"] = _records()
    data["subagents_by_session"] = {}

    async def run() -> list[str]:
        app = tui.MetricsTUIApp(mr, data, arguments=arguments)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            tree = app.query_one(Tree)
            group = next(node for node in tree.root.children if "my-api" in str(node.label))
            group.expand()
            await pilot.pause()
            tree.move_cursor(group.children[0])
            await pilot.press("b")
            await pilot.pause()
            return [(leaf.data or {})["session_id"] for leaf in group.children]

    assert len(asyncio.run(run())) < 3


def test_b_outside_the_sessions_tab_does_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = dict(tui.load_data(arguments, metrics_core_module=mr))
    data["records"] = _records()
    data["subagents_by_session"] = {}

    async def run() -> int:
        app = tui.MetricsTUIApp(mr, data, arguments=arguments)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            assert app.active_tab_id != "tab-sessions"
            before = len(app._notifications)
            await pilot.press("b")
            await pilot.pause()
            return len(app._notifications) - before

    assert asyncio.run(run()) == 0


def test_b_with_the_cursor_on_no_group_does_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seeded_tui_home(monkeypatch, tmp_path)
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = dict(tui.load_data(arguments, metrics_core_module=mr))
    data["records"] = []
    data["subagents_by_session"] = {}

    async def run() -> int:
        app = tui.MetricsTUIApp(mr, data, arguments=arguments)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("b")
            await pilot.pause()
            return len(app._notifications)

    assert asyncio.run(run()) == 0
