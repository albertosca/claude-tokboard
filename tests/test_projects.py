import json
from datetime import datetime, timedelta, timezone
from typing import Any

import metrics_core as mr
import pytest

WORKTREE = "/home/me/proj/.claude/worktrees/feature-x"


def record(
    project_path: str | None, timestamp_start: str | None = None, **tokens: int
) -> dict[str, Any]:
    data: dict[str, Any] = {"project_path": project_path}
    if timestamp_start is not None:
        data["timestamp_start"] = timestamp_start
    data.update(tokens)
    return data


# --- compute_project_first_seen / compute_project_last_seen ----------


def test_first_and_last_seen_track_the_extremes_per_project():
    records = [
        record("/a", "2026-03-05T10:00:00"),
        record("/a", "2026-03-01T10:00:00"),
        record("/a", "2026-03-09T10:00:00"),
        record("/b", "2026-03-07T10:00:00"),
    ]
    assert mr.compute_project_first_seen(records) == {
        "/a": "2026-03-01T10:00:00",
        "/b": "2026-03-07T10:00:00",
    }
    assert mr.compute_project_last_seen(records) == {
        "/a": "2026-03-09T10:00:00",
        "/b": "2026-03-07T10:00:00",
    }


def test_first_and_last_seen_skip_records_missing_either_field():
    records = [
        record(None, "2026-03-05T10:00:00"),
        record("/a"),
        record("/a", ""),
        record("/a", "2026-03-05T10:00:00"),
    ]
    assert mr.compute_project_first_seen(records) == {"/a": "2026-03-05T10:00:00"}
    assert mr.compute_project_last_seen(records) == {"/a": "2026-03-05T10:00:00"}


# --- project_display_label -------------------------------------------


def test_project_display_label_falls_back_when_there_is_no_path():
    assert mr.project_display_label(None, lang="pt") == "sem projeto"
    assert mr.project_display_label("", lang="pt") == "sem projeto"


def test_project_display_label_is_the_basename():
    assert mr.project_display_label("/home/me/proj", lang="pt") == "proj"
    assert mr.project_display_label("/home/me/proj/", lang="pt") == "proj"


def test_project_display_label_names_a_worktree_after_its_parent():
    assert mr.project_display_label(WORKTREE, lang="pt") == "proj / feature-x"
    assert mr.project_display_label(WORKTREE + "/nested/deeper", lang="pt") == "proj / feature-x"


def test_project_display_label_ignores_a_worktree_marker_with_no_name_after_it():
    # The marker is present but nothing follows it, so there is no
    # worktree name to show - falls through to the plain basename.
    assert mr.project_display_label("/home/me/proj/.claude/worktrees/", lang="pt") == "worktrees"


def test_project_display_label_of_root_is_empty():
    # "/" rstrips to "", so both Path("").name and the `or normalized`
    # fallback are empty - the function returns "". Verified against the
    # real implementation before this test was written, not assumed.
    assert mr.project_display_label("/", lang="pt") == ""


def test_project_display_label_falls_through_when_marker_is_followed_by_a_slash():
    # The marker itself IS present (with its trailing "/"), but what
    # follows starts with another "/" - so remainder.split("/")[0] is
    # empty and worktree_name is falsy, taking the true branch of the
    # "if marker in normalized" check but the false branch of "if
    # worktree_name", falling through to the plain-basename return.
    # Verified against the real implementation before this test was
    # written, not assumed.
    assert (
        mr.project_display_label("/home/me/proj/.claude/worktrees//nested", lang="pt") == "nested"
    )


# --- color_group_key -------------------------------------------------


def test_color_group_key_passes_through_a_falsy_path():
    assert mr.color_group_key(None) is None
    assert mr.color_group_key("") == ""


def test_color_group_key_is_the_path_itself_for_a_normal_project():
    assert mr.color_group_key("/home/me/proj") == "/home/me/proj"


def test_color_group_key_of_a_worktree_is_its_parent_project():
    assert mr.color_group_key(WORKTREE) == "/home/me/proj"


# --- project_days_running --------------------------------------------


def test_project_days_running_is_empty_without_a_first_seen():
    assert mr.project_days_running({}, "/a", lang="pt") == ""
    assert mr.project_days_running({"/a": "2026-03-01T10:00:00"}, None, lang="pt") == ""


def test_project_days_running_is_empty_on_an_unparseable_timestamp():
    assert mr.project_days_running({"/a": "not a timestamp"}, "/a", lang="pt") == ""


