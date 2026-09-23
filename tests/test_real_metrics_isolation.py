"""Guards the whole CLASS of defect behind the 2026-09-23 incident: a test
that calls into metrics_core/dashboard_tui/dashboard_html without isolating
HOME (or the module-level file constants directly) reads and, worse, WRITES
the author's real ~/.claude/metrics - real, irreplaceable session data and
label caches other live Claude sessions are appending to right now.

Concretely, two tests in tests/test_tui_unbound.py
(test_m_toggles_project_ranking_expanded_state,
test_second_g_switches_back_to_sessions_and_refocuses_the_tree) called
dashboard_tui.load_data() with no isolation at all. That was pre-existing
and harmless while save_label_cache did a plain overwrite; Task 5's
migration-plus-atomic-save made it damaging: the real task_labels.json/
agent_labels.json were rewritten in the new {id: {lang: label}} shape, with
a one-time task_labels.json.pre-i18n/agent_labels.json.pre-i18n backup, and
thousands of freshly regenerated "en": "__no_label__" entries. No labels
were lost and no model call happened (--label-budget defaults to 0), but
the files were touched and had to be restored from the pre-i18n backups by
hand.

This file is the guard for that whole class - not a fix for the two
specific tests (see tests/test_tui_unbound.py for that) and not the
constant-patching fix itself (see tests/conftest.py's autouse
_isolate_from_real_metrics_by_default fixture) - it is what proves the fix
actually applies to every test by default, present and future.

Reading the six module attributes above is entirely filesystem-free, so
test_no_metrics_file_constant_resolves_under_the_real_metrics_directory is
always safe, red or green. The TestRealClaudeDirGuard class below (A1: the
process-level fix, tests/conftest.py's sys.addaudithook guard) is
different: it deliberately attempts operations that WOULD touch the real
~/.claude if the guard ever failed - each one is constructed so that
failure mode is impossible (a canary path under a subdirectory that does
not exist, or a `claude` lookup with PATH pointed at an empty directory),
never an actual write or an actual launch of the real CLI.
"""

import subprocess
from pathlib import Path

import metrics_core as mr
import metrics_core.backfill as backfill_module
import metrics_core.labels as labels_module
import metrics_core.paths as paths_module
import pytest
from conftest import RealClaudeDirWrite

# Captured once, at collection time - before any fixture in this session
# (this file's own included) gets a chance to monkeypatch HOME or any of
# the six constants below. Path.home() reads $HOME at CALL time, so this
# has to run before anything rebinds it; module-level code in a test file
# runs at collection, which is as early as pytest offers.
_REAL_METRICS_DIR = (Path.home() / ".claude" / "metrics").resolve()
_REAL_CLAUDE_DIR = _REAL_METRICS_DIR.parent


def _watched_constants() -> dict[str, str]:
    """The exact six file-path constants the 2026-09-23 incident named:
    read from the module OBJECTS the production code actually calls at
    runtime (mr.METRICS_FILE etc.), not copied/re-imported values - a test
    that patched an unrelated name would not fool this."""
    return {
        "metrics_core.METRICS_FILE": mr.METRICS_FILE,
        "metrics_core.SUBAGENTS_FILE": mr.SUBAGENTS_FILE,
        "metrics_core.labels.TASK_LABELS_FILE": labels_module.TASK_LABELS_FILE,
        "metrics_core.labels.AGENT_LABELS_FILE": labels_module.AGENT_LABELS_FILE,
        "metrics_core.backfill.BACKFILL_STATE_FILE": backfill_module.BACKFILL_STATE_FILE,
        "metrics_core.paths.CONFIG_FILE": paths_module.CONFIG_FILE,
    }


def test_no_metrics_file_constant_resolves_under_the_real_metrics_directory() -> None:
    offenders = {
        name: value
        for name, value in _watched_constants().items()
        if Path(value).resolve().is_relative_to(_REAL_METRICS_DIR)
    }
    assert not offenders, (
        "these constants still resolve under the real, irreplaceable "
        f"{_REAL_METRICS_DIR} from inside a test: {offenders} - every test "
        "must be isolated from ~/.claude/metrics by default (see "
        "tests/conftest.py's autouse isolation fixture)"
    )


class TestRealClaudeDirGuard:
    """A1: tests/conftest.py's process-wide sys.addaudithook guard, which
    blocks a real write to (or a real `claude` CLI launch from) ~/.claude
    regardless of which metrics_core constant a test remembered to patch.

    Every "blocked" test here is built so the operation could NEVER reach
    the real filesystem/process even without the guard - see each test's
    own comment for why - so a guard regression fails loud (wrong
    exception type or no exception at all) instead of quietly touching
    real, irreplaceable data.
    """

    def test_write_mode_open_inside_the_real_claude_dir_is_blocked(self) -> None:
        # no-such-dir-canary does not exist under the real ~/.claude.
        # Without the guard, opening for append would raise plain
        # FileNotFoundError (missing parent directory) - it can never
        # succeed in creating anything, guard or no guard.
        canary = _REAL_CLAUDE_DIR / "no-such-dir-canary" / "x.jsonl"
        assert not canary.parent.exists()
        with pytest.raises(RealClaudeDirWrite):
            canary.open("a")
        assert not canary.parent.exists()

    def test_os_replace_into_the_real_claude_dir_is_blocked(self, tmp_path: Path) -> None:
        canary_dir = _REAL_CLAUDE_DIR / "no-such-dir-canary"
        assert not canary_dir.exists()
        src = tmp_path / "source.jsonl"
        src.write_text("{}\n")
        # Same shape as above: the destination's parent does not exist, so
        # without the guard this would raise FileNotFoundError, never
        # actually move anything. Path.replace() fires the same "os.rename"
        # audit event as os.replace() (verified in scratch).
        with pytest.raises(RealClaudeDirWrite):
            src.replace(canary_dir / "moved.jsonl")
        assert not canary_dir.exists()
        assert src.exists()  # never moved

    def test_launching_the_real_claude_cli_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # tmp_path is empty, so the real `claude` binary cannot be found on
        # this PATH: without the guard, Popen would raise FileNotFoundError
        # the moment it tries to resolve "claude" - it can never actually
        # exec the real CLI. The guard fires even earlier than that lookup
        # (confirmed in scratch: the subprocess.Popen audit event fires
        # before PATH resolution is attempted), which is why it wins the
        # race and raises RealClaudeDirWrite instead.
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(RealClaudeDirWrite):
            subprocess.Popen(["claude", "--version"])

    def test_reads_under_the_real_claude_dir_remain_allowed(self) -> None:
        # The guard must not be over-broad: a plain read-only open (or a
        # directory listing) under the real ~/.claude must never raise
        # RealClaudeDirWrite. ~/.claude may not exist at all on a bare CI
        # runner (only on the author's own machine), so this degrades to
        # "listing it didn't raise" when there is nothing to open.
        if not _REAL_CLAUDE_DIR.exists():
            return
        entries = list(_REAL_CLAUDE_DIR.iterdir())  # a read; must not raise
        for candidate in entries:
            if candidate.is_file():
                with candidate.open():  # read-mode open; must not raise
                    pass
                break

    def test_writes_to_tmp_path_are_unaffected(self, tmp_path: Path) -> None:
        target = tmp_path / "sessions.jsonl"
        target.write_text("{}\n")
        target.write_text("{}\n{}\n")
        renamed = tmp_path / "renamed.jsonl"
        target.rename(renamed)
        renamed.unlink()
