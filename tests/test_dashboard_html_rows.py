"""Covers dashboard_html.py's row and panel renderers: render_session_row,
render_group_header, render_detail_panel.

Every expected value below was derived by running the function under test
(not inferred from reading it) - see the task brief's rule 1. Assertions
pin structure and invariants (which fields appear, ordering, class
membership, absence of inline styles) rather than byte-exact markup, so an
innocent whitespace refactor doesn't fail the suite.
"""

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import metrics_core as mr

import dashboard_html

# --------------------------------------------------------------------------
# render_session_row
# --------------------------------------------------------------------------


def _base_record(**overrides: Any) -> dict[str, Any]:
    record = {
        "session_id": "abcdefghijklmnopqrstuvwxyz",  # 26 chars, longer than the [:12] bound
        "task_label": "review the pricing page copy",
        "tag": "mytag",
        "git_branch": "main",
        "tmux_window": "1:zsh",
        "input_tokens": 1000,
        "output_tokens": 2000,
        "duration_seconds": 125,
        "timestamp_start": "2026-08-20T10:00:00Z",
        "project_path": "/proj/a",
    }
    record.update(overrides)
    return record


def _extract_span(html_out: str, css_class: str) -> str:
    # [^>]* after the class value tolerates a cosmetic attribute like
    # title="..." (2026-09-09, the tmux-window/branch-col title tooltips) -
    # same "located by identity, not by being the tag's only attribute"
    # philosophy as the newer locators below, just applied to this older
    # helper too rather than leaving two competing strictness levels.
    match = re.search(rf'<span class="{re.escape(css_class)}"[^>]*>(.*?)</span>', html_out)
    assert match, f'no <span class="{css_class}"> found in {html_out!r}'
    return match.group(1)


# --------------------------------------------------------------------------
# Attribute-order-independent locators (review M1). Elements are found by
# identity - an attribute they genuinely carry - and attributes are read by
# NAME, never by position in the tag, so adding or reordering a cosmetic
# attribute never fails a test that isn't about that attribute.
# --------------------------------------------------------------------------


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'{re.escape(name)}="([^"]*)"', attrs)
    return m.group(1) if m else None


def _attr_names(attrs: str) -> set[str]:
    """Attribute NAMES present in a tag's attribute string, including
    valueless boolean attributes like `hidden`. Substring-testing the raw
    string instead would flip on any future attribute whose name merely
    contains "hidden"."""
    return set(re.findall(r'([A-Za-z_:][-A-Za-z0-9_:.]*)(?:="[^"]*")?', attrs))


def _period_block(html_out: str, period_id: str) -> tuple[str, str]:
    """The (attributes, inner text) of the group header's period-count span
    for one period, located by its data-period value and group-count class
    rather than by a literal attribute sequence."""
    # Scanned as opening tags rather than as `<span...>(.*?)</span>` pairs:
    # the period blocks are nested inside an outer <span>, and a paired
    # regex lets the outer match swallow the first inner one whole.
    for m in re.finditer(r"<span\b([^>]*)>", html_out):
        attrs = m.group(1)
        classes = (_attr(attrs, "class") or "").split()
        if _attr(attrs, "data-period") == period_id and "group-count" in classes:
            # No period block contains a nested span, so its content runs
            # to the next closing tag.
            return attrs, html_out[m.end() : html_out.index("</span>", m.end())]
    raise AssertionError(f"no group-count period-block span for period {period_id!r}")


def _div_classes(html_out: str, first_class: str) -> list[str]:
    """Every <div>'s full class string, in document order, whose class list
    starts with first_class - located by class membership, not by class
    being the div's only attribute."""
    found = []
    for m in re.finditer(r"<div\b([^>]*)>", html_out):
        classes = (_attr(m.group(1), "class") or "").split()
        if classes and classes[0] == first_class:
            found.append(" ".join(classes))
    return found


def test_render_session_row_pins_own_fields():
    record = _base_record()
    out = dashboard_html.render_session_row(
        record,
        is_active=False,
        group_colors={"/proj/a": "group-color-3"},
        group_index=2,
        subagent_count=5,
        show_tmux=True,
        period_ids=("ontem", "tudo"),
        lang="pt",
    )
    assert f'id="row-{record["session_id"]}"' in out
    assert f"showSession('{record['session_id']}')" in out
    assert _extract_span(out, "date-col") == mr.format_date(record["timestamp_start"], lang="pt")
    assert _extract_span(out, "label-col") == record["task_label"]
    assert _extract_span(out, "tag-col") == record["tag"]
    assert _extract_span(out, "branch-col") == record["git_branch"]
    assert _extract_span(out, "tokens-total") == mr.format_tokens(mr.record_token_total(record))
    assert _extract_span(out, "duration-col") == mr.format_duration(record["duration_seconds"])
    assert _extract_span(out, "subagent-col") == "🤖5"


