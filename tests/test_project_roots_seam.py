"""The declared project roots have to reach BOTH record streams.

normalize_project_paths is unit-tested in test_records.py; what this file
pins is that the two dashboards actually run their records through it -
including the subagent stream, which never went through it at all before
2026-09-21. That omission is what put literal subagent working directories
in the legend ("a legenda por vezes ta pegando nomes de subagents"), and no
test could have caught it, because the committed fixture's subagents.jsonl
is empty on purpose.

Driven through dashboard_tui.load_data rather than a rendered widget: the
seam is where the records are loaded, and load_data is the one function
both the chart and every legend read their records from.
"""

import json
import sys
from pathlib import Path
from typing import Any

import metrics_core as mr
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tui_state import isolate_tui_from_the_real_filesystem

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import dashboard_tui as tui

DEEP_SESSION = "/home/dev/code/my-api/docs/vendor"
DEEP_SUBAGENT = "/home/dev/code/blog-site/content/drafts"
PROJECT_ROOT = "/home/dev/code"


def _session(session_id: str, project: str, tokens: int) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "project_path": project,
        "timestamp_start": "2026-08-01T10:00:00",
        "timestamp_end": "2026-08-01T11:00:00",
        "input_tokens": tokens,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def _subagent(agent_id: str, project: str, tokens: int) -> dict[str, Any]:
    record = _session("parent-1", project, tokens)
    record["agent_id"] = agent_id
    return record


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    isolate_tui_from_the_real_filesystem(monkeypatch, tmp_path)
    metrics = tmp_path / ".claude" / "metrics"
    (metrics / "sessions.jsonl").write_text(json.dumps(_session("s-1", DEEP_SESSION, 100)) + "\n")
    (metrics / "subagents.jsonl").write_text(json.dumps(_subagent("a-1", DEEP_SUBAGENT, 40)) + "\n")
    return metrics


def _labels(data: Any) -> set[str]:
    """Every project name the chart's own records would put in a legend."""
    return {
        mr.project_display_label(mr.color_group_key(r.get("project_path")), lang="pt")
        for r in data["daily_chart_records"]
    }


def _load() -> Any:
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    return tui.load_data(args, mr=mr)


def test_without_a_config_both_streams_keep_their_raw_subdirectory_names(seeded: Path) -> None:
    # The baseline this test exists to change: both names are directories
    # nobody would call a project.
    assert _labels(_load()) == {"vendor", "drafts"}


def test_a_declared_root_renames_the_session_stream_and_the_subagent_stream(
    seeded: Path,
) -> None:
    (seeded / "config.json").write_text(json.dumps({"project_roots": [PROJECT_ROOT]}))
    assert _labels(_load()) == {"my-api", "blog-site"}
