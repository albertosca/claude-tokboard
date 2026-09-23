"""No string of the other language reaches either interface.

This is the completeness oracle for the translation: the inventory of
strings is taken by rendering, not by grepping, because a grep misses
f-string fragments and words too short to look foreign ("aba"). A marker
only counts if it cannot occur legitimately in the other language: a
Portuguese value that also appears, as a whole word, inside some English
value ("data" is in "data before...") is not evidence of a leak.
"""

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


@pytest.mark.parametrize(
    "lang",
    [
        pytest.param(
            "en", marks=pytest.mark.xfail(strict=True, reason="Task 3 translates the HTML")
        ),
        "pt",
    ],
)
def test_the_html_dashboard_speaks_only_its_own_language(tmp_path: Path, lang: str) -> None:
    # The machine locale is pinned to pt_BR on purpose: the English page
    # must come out English because the CONFIG says so (Review Focus 1).
    assert language_leaks(_render_html(tmp_path, lang), lang) == []


def test_the_detector_fires_on_an_injected_leak() -> None:
    # The canary: without it, an empty marker list would pass everything.
    assert _markers("en"), "no Portuguese markers - the detector is blind"
    assert "Nenhum subagente nesta sessão." in language_leaks(
        "Sessions ... Nenhum subagente nesta sessão. ...", "en"
    )
    assert "Sessions" in language_leaks("Sessões ... Sessions", "pt")