def test_render_session_row_subagent_badge_absent_when_count_is_zero():
    record = _base_record()
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, subagent_count=0, lang="pt"
    )
    assert _extract_span(out, "subagent-col") == ""


def test_render_session_row_html_escapes_tag_and_branch():
    record = _base_record(tag="<script>", git_branch="a&b")
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, lang="pt"
    )
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "a&amp;b" in out


def test_render_session_row_period_membership_lists_exactly_the_given_periods():
    record = _base_record()
    out = dashboard_html.render_session_row(
        record,
        is_active=False,
        group_colors={},
        group_index=0,
        period_ids=("ontem", "15-dias", "tudo"),
        lang="pt",
    )
    match = re.search(r'data-periods="([^"]*)"', out)
    assert match
    assert match.group(1) == "ontem 15-dias tudo"

    # A different set of periods produces a correspondingly different
    # attribute - this is not incidental to the previous assertion, it
    # rules out a hardcoded string that happens to match once.
    out_single = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, period_ids=("mes",), lang="pt"
    )
    match_single = re.search(r'data-periods="([^"]*)"', out_single)
    assert match_single
    assert match_single.group(1) == "mes"


def test_render_session_row_carries_its_groups_colour_class():
    record = _base_record(project_path="/proj/a")
    out = dashboard_html.render_session_row(
        record,
        is_active=False,
        group_colors={"/proj/a": "group-color-9", "/proj/b": "group-color-1"},
        group_index=0,
        lang="pt",
    )
    assert "group-color-9" in out
    assert "group-color-1" not in out


def test_render_session_row_falls_back_to_group_color_none_for_unmapped_project():
    record = _base_record(project_path="/unknown/project")
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={"/proj/a": "group-color-9"}, group_index=0, lang="pt"
    )
    assert "group-color-none" in out


def test_render_session_row_active_class_present_only_when_active():
    record = _base_record()
    out_active = dashboard_html.render_session_row(
        record, is_active=True, group_colors={}, group_index=0, lang="pt"
    )
    out_inactive = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, lang="pt"
    )
    assert re.search(r'class="session-row [^"]*\bactive\b', out_active)
    assert not re.search(r'class="session-row [^"]*\bactive\b', out_inactive)


def test_render_session_row_emits_no_inline_display_style():
    # The 2026-08 bug (see production comment at line 127): an inline
    # style="display:..." beats any non-!important class rule regardless
    # of specificity, so an expanded row's inline display:flex won over
    # .session-row.period-hidden { display: none }, and 4530 rows
    # coexisted on screen with 11 chart sessions under the "ontem" filter.
    # Visibility must be class-driven only - this is the highest-value
    # assertion in this module.
    record = _base_record()
    for is_active in (True, False):
        out = dashboard_html.render_session_row(
            record, is_active=is_active, group_colors={}, group_index=0, lang="pt"
        )
        assert 'style="display' not in out
        assert "style='display" not in out
        # The row does emit other inline styles (composition bar segment
        # widths) - confirming those ARE present rules out a vacuous
        # "no style attribute at all" pass.
        assert 'style="width:' in out


def test_render_session_row_tmux_window_carries_an_icon_and_a_title_tooltip():
    # 2026-09-09: Alberto found the tmux_window value read as a second,
    # contradictory project name next to the session's own project group.
    # The icon and title= tooltip mark it as terminal context instead of
    # identity - branch-col gets the same title= treatment since it can
    # also truncate.
    record = _base_record(tmux_window="1:zsh")
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, show_tmux=True, lang="pt"
    )
    assert _extract_span(out, "tmux-window") == "🗔 1:zsh"
    assert 'class="tmux-window" title="1:zsh"' in out
    assert 'class="branch-col" title="main"' in out


def test_render_session_row_no_tmux_omits_tmux_span_and_column():
    record = _base_record()
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, show_tmux=False, lang="pt"
    )
    assert "tmux-window" not in out


def test_render_session_row_missing_optional_fields_renders_without_raising():
    out = dashboard_html.render_session_row(
        {}, is_active=False, group_colors={}, group_index=0, lang="pt"
    )
    assert _extract_span(out, "sid") == ""
    assert _extract_span(out, "tag-col") == "-"
    assert _extract_span(out, "branch-col") == "-"
    assert _extract_span(out, "date-col") == "-"
    assert _extract_span(out, "label-col") == "-"


