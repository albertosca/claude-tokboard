"""The fixture must be hermetic. A rendering test that passes because it
read the user's real data would be worse than no test - it would pass on
a machine with no data at all only by accident, and its numbers would
drift daily."""

import json
import re
from pathlib import Path

import pytest

FIXTURE_SESSION_IDS = {
    "long-1",
    "yest-1",
    "today-a",
    "today-b",
    "days4-1",
    "week-1",
    "days21-1",
    "old-1",
}

# Full paths, not bare names: the page prints the path in each group
# header's title attribute, and a bare "beta" would also match the word
# inside any unrelated prose the page happens to carry.
FIXTURE_PROJECTS = (
    "/projects/alpha",
    "/projects/beta",
    "/projects/gamma",
    "/projects/delta",
    "/projects/epsilon",
    "/projects/zeta",
    "/projects/eta",
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
    html = Path(dashboard_page.html_path).read_text(encoding="utf-8")
    for project in FIXTURE_PROJECTS:
        assert project in html, f"fixture project {project!r} missing from the page"


REAL_METRICS_DIRECTORY = Path("~/.claude/metrics").expanduser()
RENDERER_SOURCES = ("dashboard_html.py", "dashboard_html_assets.py")


def _word_pattern(name: str) -> re.Pattern[str]:
    """A real name only counts as LEAKED if it appears as a whole word, not
    merely as a substring - "app" sits inside the quota line's own English
    text ("Quota (approx.): ...", metrics_core's quota_line message,
    rendered on every page regardless of any project's name) with nothing
    at all coming from project data, and a raw substring check would flag
    it as a leak forever. Same idea as tools/make-demo-assets.py's own
    _word_pattern, which documents exactly this "approx." case."""
    return re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")


def real_project_names(metrics_directory: Path, renderer_source: str) -> set[str]:
    """The reader's own project names, read from their own metrics files.

    This used to be three names typed into this file. That worked, and it
    kept the author's real project names in a public repository - the one
    place the 2026-09 privacy pass could not scrub, because renaming them
    would leave the detector watching for strings that exist nowhere.
    Reading them at test time removes them from the repo AND makes the
    check stronger: every project on the machine instead of three.

    A name that also occurs in the renderer's own source AS A WHOLE WORD
    (fix round 1, M6: this used to be a raw substring check, which could
    wrongly DROP a real project by coincidence - a project named "in"
    sits inside "input", "using", and dozens of other words the renderer
    source genuinely contains, none of which mean the name is safe UI
    vocabulary) is dropped. The page can print such a word without any
    data reaching it ("metrics", "tmux"), so finding it proves nothing. A
    name that is NOT in the renderer's source can only reach the page
    through data - which is exactly the leak this is looking for.
    """
    names: set[str] = set()
    for name in ("sessions.jsonl", "subagents.jsonl"):
        path = metrics_directory / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                project = json.loads(line).get("project_path")
            except json.JSONDecodeError:
                continue
            if project:
                base = Path(str(project).rstrip("/")).name
                if base and not _word_pattern(base).search(renderer_source):
                    names.add(base)
    return names


def leaked_names(html: str, names: set[str]) -> list[str]:
    return sorted(name for name in names if name and _word_pattern(name).search(html))


def test_no_real_metrics_leak_into_the_page(dashboard_page):
    repo = Path(__file__).resolve().parent.parent
    source = "".join(
        (repo / source_name).read_text(encoding="utf-8") for source_name in RENDERER_SOURCES
    )
    names = real_project_names(REAL_METRICS_DIRECTORY, source)
    if not names:
        # CI, or any machine that has never logged a session: there is no
        # real data to leak, and passing here would claim a check that did
        # not happen. Skipped, visibly.
        pytest.skip(f"no real project names under {REAL_METRICS_DIRECTORY} - nothing to leak")
    html = Path(dashboard_page.html_path).read_text(encoding="utf-8")
    assert leaked_names(html, names) == [], "real projects leaked - HOME was not isolated"


def test_the_leak_detector_fires_on_a_leak():
    # The detector above passes whenever nothing leaks, so on its own it
    # cannot tell a clean page from a check that never looks. This feeds it
    # a page that DOES carry a real name and requires it to say so.
    names = {"some-real-project", "another-one"}
    assert leaked_names("<html>alpha some-real-project</html>", names) == ["some-real-project"]
    assert leaked_names("<html>alpha beta</html>", names) == []


def test_the_leak_detector_matches_whole_words_not_substrings():
    # "app" sits inside "approx." - the quota line's own English text
    # (metrics_core's quota_line message, rendered on every page regardless
    # of any project's name: "Quota (approx.): {input} in ...") - with
    # nothing at all coming from project data. A raw substring check would
    # flag "app" as a leak on every single page forever, exactly like
    # tools/make-demo-assets.py's own _word_pattern docstring warns about
    # for "approx." specifically. Matching by whole word instead - the same
    # idea as that helper - does not.
    assert leaked_names("<html>Quota (approx.): 1 in</html>", {"app"}) == []
    # "app" as an actual whole word must still be caught.
    assert leaked_names("<html>the app is here</html>", {"app"}) == ["app"]


def test_a_word_the_renderer_itself_prints_is_not_taken_as_a_leak(tmp_path):
    (tmp_path / "sessions.jsonl").write_text(
        json.dumps({"project_path": "/x/metrics"})
        + "\n"
        + json.dumps({"project_path": "/x/my-private-app"})
        + "\n"
        + "not json\n"
    )
    assert real_project_names(tmp_path, "the renderer prints metrics") == {"my-private-app"}


def test_a_substring_match_in_the_renderer_source_does_not_wrongly_drop_a_real_name(tmp_path):
    # M6: "in" sits inside "input" - a raw substring check would have
    # wrongly treated a real project named "in" as safe UI vocabulary and
    # dropped it from the leak check entirely, when nothing about "input"
    # says anything at all about a project by that name.
    (tmp_path / "sessions.jsonl").write_text(json.dumps({"project_path": "/x/in"}) + "\n")
    assert real_project_names(tmp_path, "reads the input file") == {"in"}


def test_output_landed_inside_the_temporary_home(dashboard_page):
    assert dashboard_page.html_path.startswith(dashboard_page.home)
    assert not dashboard_page.html_path.startswith(str(Path("~/.claude").expanduser()))


def test_fixture_records_are_where_the_builder_says(dashboard_page):
    path = Path(dashboard_page.home) / ".claude/metrics/sessions.jsonl"
    with path.open() as sessions_file:
        ids = {json.loads(line)["session_id"] for line in sessions_file if line.strip()}
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
    for narrow, wide in (
        ("ontem", "15-dias"),
        ("desde-segunda", "15-dias"),
        ("15-dias", "mes"),
        ("mes", "tudo"),
    ):
        assert exp[narrow]["session_ids"] <= exp[wide]["session_ids"], (
            f"{narrow} not contained in {wide}"
        )
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
    by_period = {
        period: frozenset(exp[period]["session_ids"])
        for period in ("ontem", "desde-segunda", "15-dias", "mes", "tudo")
    }
    for period, ids in by_period.items():
        assert ids, (
            f"{period} holds no session at all - every non-vacuity guard in the "
            "harness is unsatisfiable under it"
        )
    seen: dict[frozenset[str], str] = {}
    for period, ids in by_period.items():
        clash = seen.get(ids)
        assert clash is None, (
            f"{period} and {clash} hold the identical set {sorted(ids)} - a window "
            "widened from one to the other would go undetected"
        )
        seen[ids] = period