def test_project_days_running_counts_days_since_first_use():
    ten_days_ago = (datetime.now().astimezone() - timedelta(days=10)).isoformat()
    assert mr.project_days_running({"/a": ten_days_ago}, "/a", lang="pt") == " · 1º uso há 10 dias"


@pytest.mark.parametrize(
    ("days_ago", "english_text", "portuguese_text"),
    [
        (0, " · first used today", " · 1º uso hoje"),
        (1, " · first used 1 day ago", " · 1º uso há 1 dia"),
        (2, " · first used 2 days ago", " · 1º uso há 2 dias"),
    ],
)
def test_project_days_running_zero_and_small_counts_in_both_languages(
    days_ago: int, english_text: str, portuguese_text: str
) -> None:
    # A few seconds' slack keeps `.days` from landing on the boundary below
    # the intended count (e.g. 1 day minus a few ms truncating to 0).
    first_seen = (datetime.now().astimezone() - timedelta(days=days_ago, seconds=5)).isoformat()
    assert mr.project_days_running({"/a": first_seen}, "/a", lang="en") == english_text
    assert mr.project_days_running({"/a": first_seen}, "/a", lang="pt") == portuguese_text


def test_project_days_running_treats_a_future_first_seen_as_today() -> None:
    # Clock skew: first_seen recorded in the future must never print
    # "-1 days" - it renders exactly like a first use today.
    tomorrow = (datetime.now().astimezone() + timedelta(days=1)).isoformat()
    assert mr.project_days_running({"/a": tomorrow}, "/a", lang="en") == " · first used today"
    assert mr.project_days_running({"/a": tomorrow}, "/a", lang="pt") == " · 1º uso hoje"


# --- project_days_running counts CALENDAR days, not elapsed 24h periods
# (Package B follow-up, controller-fixed Minor): a project first used at
# 23:00 yesterday must read "1 day ago" as soon as the clock crosses
# midnight, not still "today" until a full 24h has elapsed. `now` is
# injected here (keyword-only, defaults to real datetime.now() when
# omitted - production callers never pass it) so these boundary cases
# don't depend on when the suite happens to run.


def test_project_days_running_seen_late_yesterday_is_one_day_ago_after_midnight() -> None:
    first_seen = "2026-03-14T23:59:00"
    just_after_midnight = datetime(2026, 3, 15, 0, 1, 0).astimezone()
    assert (
        mr.project_days_running({"/a": first_seen}, "/a", lang="en", now=just_after_midnight)
        == " · first used 1 day ago"
    )
    assert (
        mr.project_days_running({"/a": first_seen}, "/a", lang="pt", now=just_after_midnight)
        == " · 1º uso há 1 dia"
    )


def test_project_days_running_seen_just_after_midnight_is_still_today_at_end_of_day() -> None:
    first_seen = "2026-03-15T00:01:00"
    just_before_midnight = datetime(2026, 3, 15, 23, 59, 0).astimezone()
    assert (
        mr.project_days_running({"/a": first_seen}, "/a", lang="en", now=just_before_midnight)
        == " · first used today"
    )
    assert (
        mr.project_days_running({"/a": first_seen}, "/a", lang="pt", now=just_before_midnight)
        == " · 1º uso hoje"
    )


def test_project_days_running_counts_calendar_days_crossed_not_hours_elapsed() -> None:
    # First seen 2 CALENDAR days back at 23:59, viewed the next-next
    # morning at 00:01 - barely over 24h elapsed, but 2 midnights crossed.
    first_seen = "2026-03-13T23:59:00"
    just_after_midnight = datetime(2026, 3, 15, 0, 1, 0).astimezone()
    assert (
        mr.project_days_running({"/a": first_seen}, "/a", lang="en", now=just_after_midnight)
        == " · first used 2 days ago"
    )
    assert (
        mr.project_days_running({"/a": first_seen}, "/a", lang="pt", now=just_after_midnight)
        == " · 1º uso há 2 dias"
    )


# --- get_open_projects -----------------------------------------------


def test_get_open_projects_on_a_missing_directory_is_empty(tmp_path):
    assert mr.get_open_projects(str(tmp_path / "does-not-exist")) == set()


