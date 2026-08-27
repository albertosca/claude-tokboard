import json
from datetime import datetime, timedelta
from typing import Any

import metrics_core as mr

WORKTREE = "/home/me/proj/.claude/worktrees/feature-x"


def record(
    project_path: str | None, timestamp_start: str | None = None, **tokens: int
) -> dict[str, Any]:
    r: dict[str, Any] = {"project_path": project_path}
    if timestamp_start is not None:
        r["timestamp_start"] = timestamp_start
    r.update(tokens)
    return r


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
    assert mr.project_display_label(None) == "sem projeto"
    assert mr.project_display_label("") == "sem projeto"


def test_project_display_label_is_the_basename():
    assert mr.project_display_label("/home/me/proj") == "proj"
    assert mr.project_display_label("/home/me/proj/") == "proj"


def test_project_display_label_names_a_worktree_after_its_parent():
    assert mr.project_display_label(WORKTREE) == "proj / feature-x"
    assert mr.project_display_label(WORKTREE + "/nested/deeper") == "proj / feature-x"


def test_project_display_label_ignores_a_worktree_marker_with_no_name_after_it():
    # The marker is present but nothing follows it, so there is no
    # worktree name to show - falls through to the plain basename.
    assert mr.project_display_label("/home/me/proj/.claude/worktrees/") == "worktrees"


def test_project_display_label_of_root_is_empty():
    # "/" rstrips to "", so both Path("").name and the `or normalized`
    # fallback are empty - the function returns "". Verified against the
    # real implementation before this test was written, not assumed.
    assert mr.project_display_label("/") == ""


def test_project_display_label_falls_through_when_marker_is_followed_by_a_slash():
    # The marker itself IS present (with its trailing "/"), but what
    # follows starts with another "/" - so remainder.split("/")[0] is
    # empty and worktree_name is falsy, taking the true branch of the
    # "if marker in normalized" check but the false branch of "if
    # worktree_name", falling through to the plain-basename return.
    # Verified against the real implementation before this test was
    # written, not assumed.
    assert mr.project_display_label("/home/me/proj/.claude/worktrees//nested") == "nested"


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
    assert mr.project_days_running({}, "/a") == ""
    assert mr.project_days_running({"/a": "2026-03-01T10:00:00"}, None) == ""


def test_project_days_running_is_empty_on_an_unparseable_timestamp():
    assert mr.project_days_running({"/a": "not a timestamp"}, "/a") == ""


def test_project_days_running_counts_days_since_first_use():
    ten_days_ago = (datetime.now().astimezone() - timedelta(days=10)).isoformat()
    assert mr.project_days_running({"/a": ten_days_ago}, "/a") == " · 1º uso há 10 dias"


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
    # No sessions_dir given - exercises the sessions_dir is None branch,
    # which must expand to ~/.claude/sessions. HOME is monkeypatched so
    # that expansion lands inside tmp_path instead of touching this
    # machine's real sessions directory, and the seeded file's cwd is
    # asserted exactly - a broken default (e.g. missing the ".claude"
    # segment, or landing outside tmp_path) would return set() instead.
    monkeypatch.setenv("HOME", str(tmp_path))
    sessions_dir = tmp_path / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "one.json").write_text(json.dumps({"cwd": "/home/me/somewhere"}))
    assert mr.get_open_projects() == {"/home/me/somewhere"}


def test_get_open_projects_on_a_glob_error_is_empty():
    # A path with an embedded null byte makes Path.glob raise ValueError
    # (verified against the real implementation before this test was
    # written) - the broad except Exception catches it and returns empty
    # rather than propagating.
    assert mr.get_open_projects("/tmp/does-not-exist\x00bad") == set()


# --- assign_group_colors ---------------------------------------------


def test_assign_group_colors_gives_each_project_its_own_slot():
    colors = mr.assign_group_colors([record("/a"), record("/b"), record("/a")])
    assert colors["/a"] == "group-color-1"
    assert colors["/b"] == "group-color-2"


def test_assign_group_colors_gives_a_worktree_its_parents_color():
    colors = mr.assign_group_colors([record("/home/me/proj"), record(WORKTREE)])
    assert colors[WORKTREE] == colors["/home/me/proj"]


def test_assign_group_colors_marks_a_pathless_record():
    colors = mr.assign_group_colors([record(None)])
    assert colors[None] == "group-color-none"


def test_assign_group_colors_wraps_after_thirty_slots():
    colors = mr.assign_group_colors([record(f"/p{i}") for i in range(31)])
    assert colors["/p0"] == "group-color-1"
    assert colors["/p29"] == "group-color-30"
    assert colors["/p30"] == "group-color-1"


# --- build_project_groups --------------------------------------------


def test_build_project_groups_groups_by_canonical_key_newest_first():
    # "/b" then "/a" - without the internal reversal, group order would
    # be ["/b", "/a"] (first-seen-first); asserting ["/a", "/b"] only
    # passes because the last record's group ("/a") leads.
    records = [record("/b", "1"), record("/a", "2")]
    colors = mr.assign_group_colors(records)
    groups = mr.build_project_groups(records, colors)
    assert [g["project"] for g in groups] == ["/a", "/b"]

    # A second record on "/a" must come back newest-first inside its
    # own group, not in input order.
    records_with_two_on_a = [record("/b", "1"), record("/a", "2"), record("/a", "3")]
    colors2 = mr.assign_group_colors(records_with_two_on_a)
    groups2 = mr.build_project_groups(records_with_two_on_a, colors2)
    assert len(groups2[0]["records"]) == 2
    assert [r["timestamp_start"] for r in groups2[0]["records"]] == ["3", "2"]


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