# --- slice-bound pins (invisible to branch coverage - see brief note B) ---


def test_render_session_row_truncates_session_id_to_12_chars_in_sid_span():
    record = _base_record(session_id="abcdefghijklmnopqrstuvwxyz")  # 26 chars
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, lang="pt"
    )
    sid_text = _extract_span(out, "sid")
    assert sid_text == "abcdefghijkl"
    assert len(sid_text) == 12
    # The full session_id is still used, untruncated, elsewhere in the row
    # (id attribute, onclick handler) - only the visible label is clipped.
    assert record["session_id"] in out


def test_render_session_row_truncates_task_label_over_80_chars_at_a_word_boundary():
    # A label with no spaces: raw_task_label[:80] can't back off to a word
    # boundary, so the slice bound itself is exposed directly.
    record = _base_record(task_label="a" * 85)
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, lang="pt"
    )
    label_text = _extract_span(out, "label-col")
    assert label_text == "a" * 80 + "…"


def test_render_session_row_task_label_of_exactly_80_chars_is_not_truncated():
    record = _base_record(task_label="b" * 80)
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, lang="pt"
    )
    assert _extract_span(out, "label-col") == "b" * 80


def test_render_session_row_truncates_long_label_at_last_word_boundary_with_ellipsis():
    # Spaced words: the code backs off via rsplit(" ", 1) to avoid cutting
    # a word in half, then strips trailing punctuation before the ellipsis.
    record = _base_record(task_label="word " * 20)  # 100 chars
    out = dashboard_html.render_session_row(
        record, is_active=False, group_colors={}, group_index=0, lang="pt"
    )
    label_text = _extract_span(out, "label-col")
    # raw_task_label[:80] lands mid-word ("word "*16 is exactly 80 chars,
    # ending in a trailing space), so rsplit(" ", 1) backs off to drop
    # that trailing empty token and the last full "word" survives intact.
    assert label_text == "word " * 15 + "word" + "…"
    assert len(label_text) == 80
    assert len(record["task_label"]) == 100  # sanity: input really exceeds the bound


# --------------------------------------------------------------------------
# render_group_header
# --------------------------------------------------------------------------


def _group(
    project: str | None, records: list[dict[str, Any]], css_class: str = "group-color-7"
) -> dict[str, Any]:
    return {"project": project, "records": records, "class": css_class}


_ALL_NONE_PERIOD_WINDOWS: dict[str, tuple[datetime, datetime] | None] = {
    p.id: None for p in mr.PERIODS
}


def test_render_group_header_shows_display_label_and_aggregate_token_total():
    records = [
        {
            "session_id": "s1",
            "project_path": "/proj/a",
            "timestamp_start": "2026-08-20T10:00:00Z",
            "input_tokens": 100,
            "output_tokens": 50,
        },
        {
            "session_id": "s2",
            "project_path": "/proj/a",
            "timestamp_start": "2026-08-20T11:00:00Z",
            "input_tokens": 10,
            "output_tokens": 5,
        },
    ]
    group = _group("/proj/a", records)
    period_windows = _ALL_NONE_PERIOD_WINDOWS
    out = dashboard_html.render_group_header(0, group, period_windows, lang="pt")

    assert mr.project_display_label("/proj/a", lang="pt") in out
    expected_total = mr.format_tokens(sum(mr.record_token_total(r) for r in records))
    # _period_block finds the span by the attributes it genuinely carries
    # (data-period + the group-count class), in any order and with any
    # number of other attributes around them - so this test only fails when
    # the thing it's actually named for, the label and aggregate total, is
    # wrong. Two earlier versions were brittle here: the first anchored on
    # the literal sequence data-period="tudo"> with no room for other
    # attributes; the second still required class to be immediately
    # followed by data-period, and broke on an attribute inserted between
    # them (review M1).
    _attrs, body = _period_block(out, "tudo")
    assert expected_total in body
    assert f"({len(records)} sessões" in body


