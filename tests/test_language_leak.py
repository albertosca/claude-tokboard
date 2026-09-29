"""No string of the other language reaches either interface.

This is the completeness oracle for the translation: the inventory of
strings is taken by rendering, not by grepping, because a grep misses
f-string fragments and words too short to look foreign ("aba"). A marker
only counts if it cannot occur legitimately in the other language: a
Portuguese value that also appears, as a whole word, inside some English
value ("data" is in "data before...") is not evidence of a leak.
"""

import asyncio
import html as html_lib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import metrics_core as mr
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

PT_DIACRITICS = re.compile(r"[ãõçáéíóúâêôà]")
PLACEHOLDER = re.compile(r"\{[^}]*\}")


def _words_in(value: str, others: list[str]) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
    return any(pattern.search(other) for other in others)


def _template_fragments(value: str) -> list[str]:
    """The fixed text around a templated value's `{placeholder}`s, each
    stripped of the punctuation/whitespace that glues it to a placeholder
    ("Subagentes despachados ({n})" -> ["Subagentes despachados", ""])."""
    return [re.sub(r"^[^\w]+|[^\w]+$", "", part) for part in PLACEHOLDER.split(value)]


def _markers(lang: str) -> list[str]:
    """Catalog values that belong ONLY to the other language.

    A templated value ("Subagentes despachados ({n})") is split on its
    `{placeholder}`s first, so the fixed-text fragments around the
    placeholder are still checked - skipping the whole value here used to
    let a Portuguese fragment ride, untranslated and unnoticed, inside an
    otherwise-English render.
    """
    other = "en" if lang == "pt" else "pt"
    mine = [texts[lang] for texts in mr.MESSAGES.values()]
    found = []
    for texts in mr.MESSAGES.values():
        value = texts[other].strip()
        mine_value = texts[lang].strip()
        if value == mine_value:
            continue
        if "{" not in value:
            if not _words_in(value, mine):
                found.append(value)
            continue
        mine_fragments = _template_fragments(mine_value)
        for fragment_index, fragment in enumerate(_template_fragments(value)):
            if not fragment:
                continue
            if fragment_index < len(mine_fragments) and fragment == mine_fragments[fragment_index]:
                continue
            if not _words_in(fragment, mine):
                found.append(fragment)
    return found


def language_leaks(text: str, lang: str) -> list[str]:
    hits = [
        marker for marker in _markers(lang) if re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", text)
    ]
    if lang == "en":
        hits += sorted(set(PT_DIACRITICS.findall(text)))
    return hits


def _visible_html_text(page: str) -> str:
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", page))