def test_get_open_projects_collects_each_sessions_cwd(tmp_path):
    (tmp_path / "one.json").write_text(json.dumps({"cwd": "/home/me/a"}))
    (tmp_path / "two.json").write_text(json.dumps({"cwd": "/home/me/b"}))
    assert mr.get_open_projects(str(tmp_path)) == {"/home/me/a", "/home/me/b"}


def test_get_open_projects_skips_unreadable_and_cwd_less_entries(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps({"cwd": "/home/me/a"}))
    (tmp_path / "corrupt.json").write_text("{not json")
    (tmp_path / "no-cwd.json").write_text(json.dumps({"other": "field"}))
    (tmp_path / "empty-cwd.json").write_text(json.dumps({"cwd": ""}))
    (tmp_path / "ignored.txt").write_text("not a json file at all")
    assert mr.get_open_projects(str(tmp_path)) == {"/home/me/a"}


def test_get_open_projects_resolves_the_real_default_sessions_directory(monkeypatch, tmp_path):
    # No sessions_directory given - exercises the sessions_directory is None branch,
    # which must expand to ~/.claude/sessions. HOME is monkeypatched so
    # that expansion lands inside tmp_path instead of touching this
    # machine's real sessions directory, and the seeded file's cwd is
    # asserted exactly - a broken default (e.g. missing the ".claude"
    # segment, or landing outside tmp_path) would return set() instead.
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_directory = tmp_path / ".claude" / "sessions"
    sessions_directory.mkdir(parents=True)
    (sessions_directory / "one.json").write_text(json.dumps({"cwd": "/home/me/somewhere"}))
    assert mr.get_open_projects() == {"/home/me/somewhere"}


def test_get_open_projects_on_a_glob_error_is_empty():
    # A path with an embedded null byte makes Path.glob raise ValueError
    # (verified against the real implementation before this test was
    # written) - the broad except Exception catches it and returns empty
    # rather than propagating.
    assert mr.get_open_projects("/tmp/does-not-exist\x00bad") == set()


# --- assign_group_colors ---------------------------------------------


def test_assign_group_colors_gives_each_project_its_own_slot():
    colors = mr.assign_group_colors(
        [record("/a", input_tokens=2), record("/b", input_tokens=1), record("/a", input_tokens=2)]
    )
    assert colors["/a"] == "group-color-1"
    assert colors["/b"] == "group-color-2"


def test_assign_group_colors_gives_a_worktree_its_parents_color():
    colors = mr.assign_group_colors([record("/home/me/proj"), record(WORKTREE)])
    assert colors[WORKTREE] == colors["/home/me/proj"]


def test_assign_group_colors_marks_a_pathless_record():
    colors = mr.assign_group_colors([record(None)])
    assert colors[None] == "group-color-none"


# --- assign_project_slots: the eight colours go to the busiest projects ---
#
# 2026-10-05 review: thirty first-appearance slots cycled through a hue
# sweep whose neighbours were indistinguishable (normal-vision delta E 1.6).
# Eight validated colours now go to the projects with the most spend in the
# last 30 days; free slots go to the biggest all-time spenders; everything
# else is "others".

SLOT_NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
RECENT = "2026-10-01T10:00:00+00:00"
OLD = "2026-06-01T10:00:00+00:00"


def test_assign_project_slots_gives_the_eight_slots_to_the_biggest_recent_spenders():
    records = [record(f"/p{index}", RECENT, input_tokens=100 + index) for index in range(10)]
    slots = mr.assign_project_slots(records, SLOT_NOW)
    assert slots == {f"/p{index}": 9 - index for index in range(2, 10)}


def test_assign_group_colors_folds_projects_without_a_slot_into_others():
    records = [record(f"/p{index}", RECENT, input_tokens=100 + index) for index in range(10)]
    colors = mr.assign_group_colors(records, now=SLOT_NOW)
    assert colors["/p9"] == "group-color-1"
    assert colors["/p2"] == "group-color-8"
    assert colors["/p1"] == colors["/p0"] == "group-color-other"


def test_assign_project_slots_fills_free_slots_by_all_time_spend():
    # Review Focus 1: three recent projects must not leave five slots empty
    # while big old projects turn gray.
    records = [
        record("/recent-small", RECENT, input_tokens=10),
        record("/recent-big", RECENT, input_tokens=30),
        record("/recent-mid", RECENT, input_tokens=20),
        *[record(f"/old{index}", OLD, input_tokens=1000 * (index + 1)) for index in range(6)],
    ]
    slots = mr.assign_project_slots(records, SLOT_NOW)
    assert [slots["/recent-big"], slots["/recent-mid"], slots["/recent-small"]] == [0, 1, 2]
    assert [slots[f"/old{index}"] for index in (5, 4, 3, 2, 1)] == [3, 4, 5, 6, 7]
    assert "/old0" not in slots