def test_render_group_header_only_default_period_block_is_unhidden():
    # The period-visibility invariant itself, pinned directly and by name -
    # the same family of bug (period.id == DEFAULT_PERIOD_ID deciding what
    # is shown vs. hidden) that render_session_row and render_detail_panel
    # each got a dedicated test for. Every period-block span must carry
    # " hidden" except the default period's own block.
    group = _group("/proj/a", [])
    period_windows = _ALL_NONE_PERIOD_WINDOWS
    out = dashboard_html.render_group_header(0, group, period_windows, lang="pt")
    for period in mr.PERIODS:
        # Located by identity, and `hidden` is checked as an attribute NAME
        # rather than as a substring of the raw tag: the substring form
        # would flip on any future attribute whose name merely contains
        # "hidden" (review M1).
        attrs, _body = _period_block(out, period.id)
        names = _attr_names(attrs)
        if period.id == mr.DEFAULT_PERIOD_ID:
            assert "hidden" not in names, f"default period {period.id!r} block is hidden"
        else:
            assert "hidden" in names, f"non-default period {period.id!r} block is not hidden"


def test_render_group_header_carries_group_colour_class():
    group = _group("/proj/a", [], css_class="group-color-13")
    period_windows = _ALL_NONE_PERIOD_WINDOWS
    out = dashboard_html.render_group_header(0, group, period_windows, lang="pt")
    assert "group-color-13" in out


def test_render_group_header_is_collapsed_by_default():
    group = _group("/proj/a", [])
    period_windows = _ALL_NONE_PERIOD_WINDOWS
    out = dashboard_html.render_group_header(0, group, period_windows, lang="pt")
    assert re.search(r'class="group-header collapsed\b', out)


def test_render_group_header_data_periods_lists_only_periods_with_scoped_sessions():
    # A single session dated 2026-01-01, evaluated as of 2026-08-21: it
    # falls outside "ontem", "desde-segunda", "15-dias" and "mes" (all of
    # which look back at most 30 days) but is always a member of "tudo".
    records = [
        {
            "session_id": "old",
            "project_path": "/proj/a",
            "timestamp_start": "2026-01-01T10:00:00Z",
            "timestamp_end": "2026-01-01T11:00:00Z",
            "input_tokens": 10,
            "output_tokens": 5,
        }
    ]
    group = _group("/proj/a", records)
    now = datetime.fromisoformat("2026-08-21T10:00:00+00:00")
    period_windows = {p.id: mr.resolve_period(p.id, now) for p in mr.PERIODS}
    out = dashboard_html.render_group_header(0, group, period_windows, lang="pt")
    match = re.search(r'data-periods="([^"]*)"', out)
    assert match
    assert match.group(1) == "tudo"


def test_render_group_header_no_project_path_uses_sem_projeto_label_and_no_title():
    group = _group(None, [], css_class="group-color-none")
    period_windows = _ALL_NONE_PERIOD_WINDOWS
    out = dashboard_html.render_group_header(0, group, period_windows, lang="pt")
    assert "sem projeto" in out
    assert "title=" not in out


# --------------------------------------------------------------------------
# render_detail_panel
# --------------------------------------------------------------------------


def _subagent(agent_type: str, timestamp: str, **overrides: Any) -> dict[str, Any]:
    subagent = {
        "agent_type": agent_type,
        "timestamp_start": timestamp,
        "input_tokens": 10,
        "output_tokens": 5,
        "tool_call_count": 2,
        "duration_seconds": 30,
    }
    subagent.update(overrides)
    return subagent


def test_render_detail_panel_renders_subagents_newest_first():
    subagents = [
        _subagent("first-agent", "2026-08-20T08:00:00Z", agent_label="oldest"),
        _subagent("second-agent", "2026-08-20T10:00:00Z", agent_label="newest"),
        _subagent("third-agent", "2026-08-20T09:00:00Z", agent_label="middle"),
    ]
    out = dashboard_html.render_detail_panel("sess-1", subagents, is_active=False, lang="pt")
    newest_pos = out.index("newest")
    middle_pos = out.index("middle")
    oldest_pos = out.index("oldest")
    assert newest_pos < middle_pos < oldest_pos


def test_render_detail_panel_reversing_mutation_would_be_caught():
    # Sanity companion to the ordering test above, expressed as an
    # explicit "not reverse order" check so the mutation in Step 3 has an
    # assertion that fails in the OPPOSITE direction too.
    subagents = [
        _subagent("a", "2026-08-20T08:00:00Z", agent_label="oldest"),
        _subagent("b", "2026-08-20T10:00:00Z", agent_label="newest"),
    ]
    out = dashboard_html.render_detail_panel("sess-1", subagents, is_active=False, lang="pt")
    assert out.index("newest") < out.index("oldest")


