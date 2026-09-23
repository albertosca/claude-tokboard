"""The catalog, the language resolution and the date formats.

The language is never read from the machine running the suite: every
resolution test passes its own environment mapping."""

import json
import locale
import re
from datetime import datetime
from pathlib import Path

import metrics_core as mr
import pytest

PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_every_message_has_both_languages_and_neither_is_empty() -> None:
    for key, texts in mr.MESSAGES.items():
        assert set(texts) == set(mr.LANGUAGES), key
        assert all(text.strip() for text in texts.values()), key


def test_both_languages_use_the_same_placeholders() -> None:
    # A placeholder present in one language only would raise KeyError at
    # runtime, in exactly the language nobody tested by hand.
    for key, texts in mr.MESSAGES.items():
        assert set(PLACEHOLDER.findall(texts["en"])) == set(PLACEHOLDER.findall(texts["pt"])), key


def test_every_plural_has_both_forms() -> None:
    bases = {key.rsplit(".", 1)[0] for key in mr.MESSAGES if key.endswith((".one", ".other"))}
    for base in bases:
        assert f"{base}.one" in mr.MESSAGES and f"{base}.other" in mr.MESSAGES, base


def test_t_fills_its_placeholders() -> None:
    assert mr.t("notify.period", "en", name="all") == "period: all"
    assert mr.t("notify.period", "pt", name="tudo") == "período: tudo"


@pytest.mark.parametrize(
    ("count", "en", "pt"),
    [(0, "0 sessions", "0 sessões"), (1, "1 session", "1 sessão"), (2, "2 sessions", "2 sessões")],
)
def test_tn_uses_the_singular_for_exactly_one(count: int, en: str, pt: str) -> None:
    assert mr.tn("sessions", count, "en") == en
    assert mr.tn("sessions", count, "pt") == pt


@pytest.mark.parametrize(
    ("config", "environ", "expected"),
    [
        ({"language": "en"}, {"LC_ALL": "pt_BR.UTF-8"}, "en"),  # config beats the locale
        ({"language": "pt"}, {"LANG": "en_US.UTF-8"}, "pt"),
        ({"language": " PT "}, {}, "pt"),  # trimmed and lower-cased
        ({"language": "es"}, {"LANG": "pt_BR.UTF-8"}, "pt"),  # invalid: falls through
        ({"language": 42}, {"LANG": "en_US.UTF-8"}, "en"),  # not a string: falls through
        ({}, {"LC_ALL": "pt_BR.UTF-8", "LC_MESSAGES": "en_US", "LANG": "en_US"}, "pt"),
        ({}, {"LC_ALL": "", "LC_MESSAGES": "pt_PT", "LANG": "en_US"}, "pt"),  # empty = unset
        ({}, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "pt"}, "pt"),
        ({}, {"LANG": "C.UTF-8"}, "en"),
        ({}, {}, "en"),  # nothing at all
    ],
)
def test_resolve_language(
    config: dict[str, object], environ: dict[str, str], expected: str
) -> None:
    assert mr.resolve_language(config, environ) == expected


MOMENT = datetime(2026, 9, 23, 9, 30)


@pytest.mark.parametrize(
    ("granularity", "pt", "en"),
    [
        ("minute", "09:30", "09:30"),
        ("hour", "23/09 09h", "Sep 23 09:00"),
        ("day", "23/09", "Sep 23"),
        ("week", "S39/26", "W39/26"),
        ("month", "09/2026", "Sep 2026"),
        ("year", "2026", "2026"),
    ],
)
def test_tick_label_in_both_languages(granularity: str, pt: str, en: str) -> None:
    assert mr.tick_label(granularity, MOMENT, "pt") == pt
    assert mr.tick_label(granularity, MOMENT, "en") == en


def test_english_month_names_come_from_the_table_for_every_month() -> None:
    names = [mr.tick_label("month", datetime(2026, m, 1), "en").split()[0] for m in range(1, 13)]
    assert names == ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]  # fmt: skip


def test_english_month_names_ignore_a_portuguese_process_locale() -> None:
    # The canary for strftime("%b"): under pt_BR it prints "set" for
    # September. Skipped only where the machine has no pt_BR locale.
    try:
        previous = locale.setlocale(locale.LC_TIME)
        locale.setlocale(locale.LC_TIME, "pt_BR.UTF-8")
    except locale.Error:
        pytest.skip("no pt_BR locale on this machine")
    try:
        assert mr.tick_label("day", MOMENT, "en") == "Sep 23"
    finally:
        locale.setlocale(locale.LC_TIME, previous)


def test_session_date_in_both_languages() -> None:
    stamp = datetime(2026, 9, 23, 9, 30).astimezone().isoformat()
    assert mr.session_date(stamp, "pt") == "23/09 09:30"
    assert mr.session_date(stamp, "en") == "Sep 23 09:30"


@pytest.mark.parametrize("bad", [None, "", "not a date"])
def test_session_date_of_nothing_or_garbage_is_a_dash(bad: str | None) -> None:
    assert mr.session_date(bad, "en") == "-"
    assert mr.session_date(bad, "pt") == "-"


def test_load_config_reads_an_object(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"language": "pt", "project_roots": ["/a"]}))
    assert mr.load_config(str(path)) == {"language": "pt", "project_roots": ["/a"]}


@pytest.mark.parametrize("content", [None, "{not json", json.dumps(["a"]), json.dumps("x")])
def test_load_config_tolerates_anything_else(tmp_path: Path, content: str | None) -> None:
    path = tmp_path / "config.json"
    if content is not None:
        path.write_text(content)
    assert mr.load_config(str(path)) == {}