def test_assign_project_slots_ranks_recent_spend_not_lifetime_spend():
    records = [
        record("/old-giant", OLD, input_tokens=1_000_000),
        record("/old-giant", RECENT, input_tokens=1),
        record("/new-busy", RECENT, input_tokens=500),
    ]
    slots = mr.assign_project_slots(records, SLOT_NOW, slot_count=1)
    assert slots == {"/new-busy": 0}


def test_assign_project_slots_never_gives_a_slot_to_no_project_and_shares_it_with_worktrees():
    # Review Focus 2.
    records = [
        record(None, RECENT, input_tokens=10_000),
        record("/home/me/proj", RECENT, input_tokens=5),
        record(WORKTREE, RECENT, input_tokens=5),
    ]
    slots = mr.assign_project_slots(records, SLOT_NOW)
    assert slots == {"/home/me/proj": 0}
    colors = mr.assign_group_colors(records, now=SLOT_NOW)
    assert colors[WORKTREE] == colors["/home/me/proj"] == "group-color-1"
    assert colors[None] == "group-color-none"


def test_assign_project_slots_counts_an_undated_record_only_in_the_all_time_ranking():
    # Review Focus 5: no timestamp_start must not crash, and must not count
    # as recent spend.
    records = [
        record("/undated", None, input_tokens=1_000),
        record("/recent", RECENT, input_tokens=1),
    ]
    slots = mr.assign_project_slots(records, SLOT_NOW, slot_count=1)
    assert slots == {"/recent": 0}
    assert mr.assign_project_slots(records, SLOT_NOW, slot_count=2) == {"/recent": 0, "/undated": 1}


def test_assign_project_slots_breaks_ties_by_name_so_colours_are_deterministic():
    records = [record("/b", RECENT, input_tokens=5), record("/a", RECENT, input_tokens=5)]
    assert mr.assign_project_slots(records, SLOT_NOW) == {"/a": 0, "/b": 1}


# --- build_project_groups --------------------------------------------


def test_build_project_groups_groups_by_canonical_key_newest_first():
    # "/b" then "/a" - without the internal reversal, group order would
    # be ["/b", "/a"] (first-seen-first); asserting ["/a", "/b"] only
    # passes because the last record's group ("/a") leads.
    records = [record("/b", "1"), record("/a", "2")]
    colors = mr.assign_group_colors(records)
    groups = mr.build_project_groups(records, colors)
    assert [group["project"] for group in groups] == ["/a", "/b"]

    # A second record on "/a" must come back newest-first inside its
    # own group, not in input order.
    records_with_two_on_first_project = [
        record("/b", "1"),
        record("/a", "2"),
        record("/a", "3"),
    ]
    colors2 = mr.assign_group_colors(records_with_two_on_first_project)
    groups2 = mr.build_project_groups(records_with_two_on_first_project, colors2)
    assert len(groups2[0]["records"]) == 2
    assert [entry["timestamp_start"] for entry in groups2[0]["records"]] == ["3", "2"]


def test_build_project_groups_merges_a_worktree_into_its_parent():
    records = [record("/home/me/proj", "1"), record(WORKTREE, "2")]
    colors = mr.assign_group_colors(records)
    groups = mr.build_project_groups(records, colors)
    assert len(groups) == 1
    assert groups[0]["project"] == "/home/me/proj"
    assert len(groups[0]["records"]) == 2


def test_build_project_groups_falls_back_when_a_color_is_missing():
    records = [record("/a", "1")]
    groups = mr.build_project_groups(records, {})
    assert groups[0]["class"] == "group-color-none"


def test_build_project_groups_colors_a_worktree_only_group():
    # The canonical key ("/home/me/proj") is never itself a key in
    # group_colors when every session ran inside a worktree - the lookup
    # goes through a member's own raw path instead.
    records = [record(WORKTREE, "1")]
    colors = mr.assign_group_colors(records)
    groups = mr.build_project_groups(records, colors)
    assert groups[0]["class"] == colors[WORKTREE]
    assert groups[0]["class"] != "group-color-none"