def fixture_page_html(home: Path, lang: str) -> str:
    """The real dashboard page, generated end to end by `dashboard_html.py
    --html` from the committed fixture, in `lang`, with the machine locale
    pinned to pt_BR - so an English page proves the config wins."""
    # Idempotent on purpose: this is called once per language, per test, and
    # an unconditional sys.path[:0] = [...] grew sys.path by two entries on
    # every single call - harmless in effect (both directories were already
    # importable), but unbounded across the many calls this file makes.
    for entry in (str(REPO / "tests"), str(REPO / "lib")):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    from datetime import datetime

    from fixtures.build_fixture import build_fixture

    build_fixture(str(home), datetime.now().astimezone())
    metrics = home / ".claude" / "metrics"
    (metrics / "config.json").write_text(json.dumps({"language": lang}))
    environment = dict(os.environ, HOME=str(home), PYTHONPATH=str(REPO / "lib"),
               LC_ALL="pt_BR.UTF-8", LC_MESSAGES="", LANG="")  # fmt: skip
    subprocess.run(
        [sys.executable, str(REPO / "dashboard_html.py"), "--html", "--label-budget", "0", "--all"],
        env=environment, cwd=str(REPO), check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    return (metrics / "dashboard.html").read_text()


def _render_html(home: Path, lang: str) -> str:
    return _visible_html_text(fixture_page_html(home, lang))


def test_fixture_page_html_does_not_grow_sys_path_on_repeated_calls(tmp_path: Path) -> None:
    fixture_page_html(tmp_path, "en")  # a first call, to seed both entries
    before = list(sys.path)
    fixture_page_html(tmp_path, "en")
    fixture_page_html(tmp_path, "pt")
    assert sys.path == before, "sys.path grew across repeated fixture_page_html calls"


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_html_dashboard_speaks_only_its_own_language(tmp_path: Path, lang: str) -> None:
    # The machine locale is pinned to pt_BR on purpose: the English page
    # must come out English because the CONFIG says so (Review Focus 1).
    rendered = _render_html(tmp_path, lang)
    # A positive assertion first (addition B): an empty page, or one that
    # never rendered the group headers at all, would pass the leak check
    # below for the wrong reason - it has to actually say something in
    # its own language before "says nothing foreign" means anything.
    assert ("Sessões" if lang == "pt" else "Sessions") in rendered
    assert language_leaks(rendered, lang) == []


def test_the_detector_fires_on_an_injected_leak() -> None:
    # The canary: without it, an empty marker list would pass everything.
    assert _markers("en"), "no Portuguese markers - the detector is blind"
    assert "Nenhum subagente nesta sessão." in language_leaks(
        "Sessions ... Nenhum subagente nesta sessão. ...", "en"
    )
    assert "Sessions" in language_leaks("Sessões ... Sessions", "pt")
    # Templated catalog values ("Subagentes despachados ({n})", the
    # "partial" suffix) used to be skipped outright because they contain
    # "{" - so a Portuguese leak riding inside one of them passed silently.
    assert language_leaks("Subagentes despachados (3)", "en")
    assert language_leaks("beta (parcial, 58%)", "en")


def _string_leaves(value: object) -> list[str]:
    """Every string anywhere inside a JSON-shaped value - a dict's KEYS as
    well as its values (fix round 1, M5: tokens_by_model's own keys are
    model names, e.g. "claude-opus-5", and are rendered - a dict-values-only
    walk silently skipped them), a list's items, recursively - so this
    keeps working unchanged the day tests/fixtures/build_fixture.py grows a
    new field (an agent type, a task label, a branch name, a tag), not
    just today's project_path."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [
            leaf
            for entry_key, entry_value in value.items()
            for leaf in (*_string_leaves(entry_key), *_string_leaves(entry_value))
        ]
    if isinstance(value, list):
        return [leaf for item in value for leaf in _string_leaves(item)]
    return []


def test_string_leaves_walks_dict_keys_as_well_as_values() -> None:
    # M5: tokens_by_model's own keys are model names and ARE rendered
    # (dashboard_html.py's model legend, dashboard_tui.py's own chart mode)
    # - a values-only walk would silently miss a leak riding in one.
    assert _string_leaves({"zero-duration-agent": 1}) == ["zero-duration-agent"]


# Fix round 1, M5: shared with _seed_one_subagent_under_week_1 below - one
# subagent dict, not two copies to drift out of sync - so THIS test also
# covers the leak test's own seeded subagent data, not just what
# build_fixture.py itself writes.
_SEEDED_SUBAGENT: dict[str, Any] = {
    "session_id": "week-1",
    "agent_id": "sub-a",
    "agent_type": "general-purpose",
    "input_tokens": 10,
    "output_tokens": 20,
    "cache_read_tokens": 1,
    "cache_creation_tokens": 1,
    "tool_call_count": 3,
    "duration_seconds": 12.5,
    # Flagged, so the drive's click into week-1 also reaches the output
    # floor note (the session is flagged through the fold).
    "incomplete_usage_messages": 2,
}


def test_fixture_user_data_is_language_neutral(tmp_path: Path) -> None:
    """#9: the leak detector cannot tell user data from UI text - it only
    knows a word is foreign because it belongs to the OTHER language's
    catalog. A fixture identifier that happens to share a word with that
    catalog (tests/test_tui_clicks.py's "zero-duration-agent" collides with
    col.duration's English value "duration") would register as a false
    leak the moment it reached a rendered page or screen. See
    tests/fixtures/build_fixture.py's own docstring for the rule this
    enforces; this is the enforcement.

    Covers build_fixture.py's own records AND this file's own
    _SEEDED_SUBAGENT (fix round 1, M5) - the latter is exactly the shape of
    data that already collided once (see _seed_one_subagent_under_week_1's
    docstring for the "zero-duration-agent" incident this would have
    caught).
    """
    from datetime import datetime

    from fixtures.build_fixture import build_fixture

    built = build_fixture(str(tmp_path), datetime.now().astimezone())
    strings = sorted(
        {leaf for record in built["records"] for leaf in _string_leaves(record)}
        | set(_string_leaves(_SEEDED_SUBAGENT))
    )
    assert strings, "collected no strings at all - the fixture's own shape changed"

    offenders = {
        string_value: {
            "en": language_leaks(string_value, "en"),
            "pt": language_leaks(string_value, "pt"),
        }
        for string_value in strings
        if language_leaks(string_value, "en") or language_leaks(string_value, "pt")
    }
    assert not offenders, (
        f"fixture user data collides with metrics_core's own catalog: {offenders} - "
        "see tests/fixtures/build_fixture.py's docstring"
    )


# --- #10: branches the committed fixture can never reach ------------------
#
# no_transcript, detail.subagents_title, past_projects (both plural forms),
# col.tmux, no_project and fewer never render against tests/fixtures/
# build_fixture.py's own data (no subagent has ever run against it, no
# record is ever missing a project_path, and it never invents more than
# seven projects) - so test_the_html_dashboard_speaks_only_its_own_language
# and test_the_tui_speaks_only_its_own_language, however thorough, cannot
# see these strings at all. Driving each render function directly, with
# hand-built data that reaches exactly the branch in question, is the only
# way any of them are ever checked.


def test_col_tmux_header_speaks_only_its_own_language() -> None:
    import dashboard_html as dh

    for lang in ("en", "pt"):
        # Visible text only (addition A) - the raw HTML's own class names
        # ("session-row", "tmux-window", ...) are English chrome that is
        # never rendered as text, and language_leaks over the raw markup
        # would flag them as leaks that no reader could ever see.
        header = _visible_html_text(dh.render_session_header(True, lang=lang))
        assert mr.t("col.tmux", lang) in header
        assert language_leaks(header, lang) == []


def test_detail_panel_subagents_title_and_no_transcript_speak_only_their_own_language() -> None:
    # One subagent, with tokens (survives render_detail_panel's own
    # zero-token filter) and no agent_label - exactly the shape that
    # reaches both detail.subagents_title (the panel has something to
    # title) and no_transcript (this specific row has nothing else to show
    # in its task column).
    subagents = [
        {
            "session_id": "s-1",
            "agent_id": "a-1",
            "agent_type": "general-purpose",
            "input_tokens": 5,
            "output_tokens": 5,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "duration_seconds": 3.0,
            "timestamp_start": "2026-01-01T09:00:00",
        }
    ]
    import dashboard_html as dh

    for lang in ("en", "pt"):
        panel = _visible_html_text(dh.render_detail_panel("s-1", subagents, True, lang=lang))
        assert mr.t("detail.subagents_title", lang, n=1) in panel
        assert mr.t("no_transcript", lang) in panel
        assert language_leaks(panel, lang) == []


def test_output_floor_note_speaks_only_its_own_language() -> None:
    # The note lives in title attributes, which _visible_html_text strips -
    # so the page-wide HTML test above can never see it. Checked directly,
    # both plural forms, in both renderers that carry it.
    import dashboard_html as dh

    session = {"session_id": "s-1", "output_tokens": 5, "incomplete_usage_messages": 1}
    subagent = dict(session, agent_type="general-purpose", incomplete_usage_messages=2)
    for lang in ("en", "pt"):
        row = dh.render_session_row(session, False, {}, 0, lang=lang)
        panel = dh.render_detail_panel("s-1", [subagent], True, lang=lang)
        titles = html_lib.unescape(" ".join(re.findall(r'title="([^"]*)"', row + panel)))
        assert mr.tn("floor_note", 1, lang) in titles
        assert mr.tn("floor_note", 2, lang) in titles
        assert language_leaks(titles, lang) == []


def _no_project_records() -> list[dict[str, Any]]:
    """Two records, on different days (render_trend_chart returns "" below
    two distinct buckets), neither with a project_path at all - the branch
    that falls back to no_project."""
    return [
        {
            "project_path": None,
            "timestamp_start": "2026-01-01T09:00:00",
            "timestamp_end": "2026-01-01T09:30:00",
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
        {
            "project_path": None,
            "timestamp_start": "2026-01-02T09:00:00",
            "timestamp_end": "2026-01-02T09:30:00",
            "input_tokens": 1,
            "output_tokens": 1,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    ]


def test_no_project_label_speaks_only_its_own_language() -> None:
    import dashboard_html as dh

    records = _no_project_records()
    for lang in ("en", "pt"):
        chart = _visible_html_text(dh.render_trend_chart(records, {}, "tudo", lang=lang))
        assert mr.t("no_project", lang) in chart
        assert language_leaks(chart, lang) == []


def _records_for_hidden_projects(count: int) -> list[dict[str, Any]]:
    """`count` distinct, nonzero-token projects - enough that the ten-project
    cutoff both render_project_totals_chart and ProjectRankingList apply
    pushes `count - 10` of them into the "more/past projects" overflow,
    which is the only way either widget ever renders past_projects at all.
    """
    return [
        {
            "project_path": f"/synthetic/project-{index:02d}",
            "input_tokens": index + 1,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
        for index in range(count)
    ]


@pytest.mark.parametrize("hidden_count", [1, 2])
def test_past_projects_both_plural_forms_speak_only_their_own_language(hidden_count: int) -> None:
    import dashboard_html as dh

    records = _records_for_hidden_projects(10 + hidden_count)
    for lang in ("en", "pt"):
        chart = _visible_html_text(
            dh.render_project_totals_chart(records, {}, {}, set(), "tudo", lang=lang)
        )
        assert mr.tn("past_projects", hidden_count, lang) in chart
        assert language_leaks(chart, lang) == []


def test_project_ranking_list_fewer_and_past_projects_speak_only_their_own_language() -> None:
    import dashboard_tui as tui

    records = _records_for_hidden_projects(12)  # 2 hidden - forces the plural
    for lang in ("en", "pt"):
        widget = tui.ProjectRankingList(mr, records, {}, {}, set(), lang=lang)
        collapsed = widget.render()
        assert mr.tn("past_projects", 2, lang) in collapsed
        assert language_leaks(collapsed, lang) == []

        widget.expanded = True
        expanded = widget.render()
        assert mr.t("fewer", lang) in expanded
        assert language_leaks(expanded, lang) == []


# --- the TUI half --------------------------------------------------------
#
# Textual's SVG export renders every inter-word gap as U+00A0 (a
# non-breaking space, not a plain " ") - confirmed 2026-09-23 by inspecting
# a real export: "(1 session · N tokens)" comes back as
# "(1\xa0session\xa0·\xa0N\xa0tokens)". Left unnormalized, no multi-word
# marker or catalog value could ever match, silently turning this into a
# detector that only catches single-word leaks.
def _visible_svg_text(svg: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", svg)).replace("\xa0", " ")


def _seed_one_subagent_under_week_1(monkeypatch: pytest.MonkeyPatch, home: Path) -> Path:
    """The committed fixture, plus ONE subagent under session "week-1" -
    just enough to reach SessionDetailPanel's populated branch
    (detail.subagents_title, and its own column header).

    Deliberately NOT tests/test_tui_clicks.py's own
    seeded_tui_home_with_subagents: its third subagent's agent_type,
    "zero-duration-agent", contains the whole word "duration" - which is
    col.duration's own ENGLISH catalog value, and so registers as a false
    "en" leak on the Portuguese screen. That collision is a property of an
    identifier chosen for a different test's purpose (pinning the
    zero-vs-None duration distinction), not a missed translation site, so
    it is not something to chase here by touching that shared fixture -
    a smaller, dedicated one avoids the collision instead.
    """
    from test_tui_state import seeded_tui_home

    seeded_tui_home(monkeypatch, home)
    subagents_path = home / ".claude" / "metrics" / "subagents.jsonl"
    subagents_path.write_text(json.dumps(_SEEDED_SUBAGENT) + "\n")
    return home


def _tui_screens(home: Path, lang: str) -> list[str]:
    """Every screen a person actually looks at, in one drive: the sessions
    tree, an expanded group's session with subagents (reaching
    SessionDetailPanel's populated branch and its own column header - the
    empty branch is already covered by every OTHER TUI test's Portuguese
    default), then "g" (Panorama), "t", "t" (the chart's three modes) and
    "p" (a period cycle, and its notification).
    """
    from textual.widgets import Tree

    import dashboard_tui as tui

    async def _drive() -> list[str]:
        parsed_arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
        app = tui.MetricsTUIApp(
            mr, tui.load_data(parsed_arguments, metrics_core_module=mr), arguments=parsed_arguments
        )
        screens = []
        # notifications=True: run_test() disables the Toast rack by default
        # (Textual 8.2.8), which would otherwise hide the "p" notification
        # from every screenshot regardless of language.
        async with app.run_test(size=(140, 45), notifications=True) as pilot:
            await pilot.pause()
            screens.append(app.export_screenshot())

            tree = app.query_one(Tree)
            beta_node = next(child for child in tree.root.children if "beta" in str(child.label))
            beta_node.expand()
            await pilot.pause()
            week_row = next(
                index
                for index, line in enumerate(tree._tree_lines)
                if line.path[-1].data == {"session_id": "week-1"}
            )
            await pilot.click(tree, offset=(4, week_row))
            await pilot.pause()
            screens.append(app.export_screenshot())

            await pilot.press("g")
            await pilot.pause()
            screens.append(app.export_screenshot())
            await pilot.press("t")
            await pilot.pause()
            screens.append(app.export_screenshot())
            await pilot.press("t")
            await pilot.pause()
            screens.append(app.export_screenshot())
            await pilot.press("p")
            await pilot.pause()
            screens.append(app.export_screenshot())
        return screens

    monkeypatch = pytest.MonkeyPatch()
    try:
        _seed_one_subagent_under_week_1(monkeypatch, home)
        (home / ".claude" / "metrics" / "config.json").write_text(json.dumps({"language": lang}))
        return [_visible_svg_text(svg) for svg in asyncio.run(_drive())]
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_tui_speaks_only_its_own_language(tmp_path: Path, lang: str) -> None:
    screens = _tui_screens(tmp_path, lang)
    combined = "\n".join(screens)
    # A positive assertion first (addition B, mirroring the HTML test
    # above): must actually say something in its own language before "says
    # nothing foreign" means anything.
    assert ("Sessions" if lang == "en" else "Sessões") in combined
    # A second positive assertion, narrower than the first: _tui_screens's
    # own click into week-1's detail panel is what is supposed to reach
    # SessionDetailPanel's populated branch (detail.subagents_title, n=1 -
    # _seed_one_subagent_under_week_1 seeds exactly one). Without this, a
    # click that silently missed its row - landing on the group header
    # instead, say - would leave that branch unscanned by every assertion
    # here, and the leak check above would report a clean pass for a
    # screen that never rendered the thing this test exists to cover.
    assert mr.t("detail.subagents_title", lang, n=1) in combined
    # The panel wraps the note over two lines, so only its head is matched
    # whole; the leak check below still reads every word of both lines.
    assert mr.tn("floor_note", 2, lang)[:40] in combined
    assert language_leaks(combined, lang) == []


def _panorama_with_no_data(home: Path, lang: str) -> str:
    """ProjectLegend.render() and ProjectRankingList.render() each have
    their own early return for an empty chart_records - "no_data" in the
    catalog - which the committed fixture can NEVER reach (every period is
    non-empty by construction, see fixtures/build_fixture.py's own
    docstring). Without a dedicated empty-data drive, a canary on that
    literal is blind: reverting it to "Sem dados." was tried by hand while
    writing this file and the drive above stayed green, because neither
    widget's early return ever runs against that fixture. This function
    is what makes the canary see real damage.
    """
    from test_tui_state import isolate_tui_from_the_real_filesystem

    import dashboard_tui as tui

    async def _drive() -> str:
        parsed_arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
        app = tui.MetricsTUIApp(
            mr, tui.load_data(parsed_arguments, metrics_core_module=mr), arguments=parsed_arguments
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            return app.export_screenshot()

    monkeypatch = pytest.MonkeyPatch()
    try:
        isolate_tui_from_the_real_filesystem(monkeypatch, home)
        (home / ".claude" / "metrics" / "config.json").write_text(json.dumps({"language": lang}))
        # No sessions.jsonl at all - load_records tolerates a missing file
        # and returns [], so every period's chart_records is empty.
        return _visible_svg_text(asyncio.run(_drive()))
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_empty_panorama_speaks_only_its_own_language(tmp_path: Path, lang: str) -> None:
    text = _panorama_with_no_data(tmp_path, lang)
    assert mr.t("no_data", lang) in text
    assert language_leaks(text, lang) == []
