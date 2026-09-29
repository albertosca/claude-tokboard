import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import metrics_core.backfill as backfill_module
import pytest
from state_shapes import SHAPE_NAMES, restore_permissions, write_unreadable_state

import dashboard_html


def test_render_backfill_note_wraps_the_description_in_a_span(monkeypatch):
    # Decoupled from load_backfill_state/describe_backfill_state's own
    # logic on purpose - those are already covered directly in
    # tests/test_backfill.py (Task 1). This test only proves
    # render_backfill_note() wires them up and escapes the result.
    monkeypatch.setattr(
        dashboard_html.metrics_core_module,  # type: ignore[attr-defined]  # metrics_core_module is dashboard_html's internal metrics_core import, not re-exported under strict's implicit-reexport check
        "describe_backfill_state",
        lambda state, lang: "a <b> note",
    )
    assert (
        dashboard_html.render_backfill_note(lang="pt")
        == '<span class="backfill-note">a &lt;b&gt; note</span>'
    )


def test_render_period_filter_includes_the_backfill_note(monkeypatch):
    monkeypatch.setattr(dashboard_html, "render_backfill_note", lambda lang: "<span>NOTE</span>")
    assert "<span>NOTE</span>" in dashboard_html.render_period_filter(lang="pt")


@pytest.mark.parametrize("shape", SHAPE_NAMES)
def test_the_note_renders_over_every_unreadable_state(monkeypatch, tmp_path, shape):
    # The dashboards read the state leniently: whatever the file holds,
    # the page renders a note rather than raising.
    state = tmp_path / "backfill_state.json"
    write_unreadable_state(state, shape)
    monkeypatch.setattr(backfill_module, "BACKFILL_STATE_FILE", str(state))
    try:
        for lang in ("en", "pt"):
            note = dashboard_html.render_backfill_note(lang=lang)
            assert note.startswith('<span class="backfill-note">'), note
            assert len(note) > len('<span class="backfill-note"></span>')
    finally:
        restore_permissions(state)
