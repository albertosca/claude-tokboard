"""load_project_roots: the declared list of directories a reader's projects
live in.

Every failure mode here resolves to the same answer - no roots - on
purpose. The config exists only to give legend entries better names, so a
dashboard that refused to render over a typo would be a worse trade than
one that names a few directories awkwardly. These tests pin that each of
those failure modes is actually REACHED, rather than trusting one broad
try/except to have covered them.
"""

import json
from pathlib import Path

import metrics_core as mr
import pytest


def _write(tmp_path: Path, content: str) -> str:
    config = tmp_path / "config.json"
    config.write_text(content)
    return str(config)


def test_a_list_of_roots_comes_back_as_given(tmp_path: Path) -> None:
    path = _write(tmp_path, json.dumps({"project_roots": ["/a/code", "/b/work"]}))
    assert mr.load_project_roots(path) == ["/a/code", "/b/work"]


def test_a_tilde_in_a_root_is_expanded_because_that_is_how_people_write_it(tmp_path: Path) -> None:
    path = _write(tmp_path, json.dumps({"project_roots": ["~/code"]}))
    assert mr.load_project_roots(path) == [str(Path("~/code").expanduser())]


def test_a_missing_file_means_no_roots(tmp_path: Path) -> None:
    assert mr.load_project_roots(str(tmp_path / "nope.json")) == []


def test_a_directory_where_the_file_should_be_means_no_roots(tmp_path: Path) -> None:
    # OSError rather than JSONDecodeError - a different except branch.
    assert mr.load_project_roots(str(tmp_path)) == []


def test_malformed_json_means_no_roots(tmp_path: Path) -> None:
    assert mr.load_project_roots(_write(tmp_path, "{not json")) == []


def test_a_json_document_that_is_not_an_object_means_no_roots(tmp_path: Path) -> None:
    assert mr.load_project_roots(_write(tmp_path, json.dumps(["/a/code"]))) == []


def test_a_config_without_the_key_means_no_roots(tmp_path: Path) -> None:
    assert mr.load_project_roots(_write(tmp_path, json.dumps({"other": 1}))) == []


def test_a_roots_value_that_is_not_a_list_means_no_roots(tmp_path: Path) -> None:
    assert mr.load_project_roots(_write(tmp_path, json.dumps({"project_roots": "/a/code"}))) == []


def test_non_string_and_empty_entries_are_dropped_not_fatal(tmp_path: Path) -> None:
    # One bad entry must not cost the reader the good ones beside it.
    path = _write(tmp_path, json.dumps({"project_roots": ["/a/code", "", 7, None, "/b/work"]}))
    assert mr.load_project_roots(path) == ["/a/code", "/b/work"]


def test_the_default_path_is_the_one_documented(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pins that the no-argument call reads CONFIG_FILE, not some other
    # path that happens to be missing in the test environment - which
    # would make every assertion above pass against a dead default.
    path = _write(tmp_path, json.dumps({"project_roots": ["/a/code"]}))
    monkeypatch.setattr(mr.paths, "CONFIG_FILE", path)
    assert mr.load_project_roots() == ["/a/code"]
