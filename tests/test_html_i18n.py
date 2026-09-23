"""The HTML dashboard in each language, read off the real generated page."""

from pathlib import Path

from test_language_leak import fixture_page_html


def test_the_page_declares_its_language(tmp_path: Path) -> None:
    assert '<html lang="en">' in fixture_page_html(tmp_path / "en", "en")
    assert '<html lang="pt-BR">' in fixture_page_html(tmp_path / "pt", "pt")


def test_group_headers_count_one_session_in_the_singular(tmp_path: Path) -> None:
    en = fixture_page_html(tmp_path / "en", "en")
    assert "(1 session · " in en and "(2 sessions · " in en
    assert "(1 sessions" not in en
    pt = fixture_page_html(tmp_path / "pt", "pt")
    assert "(1 sessão · " in pt and "(2 sessões · " in pt


def test_the_tabs_modes_and_quota_line_are_english(tmp_path: Path) -> None:
    page = fixture_page_html(tmp_path, "en")
    for text in (
        "By project",
        "By token type",
        "By model",
        "Quota (approx.):",
        "Sessions",
        "Overview",
    ):
        assert text in page, text


def test_the_sort_note_now_says_what_the_panel_does(tmp_path: Path) -> None:
    assert "sorted by total tokens, most first" in fixture_page_html(tmp_path / "en", "en")
    pt = fixture_page_html(tmp_path / "pt", "pt")
    assert "ordenado por total de tokens, do maior para o menor" in pt
    assert "atividade mais recente" not in pt
