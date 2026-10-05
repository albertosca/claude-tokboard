"""The TUI's marker for an output figure that is only a floor.

Rows whose transcript held messages that never got a final usage entry
carry incomplete_usage_messages (hooks, 2026-09-25); their output - and so
their total - is a minimum. The tree row gets a "≥" on its total, the
detail panel a "≥" on each flagged subagent's output and one note line
saying why. The HTML half is in tests/test_dashboard_html_rows.py; the
end-to-end click, in both languages, in tests/test_language_leak.py.
"""

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def _record(**overrides: Any) -> dict[str, Any]:
    record = {
        "project_path": "/projects/x",
        "session_id": "sess-1",
        "task_label": "a task",
        "timestamp_start": "2026-01-01T09:00:00",
        "duration_seconds": 42,
        "input_tokens": 5,
        "output_tokens": 7,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    record.update(overrides)
    return record


def _row(record: dict[str, Any]) -> str:
    tree = tui.build_session_tree([record], {}, mr, lang="en")
    return str(tree.root.children[0].children[0].label)


def test_tree_row_prefixes_a_floor_total_and_keeps_its_column() -> None:
    plain = _row(_record())
    flagged = _row(_record(incomplete_usage_messages=2))
    tokens = mr.format_tokens(12)
    assert "≥" not in plain
    assert flagged[58 : 58 + 8] == f"{'≥' + tokens:>8}"
    # Same width: the marker takes a pad space, it does not shift the row.
    assert len(flagged) == len(plain)


def _panel_lines(subagents: list[dict[str, Any]], lang: str, **keyword_arguments: Any) -> list[str]:
    panel = tui.SessionDetailPanel(mr, lang=lang)
    panel.show_session("sess", subagents, **keyword_arguments)
    return str(panel._body.render()).split("\n")


def _subagent(**overrides: Any) -> dict[str, Any]:
    subagent = {
        "agent_type": "general-purpose",
        "agent_label": "a label",
        "input_tokens": 5,
        "output_tokens": 7,
        "tool_call_count": 3,
        "duration_seconds": 12,
    }
    subagent.update(overrides)
    return subagent


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_detail_panel_marks_the_flagged_subagent_and_explains_once(lang: str) -> None:
    lines = _panel_lines(
        [_subagent(incomplete_usage_messages=2), _subagent(agent_label="other")],
        lang,
        incomplete_usage=5,
    )
    flagged, plain = (line for line in lines if "general-purpose" in line)
    assert f"{mr.format_tokens(5)}/≥{mr.format_tokens(7)}" in flagged
    assert "≥" not in plain
    # The note counts the session's whole folded total: its own messages
    # plus every subagent's, which is what its row's "≥" stands for.
    assert lines[-1] == mr.tn("floor_note", 5, lang)


def test_detail_panel_without_any_flag_adds_no_note() -> None:
    lines = _panel_lines([_subagent()], "en")
    assert "≥" not in "\n".join(lines)
    assert "general-purpose" in lines[-1]


def test_detail_panel_notes_a_session_flagged_by_its_own_messages_alone() -> None:
    lines = _panel_lines([], "en", incomplete_usage=1)
    assert lines[-1] == mr.tn("floor_note", 1, "en")


def test_detail_panel_floor_note_thousands_formats_the_message_count() -> None:
    # A long session's floor count can reach into the thousands - the note
    # must format it the way every other count on this dashboard is
    # formatted (format_tokens), not interpolate the raw int.
    lines = _panel_lines([], "en", incomplete_usage=2500)
    note = lines[-1]
    assert mr.format_tokens(2500) in note
    assert "2500" not in note


# --- durations read like the HTML dashboard's (2026-10-05 review, item 5) ---


def test_tree_row_formats_a_long_duration_in_hours_and_minutes() -> None:
    # The review's real row: "333874s" overflowed its column and read as noise.
    row = _row(_record(duration_seconds=333874))
    assert row.rstrip().endswith("92h44min")
    assert "333874" not in row


def test_tree_row_keeps_its_width_for_the_longest_formatted_duration() -> None:
    short = _row(_record(duration_seconds=5))
    long = _row(_record(duration_seconds=999 * 3600 + 59 * 60))
    assert long.rstrip().endswith("999h59min")
    assert len(long) == len(short)


def test_detail_panel_formats_a_subagent_duration_in_minutes() -> None:
    text = "\n".join(_panel_lines([_subagent(duration_seconds=75)], "en"))
    assert "1min" in text
    assert "75s" not in text
