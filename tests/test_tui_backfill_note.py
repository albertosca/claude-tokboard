"""The TUI's backfill note: visible when the app opens, one line, and the
whole text on hover (backlog 2026-09-24 #5).

Measured before the fix (2026-09-25), with a correction applied and a
retention boundary recorded - the longest note describe_backfill_state
writes: the note wrapped to 2-4 lines at 80-140 columns, and every
screen opened scrolled down by exactly that many lines, so the note sat
above the top of the terminal. The cause was not the note alone.
TabbedContent's height is `auto` by default, and its panes fill the
screen, so it claimed the whole terminal no matter what was above it; the
screen's content ran taller than the screen by the note's height, and
focusing the session tree on mount scrolled the screen to show it.
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
from test_tui_state import seeded_tui_home  # noqa: E402
from textual.widgets import Static  # noqa: E402

import dashboard_tui as tui  # noqa: E402

# A correction applied, with the retention boundary: the longest note.
STATE = {
    "cutoff": "2026-07-22",
    "applied_at": "2026-08-21T10:00:00",
    "earliest_corrected": "2026-07-01",
}


def _visible_text(svg: str) -> str:
    """Textual's SVG export, as text - see test_tui_i18n.py's _strip for
    why the non-breaking spaces are normalized."""
    return html.unescape(re.sub(r"<[^>]+>", " ", svg)).replace("\xa0", " ")


def _opened(
    monkeypatch: pytest.MonkeyPatch, home: Path, lang: str, width: int, *, hover: bool = False
) -> dict[str, Any]:
    seeded_tui_home(monkeypatch, home)
    metrics = home / ".claude" / "metrics"
    (metrics / "config.json").write_text(json.dumps({"language": lang}))
    (metrics / "backfill_state.json").write_text(json.dumps(STATE))
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])

    async def drive() -> dict[str, Any]:
        app = tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args)
        async with app.run_test(size=(width, 30), tooltips=True) as pilot:
            await pilot.pause()
            note = app.query(".backfill-note").first(Static)
            opened = {
                "screen": _visible_text(app.export_screenshot()),
                "scroll_y": app.screen.scroll_y,
                "note_height": note.region.height,
                "note_line": "".join(segment.text for segment in note.render_line(0)),
                "tooltip": note.tooltip,
                "virtual_height": app.screen.virtual_size.height,
            }
            if not hover:
                # The overview tab too: it opened scrolled the same way.
                await pilot.press("g")
                await pilot.pause()
                opened["overview_screen"] = _visible_text(app.export_screenshot())
                opened["overview_scroll_y"] = app.screen.scroll_y
                return opened
            await pilot.hover(".backfill-note")
            await pilot.pause(1.0)
            opened["hover_screen"] = _visible_text(app.export_screenshot())
            return opened

    return asyncio.run(drive())


@pytest.mark.parametrize("lang", ["en", "pt"])
@pytest.mark.parametrize("width", [80, 140])
def test_the_note_is_on_the_first_screen_and_the_tabs_are_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lang: str, width: int
) -> None:
    opened = _opened(monkeypatch, tmp_path, lang, width)
    note = mr.describe_backfill_state(STATE, lang=lang)
    assert opened["scroll_y"] == 0
    assert opened["virtual_height"] == 30, "the layout is taller than the terminal"
    assert note[:30] in opened["screen"]
    assert mr.t("tab.sessions", lang) in opened["screen"]
    assert mr.t("tab.panorama", lang) in opened["screen"]
    assert opened["overview_scroll_y"] == 0
    assert note[:30] in opened["overview_screen"]


@pytest.mark.parametrize("lang", ["en", "pt"])
def test_the_note_is_one_line_cut_with_an_ellipsis_and_whole_on_hover(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lang: str
) -> None:
    opened = _opened(monkeypatch, tmp_path, lang, 80, hover=True)
    note = mr.describe_backfill_state(STATE, lang=lang)
    assert opened["note_height"] == 1
    assert opened["note_line"].rstrip().endswith("…")
    assert opened["tooltip"] == note
    # The last word is the one the one-line note never shows at 80 columns,
    # so seeing it proves the tooltip, not the note, is on screen.
    last_word = note.split()[-1]
    assert last_word not in opened["note_line"]
    assert last_word in opened["hover_screen"]
