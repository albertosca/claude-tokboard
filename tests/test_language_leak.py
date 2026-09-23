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

import metrics_core as mr
import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

PT_DIACRITICS = re.compile(r"[ãõçáéíóúâêôà]")


def _words_in(value: str, others: list[str]) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")
    return any(pattern.search(other) for other in others)


def _markers(lang: str) -> list[str]:
    """Catalog values that belong ONLY to the other language."""
    other = "en" if lang == "pt" else "pt"
    mine = [texts[lang] for texts in mr.MESSAGES.values()]
    found = []
    for texts in mr.MESSAGES.values():
        value = texts[other].strip()
        if value == texts[lang].strip() or "{" in value:
            continue
        if not _words_in(value, mine):
            found.append(value)
    return found


def language_leaks(text: str, lang: str) -> list[str]:
    hits = [m for m in _markers(lang) if re.search(rf"(?<!\w){re.escape(m)}(?!\w)", text)]
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
    sys.path[:0] = [str(REPO / "tests"), str(REPO / "lib")]
    from datetime import datetime

    from fixtures.build_fixture import build_fixture

    build_fixture(str(home), datetime.now().astimezone())
    metrics = home / ".claude" / "metrics"
    (metrics / "config.json").write_text(json.dumps({"language": lang}))
    env = dict(os.environ, HOME=str(home), PYTHONPATH=str(REPO / "lib"),
               LC_ALL="pt_BR.UTF-8", LC_MESSAGES="", LANG="")  # fmt: skip
    subprocess.run(
        [sys.executable, str(REPO / "dashboard_html.py"), "--html", "--label-budget", "0", "--all"],
        env=env, cwd=str(REPO), check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    return (metrics / "dashboard.html").read_text()


def _render_html(home: Path, lang: str) -> str:
    return _visible_html_text(fixture_page_html(home, lang))


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


# --- the TUI half --------------------------------------------------------
#
# Textual's SVG export renders every inter-word gap as U+00A0 (a
# non-breaking space, not a plain " ") - confirmed 2026-09-23 by inspecting
# a real export: "(1 session · 154 tokens)" comes back as
# "(1\xa0session\xa0·\xa0154\xa0tokens)". Left unnormalized, no multi-word
# marker or catalog value could ever match, silently turning this into a
# detector that only catches single-word leaks.
def _visible_svg_text(svg: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", " ", svg)).replace("\xa0", " ")


def _seed_one_subagent_under_week_1(mp: pytest.MonkeyPatch, home: Path) -> Path:
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

    seeded_tui_home(mp, home)
    subagent = {
        "session_id": "week-1",
        "agent_id": "sub-a",
        "agent_type": "general-purpose",
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 1,
        "cache_creation_tokens": 1,
        "tool_call_count": 3,
        "duration_seconds": 12.5,
    }
    subagents_path = home / ".claude" / "metrics" / "subagents.jsonl"
    subagents_path.write_text(json.dumps(subagent) + "\n")
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
        args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
        app = tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args)
        screens = []
        # notifications=True: run_test() disables the Toast rack by default
        # (Textual 8.2.8), which would otherwise hide the "p" notification
        # from every screenshot regardless of language.
        async with app.run_test(size=(140, 45), notifications=True) as pilot:
            await pilot.pause()
            screens.append(app.export_screenshot())

            tree = app.query_one(Tree)
            beta_node = next(c for c in tree.root.children if "beta" in str(c.label))
            beta_node.expand()
            await pilot.pause()
            week_row = next(
                i
                for i, line in enumerate(tree._tree_lines)
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

    mp = pytest.MonkeyPatch()
    try:
        _seed_one_subagent_under_week_1(mp, home)
        (home / ".claude" / "metrics" / "config.json").write_text(json.dumps({"language": lang}))
        return [_visible_svg_text(svg) for svg in asyncio.run(_drive())]
    finally:
        mp.undo()


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_tui_speaks_only_its_own_language(tmp_path: Path, lang: str) -> None:
    screens = _tui_screens(tmp_path, lang)
    combined = "\n".join(screens)
    # A positive assertion first (addition B, mirroring the HTML test
    # above): must actually say something in its own language before "says
    # nothing foreign" means anything.
    assert ("Sessions" if lang == "en" else "Sessões") in combined
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
        args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
        app = tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("g")
            await pilot.pause()
            return app.export_screenshot()

    mp = pytest.MonkeyPatch()
    try:
        isolate_tui_from_the_real_filesystem(mp, home)
        (home / ".claude" / "metrics" / "config.json").write_text(json.dumps({"language": lang}))
        # No sessions.jsonl at all - load_records tolerates a missing file
        # and returns [], so every period's chart_records is empty.
        return _visible_svg_text(asyncio.run(_drive()))
    finally:
        mp.undo()


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_empty_panorama_speaks_only_its_own_language(tmp_path: Path, lang: str) -> None:
    text = _panorama_with_no_data(tmp_path, lang)
    assert mr.t("no_data", lang) in text
    assert language_leaks(text, lang) == []
