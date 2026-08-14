"""The fixture must be hermetic. A rendering test that passes because it
read the user's real data would be worse than no test - it would pass on
a machine with no data at all only by accident, and its numbers would
drift daily."""
import json
import os


def test_dashboard_is_generated_from_the_fixture(dashboard_page):
    html = open(dashboard_page.html_path, encoding="utf-8").read()
    for project in ("alpha", "beta", "gamma"):
        assert project in html, f"fixture project {project!r} missing from the page"


def test_no_real_metrics_leak_into_the_page(dashboard_page):
    # If the real ~/.claude/metrics were read, these would appear. They are
    # the user's actual project names, so their absence is the proof.
    html = open(dashboard_page.html_path, encoding="utf-8").read()
    for leaked in ("billing-report", "editor-plugin", "claude-metrics-dashboard"):
        assert leaked not in html, f"real project {leaked!r} leaked - HOME was not isolated"


def test_output_landed_inside_the_temporary_home(dashboard_page):
    assert dashboard_page.html_path.startswith(dashboard_page.home)
    assert not dashboard_page.html_path.startswith(os.path.expanduser("~/.claude"))


def test_fixture_records_are_where_the_builder_says(dashboard_page):
    path = os.path.join(dashboard_page.home, ".claude/metrics/sessions.jsonl")
    ids = {json.loads(line)["session_id"] for line in open(path) if line.strip()}
    assert ids == {"long-1", "yest-1", "old-1", "week-1"}


def test_expected_periods_are_nested_as_the_windows_are(dashboard_page):
    exp = dashboard_page.expected
    # ontem is the narrowest, tudo the widest, and each contains the last.
    for narrow, wide in (("ontem", "desde-segunda"), ("desde-segunda", "15-dias"), ("15-dias", "mes"), ("mes", "tudo")):
        assert exp[narrow]["session_ids"] <= exp[wide]["session_ids"], f"{narrow} not contained in {wide}"
    assert exp["tudo"]["session_ids"] == {"long-1", "yest-1", "old-1", "week-1"}
    assert "old-1" not in exp["mes"]["session_ids"]
    assert "long-1" in exp["ontem"]["session_ids"], "the span-overlap case must survive into ontem"
