"""The fixture must be hermetic. A rendering test that passes because it
read the user's real data would be worse than no test - it would pass on
a machine with no data at all only by accident, and its numbers would
drift daily."""
import json
import os


FIXTURE_SESSION_IDS = {
    "long-1", "yest-1", "today-a", "today-b", "days4-1", "week-1", "days21-1", "old-1",
}

# Full paths, not bare names: the page prints the path in each group
# header's title attribute, and a bare "beta" would also match the word
# inside any unrelated prose the page happens to carry.
FIXTURE_PROJECTS = (
    "/projects/alpha", "/projects/beta", "/projects/gamma", "/projects/delta",
    "/projects/epsilon", "/projects/zeta", "/projects/eta",
)

# Exactly who belongs where. Four of the five windows are anchored to
# today, not to the calendar, so their membership is the same on every
# weekday and can simply be written down. "desde-segunda" is the one that
# moves - it gets bounds instead, below.
EXPECTED_MEMBERSHIP = {
    "ontem": {"long-1", "yest-1"},
    "15-dias": {"long-1", "yest-1", "today-a", "today-b", "days4-1", "week-1"},
    "mes": {"long-1", "yest-1", "today-a", "today-b", "days4-1", "week-1", "days21-1"},
    "tudo": FIXTURE_SESSION_IDS,
}


def test_dashboard_is_generated_from_the_fixture(dashboard_page):
    html = open(dashboard_page.html_path, encoding="utf-8").read()
    for project in FIXTURE_PROJECTS:
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
    assert ids == FIXTURE_SESSION_IDS


def test_expected_periods_are_nested_as_the_windows_are(dashboard_page):
    exp = dashboard_page.expected
    # Containment, in window order - but "ontem" is deliberately NOT
    # compared against "desde-segunda". desde-segunda is calendar-anchored,
    # so on a MONDAY its window is today alone while ontem is Sunday: two
    # DISJOINT windows. Asserting ontem <= desde-segunda was false by
    # definition one day in seven, and no fixture could have fixed it.
    # ontem's real container is 15-dias, which always reaches back
    # fourteen days whatever the weekday.
    for narrow, wide in (("ontem", "15-dias"), ("desde-segunda", "15-dias"), ("15-dias", "mes"), ("mes", "tudo")):
        assert exp[narrow]["session_ids"] <= exp[wide]["session_ids"], f"{narrow} not contained in {wide}"
    assert exp["tudo"]["session_ids"] == FIXTURE_SESSION_IDS
    assert "old-1" not in exp["mes"]["session_ids"]
    assert "long-1" in exp["ontem"]["session_ids"], "the span-overlap case must survive into ontem"


def test_each_period_holds_exactly_the_sessions_it_should(dashboard_page):
    """Pins the membership itself, not just the relations between periods.

    Every other assertion in this suite - the harness's fifteen readings
    included - is computed FROM these windows, so a window that silently
    changes width moves the expectation and the page together and the
    whole suite agrees with itself about the wrong answer. Widening
    "ontem" to a week and letting "15-dias" reach back 25 days both did
    exactly that, and both went unnoticed. Written down, they cannot."""
    exp = dashboard_page.expected
    for period, ids in EXPECTED_MEMBERSHIP.items():
        assert exp[period]["session_ids"] == ids, (
            f"{period} holds {sorted(exp[period]['session_ids'])}, not {sorted(ids)} - "
            "either the fixture moved or the window did"
        )
    # desde-segunda spans one day on a Monday and seven on a Sunday, so
    # only its bounds are weekday-independent: today is always in, and
    # nothing older than a week ever can be.
    since_monday = exp["desde-segunda"]["session_ids"]
    assert {"today-a", "today-b"} <= since_monday, (
        f"desde-segunda lost today's own sessions ({sorted(since_monday)}) - on a Monday "
        "that is the entire period, and the suite would go red every Monday"
    )
    assert not since_monday & {"week-1", "days21-1", "old-1"}, (
        f"desde-segunda reached past a week: {sorted(since_monday)}"
    )


def test_every_period_holds_a_different_set_of_sessions(dashboard_page):
    """The harness loops over five periods; the fixture has to be able to
    tell them apart, or twelve of those fifteen readings are decoration. It
    used to hold three distinguishable outcomes for five periods, and two
    window-widening injections walked straight through the suite.

    This only proves it for the weekday the suite happens to run on. The
    per-weekday proof is in the fix report (every weekday simulated by
    forcing periods.resolve_period's now.weekday() term)."""
    exp = dashboard_page.expected
    by_period = {p: frozenset(exp[p]["session_ids"]) for p in
                 ("ontem", "desde-segunda", "15-dias", "mes", "tudo")}
    for period, ids in by_period.items():
        assert ids, (
            f"{period} holds no session at all - every non-vacuity guard in the "
            "harness is unsatisfiable under it"
        )
    seen = {}
    for period, ids in by_period.items():
        clash = seen.get(ids)
        assert clash is None, (
            f"{period} and {clash} hold the identical set {sorted(ids)} - a window "
            "widened from one to the other would go undetected"
        )
        seen[ids] = period
