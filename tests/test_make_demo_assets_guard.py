"""tools/make-demo-assets.py's refuse_if_it_shows_real_data (and its two
helpers, _word_pattern and _plain_text) had no committed test (#13a) -
nothing proved the whole-word matching or the placeholder-stripped
vocabulary check kept working. tools/ is deliberately OUTSIDE the 100%
coverage gate (see that module's own docstring: it shells out to a real
browser and video encoder) - this file exists for correctness, not to
satisfy --cov, and must never be added to pyproject.toml's addopts.

Every "real name" used below is synthetic (zzz-real, projects, name) -
never one of the author's actual project names - fed in by monkeypatching
the tool's own mr_metrics/mr_subagents functions, which is what the
function under test calls to find out what a real denylist would contain.
"""

import importlib.util
import json
import types
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "tools" / "make-demo-assets.py"


@pytest.fixture
def tool() -> types.ModuleType:
    # The filename has a hyphen, so it cannot be imported with a normal
    # `import` statement - load it as a module by path instead, the same
    # way tests/test_metrics_log_hook.py loads hooks/metrics-log.py.
    spec = importlib.util.spec_from_file_location("make_demo_assets_guard", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_real_names(
    tool: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *names: str,
) -> None:
    """Point the tool's own real-name source at a synthetic metrics file -
    never the author's real ~/.claude/metrics - with one project_path per
    name given. mr_subagents() is pointed at a file that does not exist,
    which refuse_if_it_shows_real_data already treats as "no data there"."""
    metrics = tmp_path / "synthetic-metrics.jsonl"
    metrics.write_text(
        "\n".join(json.dumps({"project_path": f"/synthetic/{name}"}) for name in names) + "\n"
    )
    missing_subagents = tmp_path / "no-such-subagents.jsonl"
    monkeypatch.setattr(tool, "mr_metrics", lambda: str(metrics))
    monkeypatch.setattr(tool, "mr_subagents", lambda: str(missing_subagents))


def _write_frame(work: Path, index: int, text: str) -> None:
    (work / f"f{index:02d}.svg").write_text(f'<svg><text x="0" y="0">{text}</text></svg>')


class TestRefuseIfItShowsRealData:
    """refuse_if_it_shows_real_data(work, count, lang) - see
    tools/make-demo-assets.py for the full docstring of what it does and
    why (positive control, then a denylist built from real project names,
    minus whatever collides with this language's own UI vocabulary)."""

    def test_refuses_on_a_real_name_shown_as_a_whole_word(
        self, tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_real_names(tool, monkeypatch, tmp_path, "zzz-real")
        work = tmp_path / "work"
        work.mkdir()
        _write_frame(work, 0, "alpha zzz-real")

        with pytest.raises(SystemExit, match=r"frame 0 shows 1 real project name"):
            tool.refuse_if_it_shows_real_data(work, 1, "en")

    def test_passes_when_the_real_name_is_only_a_substring(
        self, tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # "zzz-realx" is not the whole word "zzz-real" - _word_pattern's
        # negative lookahead for a following \w must keep this from being
        # treated as a leak.
        _seed_real_names(tool, monkeypatch, tmp_path, "zzz-real")
        work = tmp_path / "work"
        work.mkdir()
        _write_frame(work, 0, "alpha zzz-realx")

        tool.refuse_if_it_shows_real_data(work, 1, "en")  # must not raise

    def test_ui_vocabulary_collision_is_reported_by_count_never_by_name(
        self,
        tool: types.ModuleType,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # "projects" is literal English UI chrome - metrics_core.MESSAGES's
        # own key.more_projects reads "More/fewer projects" - so a real
        # project happening to be named exactly that must not be able to
        # refuse every English frame forever.
        _seed_real_names(tool, monkeypatch, tmp_path, "projects")
        work = tmp_path / "work"
        work.mkdir()
        _write_frame(work, 0, "alpha More/fewer projects")

        tool.refuse_if_it_shows_real_data(work, 1, "en")  # must not raise

        out = capsys.readouterr().out
        assert "1 real name(s) skipped (collide with UI vocabulary)" in out
        assert "projects" not in out  # the count is printed, never the name

    def test_placeholder_only_appearance_does_not_excuse_the_name(
        self, tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # "name" appears in the English catalog ONLY inside the
        # notify.period template ("period: {name}"), stripped away by the
        # guard's own placeholder regex before the vocabulary check runs -
        # so a real project literally called "name" must still be caught.
        _seed_real_names(tool, monkeypatch, tmp_path, "name")
        work = tmp_path / "work"
        work.mkdir()
        _write_frame(work, 0, "alpha name")

        with pytest.raises(SystemExit, match=r"frame 0 shows 1 real project name"):
            tool.refuse_if_it_shows_real_data(work, 1, "en")

    def test_refuses_when_a_frame_names_no_fixture_project(
        self, tool: types.ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _seed_real_names(tool, monkeypatch, tmp_path)
        work = tmp_path / "work"
        work.mkdir()
        _write_frame(work, 0, "nothing recognizable here")

        with pytest.raises(SystemExit, match=r"did it render\?"):
            tool.refuse_if_it_shows_real_data(work, 1, "en")