# --- the second worktree marker --------------------------------------
#
# `git worktree add` has no opinion about where worktrees live. Claude
# Code's own command puts them under `<root>/.claude/worktrees/`, which is
# the only shape recognised until now, but a plain `.worktrees/` at the
# repo root is just as common - and a worktree there was showing up in the
# legend under its own bare name, reading like a subagent rather than a
# branch of a project (reported 2026-09-21).

PLAIN_WORKTREE = "/home/me/proj/.worktrees/feature-x"


def test_project_display_label_names_a_plain_worktree_after_its_parent_too():
    assert mr.project_display_label(PLAIN_WORKTREE, lang="pt") == "proj / feature-x"
    assert (
        mr.project_display_label(PLAIN_WORKTREE + "/nested/deeper", lang="pt") == "proj / feature-x"
    )


def test_color_group_key_of_a_plain_worktree_is_its_parent_project():
    assert mr.color_group_key(PLAIN_WORKTREE) == "/home/me/proj"


def test_a_directory_merely_named_worktrees_is_not_a_worktree_marker():
    # The marker is a path SEGMENT. A project whose own name ends in
    # ".worktrees", or a file called that, must not be split.
    assert mr.project_display_label("/home/me/my.worktrees", lang="pt") == "my.worktrees"
    assert mr.color_group_key("/home/me/my.worktrees") == "/home/me/my.worktrees"


def test_the_claude_marker_still_wins_inside_a_plain_worktrees_directory():
    # Both markers present: the Claude one is more specific and is what
    # the path was actually created by, so it decides.
    both = "/home/me/proj/.worktrees/a/.claude/worktrees/b"
    assert mr.project_display_label(both, lang="pt") == "a / b"


def test_assign_group_colors_ranks_by_ranking_records_and_still_colours_every_path():
    # The HTML dashboard colours paths from two feeds (session snapshots and
    # their per-day deltas); ranking both would count every token twice and
    # rank a delta-only path at half weight. Spend comes from ranking_records.
    snapshots = [record("/a", RECENT, input_tokens=10), record("/b", RECENT, input_tokens=20)]
    deltas = [record("/a", RECENT, input_tokens=10_000), record(WORKTREE, RECENT, input_tokens=1)]
    colors = mr.assign_group_colors(snapshots + deltas, now=SLOT_NOW, ranking_records=snapshots)
    assert colors["/b"] == "group-color-1"
    assert colors["/a"] == "group-color-2"
    assert colors[WORKTREE] == "group-color-other"


def test_assign_project_slots_never_spends_a_slot_on_a_project_without_tokens():
    # Whole-branch review (2026-10-05): a project opened in the last 30 days
    # that recorded nothing ranked ahead of every all-time spender and
    # pushed a real one into "others".
    records = [record("/zero", RECENT, input_tokens=0)] + [
        record(f"/old{index}", OLD, input_tokens=100 + index) for index in range(8)
    ]
    slots = mr.assign_project_slots(records, SLOT_NOW)
    assert "/zero" not in slots
    assert sorted(slots) == sorted(f"/old{index}" for index in range(8))


@pytest.mark.parametrize("timestamp", [12345, ["2026-10-01"], {"at": "now"}])
def test_assign_project_slots_survives_a_non_string_timestamp(timestamp: Any) -> None:
    # A malformed row must not take the whole dashboard down; it simply
    # does not count as recent (session_date tolerates the same shapes).
    records = [record("/a", None, input_tokens=5), record("/b", RECENT, input_tokens=1)]
    records[0]["timestamp_start"] = timestamp
    assert mr.assign_project_slots(records, SLOT_NOW, slot_count=1) == {"/b": 0}


def test_assign_project_slots_survives_a_timestamp_outside_the_calendar():
    # fromisoformat accepts year 1 and 9999; converting them to local time
    # overflows. A corrupted row must not take the render down (2026-10-05
    # review of the whole-branch residuals).
    records = [
        {"project_path": "/p/a", "input_tokens": 5, "timestamp_start": "0001-01-01T00:00:00"},
        {"project_path": "/p/b", "input_tokens": 5, "timestamp_start": "9999-12-31T23:59:59"},
    ]
    assert set(mr.assign_project_slots(records, SLOT_NOW)) == {"/p/a", "/p/b"}


def test_assign_project_slots_skips_a_no_project_row_before_reading_its_tokens():
    records = [{"project_path": None, "input_tokens": "5"}]
    assert mr.assign_project_slots(records, SLOT_NOW) == {}