def test_render_detail_panel_zebra_stripes_odd_rows_only():
    # Found by the unnamed-mutation sweep (Step 4): nothing originally
    # pinned WHICH rows get "subagent-row-odd" versus plain "subagent-row",
    # only that both classes exist somewhere. Flipping the modulo
    # (`i % 2 == 1` -> `i % 2 == 0`) passed the full suite silently. Three
    # subagents (newest-first order: c, b, a) pins the full even/odd/even
    # pattern by row position, not just presence of both classes.
    subagents = [
        _subagent("a", "2026-08-20T08:00:00Z", agent_label="row-a"),
        _subagent("b", "2026-08-20T09:00:00Z", agent_label="row-b"),
        _subagent("c", "2026-08-20T10:00:00Z", agent_label="row-c"),
    ]
    out = dashboard_html.render_detail_panel("sess-1", subagents, is_active=False, lang="pt")
    # _div_classes locates rows by class membership instead of requiring
    # class to be the div's only attribute and immediately followed by `>`
    # (review M1). The first subagent-row is the column header, which the
    # zebra pattern deliberately doesn't count.
    rows = [cls for cls in _div_classes(out, "subagent-row") if "subagent-header" not in cls]
    assert rows == ["subagent-row", "subagent-row subagent-row-odd", "subagent-row"]


def test_render_detail_panel_no_subagents_renders_empty_state_without_raising():
    out = dashboard_html.render_detail_panel("sess-2", [], is_active=False, lang="pt")
    assert "Nenhum subagente nesta sessão" in out
    assert "subagent-row" not in out


def test_render_detail_panel_all_zero_token_subagents_renders_empty_state():
    # Subagents are filtered by record_token_total > 0 before the
    # empty-state check, so an all-zero list must also hit the empty path
    # rather than rendering a header with no rows under it.
    zero_subagents = [_subagent("ghost", "2026-08-20T08:00:00Z", input_tokens=0, output_tokens=0)]
    out = dashboard_html.render_detail_panel("sess-3", zero_subagents, is_active=False, lang="pt")
    assert "Nenhum subagente nesta sessão" in out


def test_render_detail_panel_opens_closed_by_default():
    subagents = [_subagent("a", "2026-08-20T08:00:00Z")]
    out_inactive = dashboard_html.render_detail_panel(
        "sess-1", subagents, is_active=False, lang="pt"
    )
    out_active = dashboard_html.render_detail_panel("sess-1", subagents, is_active=True, lang="pt")
    assert not re.search(r'class="detail-panel[^"]*\bopen\b', out_inactive)
    assert re.search(r'class="detail-panel[^"]*\bopen\b', out_active)


def test_render_detail_panel_no_inline_display_style():
    # Same inline-vs-class fight as render_session_row (see production
    # comment at lines 267-270): open/closed state must be the "open"
    # class, never an inline style, so it composes with .period-hidden.
    subagents = [_subagent("a", "2026-08-20T08:00:00Z")]
    for is_active in (True, False):
        out = dashboard_html.render_detail_panel(
            "sess-1", subagents, is_active=is_active, lang="pt"
        )
        assert 'style="display' not in out
        assert "style='display" not in out


@pytest.mark.parametrize(
    "agent_label",
    [None, ""],
)
def test_render_detail_panel_subagent_without_label_uses_fallback_text(agent_label):
    subagents = [_subagent("code-reviewer", "2026-08-20T08:00:00Z", agent_label=agent_label)]
    out = dashboard_html.render_detail_panel("sess-1", subagents, is_active=False, lang="pt")
    assert "code-reviewer (sem transcript salvo)" in out
    # No bare dash standing in for the missing label.
    assert "code-reviewer —" not in out


def test_render_detail_panel_subagent_with_label_uses_em_dash_form():
    subagents = [_subagent("general-purpose", "2026-08-20T08:00:00Z", agent_label="explore repo")]
    out = dashboard_html.render_detail_panel("sess-1", subagents, is_active=False, lang="pt")
    assert "general-purpose — explore repo" in out
    assert "sem transcript salvo" not in out


def test_render_detail_panel_period_ids_default_to_tudo():
    out = dashboard_html.render_detail_panel("sess-1", [], is_active=False, lang="pt")
    assert 'data-periods="tudo"' in out


def test_render_detail_panel_custom_period_ids_are_carried_through():
    out = dashboard_html.render_detail_panel(
        "sess-1", [], is_active=False, period_ids=("ontem", "mes"), lang="pt"
    )
    assert 'data-periods="ontem mes"' in out


def test_render_detail_panel_html_escapes_agent_type_and_label():
    subagents = [
        _subagent("<script>", "2026-08-20T08:00:00Z", agent_label="<b>bold</b>"),
    ]
    out = dashboard_html.render_detail_panel("sess-1", subagents, is_active=False, lang="pt")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert "&lt;b&gt;bold&lt;/b&gt;" in out
