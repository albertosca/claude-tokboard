import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))

import dashboard_html  # noqa: E402


def test_render_backfill_note_wraps_the_description_in_a_span(monkeypatch):
    # Decoupled from load_backfill_state/describe_backfill_state's own
    # logic on purpose - those are already covered directly in
    # tests/test_backfill.py (Task 1). This test only proves
    # render_backfill_note() wires them up and escapes the result.
    monkeypatch.setattr(dashboard_html.mr, "describe_backfill_state", lambda state: "a <b> note")
    assert dashboard_html.render_backfill_note() == '<span class="backfill-note">a &lt;b&gt; note</span>'


def test_render_period_filter_includes_the_backfill_note(monkeypatch):
    monkeypatch.setattr(dashboard_html, "render_backfill_note", lambda: "<span>NOTE</span>")
    assert "<span>NOTE</span>" in dashboard_html.render_period_filter()
