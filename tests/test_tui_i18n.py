"""The TUI dashboard in English, driven through Textual's own testing API -
see test_tui_state.py's module docstring for why this runs in-process
rather than as a subprocess, and test_html_i18n.py for the same coverage
on the HTML dashboard.

Portuguese is already pinned end to end by every other test in this suite
(seeded_tui_home's config.json defaults to {"language": "pt"}), so this
file's job is narrower: prove the "en" config value actually reaches every
widget, including the key bar, which needed a structural change (bindings
built per-instance in __init__ instead of the class-level BINDINGS) because
its descriptions are themselves translated text.

Textual 8.2.8 has no public attribute listing notifications (only `notify`
and `clear_notifications`, checked 2026-09-23), so the notification is
asserted where a person actually sees it: in the screenshot taken right
after "p".
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import metrics_core as mr  # noqa: E402
from test_language_leak import language_leaks  # noqa: E402
from test_tui_state import seeded_tui_home  # noqa: E402

import dashboard_tui as tui  # noqa: E402


def _strip(svg: str) -> str:
    """Textual's SVG export renders every inter-word gap as U+00A0 (a
    non-breaking space, not a plain " ") - confirmed 2026-09-23 by
    inspecting a real export: "(1 session · N tokens)" comes back as
    "(1\xa0session\xa0·\xa0N\xa0tokens)". Left unnormalized, every
    multi-word assertion here (and in the leak detector) would silently
    never match, since a plain-space literal cannot appear in a string
    that never contains a plain space between words."""
    return html.unescape(re.sub(r"<[^>]+>", " ", svg)).replace("\xa0", " ")


async def _english_screens() -> dict[str, Any]:
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    app = tui.MetricsTUIApp(
        mr, tui.load_data(arguments, metrics_core_module=mr), arguments=arguments
    )
    # notifications=True: run_test() disables the Toast rack by default
    # (Textual 8.2.8's own default, verified 2026-09-23) - without this the
    # notify() call after "p" still records a Notification internally but
    # never mounts a Toast widget, so it would never appear in a screenshot
    # regardless of language.
    async with app.run_test(size=(140, 45), notifications=True) as pilot:
        await pilot.pause()
        sessions = app.export_screenshot()
        await pilot.press("g")
        await pilot.pause()
        chart = app.export_screenshot()
        await pilot.press("p")
        await pilot.pause()
        after_period_press = app.export_screenshot()
        return {"sessions": sessions, "chart": chart, "after_p": after_period_press}


@pytest.fixture(scope="session")
def english_screens(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """The committed fixture, rendered in English - config.json wins over
    the pt_BR locale seeded_tui_home also pins (that is the entire point:
    the config, not the machine, decides the language)."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        home = seeded_tui_home(monkeypatch, tmp_path_factory.mktemp("tui-i18n-en-home"))
        (home / ".claude" / "metrics" / "config.json").write_text(json.dumps({"language": "en"}))
        raw = asyncio.run(_english_screens())
        return {name: _strip(svg) for name, svg in raw.items()}
    finally:
        monkeypatch.undo()


def test_the_key_bar_is_english(english_screens: dict[str, str]) -> None:
    for text in ("Quit", "Chart mode", "Charts/Sessions", "period"):
        assert text in english_screens["sessions"], text


def test_the_sessions_screen_is_english(english_screens: dict[str, str]) -> None:
    """Also the revert-check for load_base_data actually resolving the
    language: a hardcoded `lang = "pt"` there leaves data["lang"] == "pt"
    regardless of config.json, and this is the assertion that catches it -
    "Sessions" would be "Sessões" and "(1 session · " would be
    "(1 sessão · "."""
    assert "Sessions" in english_screens["sessions"]
    assert "(1 session · " in english_screens["sessions"]


def test_the_chart_screen_is_english(english_screens: dict[str, str]) -> None:
    assert "Tokens per day (by project)" in english_screens["chart"]


def test_the_notification_after_cycling_the_period_is_english(
    english_screens: dict[str, str],
) -> None:
    assert "period: yesterday" in english_screens["after_p"]
    assert language_leaks(english_screens["after_p"], "en") == []


def test_language_falls_back_to_the_pinned_locale_not_the_real_machines_locale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seeded_tui_home's isolation helper writes config.json WITH a
    "language" key, which always wins over the locale (resolve_language's
    own contract) - so no other test in this suite actually exercises the
    locale-FALLBACK branch. This one does: config.json is overwritten to
    drop "language" entirely (the same shape test_project_roots_seam.py's
    own config write leaves it in), so the only thing keeping this test's
    expectation Portuguese is the LC_ALL=pt_BR.UTF-8 the isolation helper
    also pins - never this machine's real locale, whatever it is.

    Revert-check: with the LC_ALL/LC_MESSAGES/LANG pinning lines removed
    from isolate_tui_from_the_real_filesystem and the suite then run under
    a real LC_ALL=en_US.UTF-8, this test goes red (data["lang"] resolves
    to "en") - the pin Task 2 could not make red on its own, since every
    OTHER seeded test's config.json already carries an explicit "language"
    key that wins regardless of locale.
    """
    home = seeded_tui_home(monkeypatch, tmp_path)
    (home / ".claude" / "metrics" / "config.json").write_text(json.dumps({}))
    arguments = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = tui.load_data(arguments, metrics_core_module=mr)
    assert data["lang"] == "pt"
