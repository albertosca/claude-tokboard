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

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import conftest
import metrics_core as mr
import metrics_core.backfill as backfill_module
import metrics_core.labels as labels_module
import metrics_core.paths as paths_module
import pytest
from conftest import RealClaudeDirWrite


def _assert_guard_hit_and_clear_it() -> None:
    """Fix round 1, Important 1(b): every intentional trigger of the guard
    in this file must both assert on the exception (already done via
    pytest.raises) AND confirm it was RECORDED - proving the guard's own
    hit-tracking is what a downstream `except BaseException:` would have to
    defeat, not just the exception type - then clear it, or the autouse
    _fail_loudly_on_swallowed_guard_hits fixture in conftest.py fails this
    otherwise-passing test at teardown."""
    assert conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS
    conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()


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
        _assert_guard_hit_and_clear_it()

    def test_os_replace_into_the_real_claude_dir_is_blocked(self, tmp_path: Path) -> None:
        canary_directory = _REAL_CLAUDE_DIR / "no-such-dir-canary"
        assert not canary_directory.exists()
        source = tmp_path / "source.jsonl"
        source.write_text("{}\n")
        # Same shape as above: the destination's parent does not exist, so
        # without the guard this would raise FileNotFoundError, never
        # actually move anything. Path.replace() fires the same "os.rename"
        # audit event as os.replace() (verified in scratch).
        with pytest.raises(RealClaudeDirWrite):
            source.replace(canary_directory / "moved.jsonl")
        assert not canary_directory.exists()
        assert source.exists()  # never moved
        _assert_guard_hit_and_clear_it()

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
        _assert_guard_hit_and_clear_it()

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


class TestClaudeLaunchMatchingIsPrecise:
    """Fix round 1, M3: _launches_real_claude_cli must match the ACTUAL
    command being run, not any argument that merely mentions "claude" -
    the earlier version scanned every argv position and would have
    blocked ["echo", "claude"]. Every "blocked" case empties PATH first
    (same safe-canary shape as test_launching_the_real_claude_cli_is_
    blocked above), so a guard regression can only ever surface as
    FileNotFoundError, never a real launch of anything.
    """

    @pytest.mark.parametrize(
        "argv",
        [
            ["claude", "--version"],
            ["/nonexistent/bin/claude", "-p", "x"],
            ["nice", "-n", "15", "claude", "-p", "x"],
            [b"claude"],
            [Path("claude")],
            ["sh", "-c", "claude -p x"],
            ["/usr/bin/env", "claude"],
        ],
        ids=[
            "bare-claude",
            "absolute-path",
            "nice-wrapper",
            "bytes-argv0",
            "pathlike-argv0",
            "sh-dash-c",
            "env-wrapper",
        ],
    )
    def test_blocks_every_real_launch_shape(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        argv: list[str | bytes | Path],
    ) -> None:
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(RealClaudeDirWrite):
            subprocess.Popen(argv)
        _assert_guard_hit_and_clear_it()

    def test_blocks_shell_true(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # shell=True: CPython itself normalizes this to ["/bin/sh", "-c",
        # "claude --version"] before the audit event fires (confirmed in
        # scratch) - covered by the same code path as sh-dash-c above, but
        # exercised via the actual kwarg a caller would really use.
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(RealClaudeDirWrite):
            subprocess.Popen("claude --version", shell=True)
        _assert_guard_hit_and_clear_it()

    def test_blocks_nice_inside_a_shell_true_command(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(RealClaudeDirWrite):
            subprocess.Popen("nice -n 15 claude -p x", shell=True)
        _assert_guard_hit_and_clear_it()

    def test_blocks_an_executable_kwarg_that_differs_from_argv0(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Popen lets `executable` differ from argv[0] entirely - the real
        # command run is `executable`, regardless of what argv[0] says.
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(RealClaudeDirWrite):
            subprocess.Popen(["x"], executable="claude")
        _assert_guard_hit_and_clear_it()

    @pytest.mark.parametrize(
        "argv",
        [
            ["/bin/echo", "claude"],
            ["/usr/bin/grep", "-c", "claude", "/dev/null"],
            ["/bin/echo", "x/claude"],
            ["claudette"],
        ],
        ids=["echo-claude", "grep-claude", "echo-path-with-claude", "claudette-lookalike"],
    )
    def test_does_not_block_claude_as_a_mere_argument_or_lookalike(self, argv: list[str]) -> None:
        # Real, harmless commands actually run (echo/grep do nothing to
        # any file); "claudette" doesn't exist on a bare test machine, so
        # THAT one is a plain FileNotFoundError - the only thing under
        # test is that RealClaudeDirWrite is never the one raised.
        try:
            subprocess.run(argv, capture_output=True)
        except RealClaudeDirWrite:
            pytest.fail(f"{argv} was wrongly blocked as a claude CLI launch")
        except OSError:
            pass


class TestUnguardedPythonChildIsBlocked:
    """Fix round 1, Important 2: sys.addaudithook installs are per-process
    - a subprocess.Popen'd Python child runs in its OWN process, and this
    guard cannot see a single thing it does there, no matter what it
    writes. The only thing checkable from the parent side is whether the
    child's env redirects HOME away from the real one before it starts.

    Every "blocked" case here is safe even if the guard regressed: it
    fires (aborting the whole Popen) BEFORE the child process is ever
    created, so the child's own code (a harmless `pass`) never actually
    runs either way.
    """

    def test_a_python_child_with_no_env_override_is_blocked(self) -> None:
        # No env= at all: the child would inherit this process's own REAL
        # HOME - exactly the 2026-09-23-shaped incident this closes.
        with pytest.raises(RealClaudeDirWrite):
            subprocess.Popen([sys.executable, "-c", "pass"])
        _assert_guard_hit_and_clear_it()

    def test_a_python_child_with_env_missing_home_is_blocked(self) -> None:
        # env IS given, but has no "HOME" key at all - Popen's env
        # REPLACES the child's whole environment rather than merging with
        # the parent's, so this is just as unguarded as no env at all.
        with pytest.raises(RealClaudeDirWrite):
            subprocess.Popen([sys.executable, "-c", "pass"], env={"PATH": "/usr/bin"})
        _assert_guard_hit_and_clear_it()

    def test_a_python_child_with_home_redirected_to_a_tmp_dir_is_allowed(
        self, tmp_path: Path
    ) -> None:
        # The exact shape tests/conftest.py's own dashboard_page fixture
        # and tests/test_language_leak.py's fixture_page_html already use
        # - must keep working, or every existing sys.executable child in
        # this suite would start failing.
        environment = dict(os.environ, HOME=str(tmp_path))
        process = subprocess.Popen([sys.executable, "-c", "pass"], env=environment)
        assert process.wait(timeout=10) == 0

    def test_a_non_python_child_is_unaffected_by_this_check(self) -> None:
        # "echo" is not a Python interpreter, so _is_python_interpreter
        # never matches it, regardless of env - a real system binary
        # actually runs here to prove that.
        process = subprocess.Popen(["/bin/echo", "hi"], stdout=subprocess.DEVNULL)
        assert process.wait(timeout=10) == 0


class TestCaseAndSymlinkEscapes:
    """Fix round 1, M1: two ways a write could reach the real ~/.claude
    while a naive resolve()-based, case-sensitive comparison sees nothing
    wrong."""

    def test_a_case_variant_path_is_blocked_on_a_case_insensitive_filesystem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # macOS's default filesystem (APFS) is case-insensitive but
        # CASE-PRESERVING: ~/.CLAUDE and ~/.claude name the exact same
        # physical directory there, even though Path.resolve() does not
        # correct a caller's case to match what's really on disk. Forced
        # to "darwin" so this test's result never depends on which OS
        # actually runs the suite.
        monkeypatch.setattr(sys, "platform", "darwin")
        canary = Path(str(_REAL_CLAUDE_DIR).swapcase()) / "no-such-dir-canary" / "x.jsonl"
        # Neither the real-case nor the case-swapped canary directory
        # exists - this can never succeed in creating anything, guard or
        # no guard, exactly like the plain-case canary tests above.
        assert not canary.parent.exists()
        with pytest.raises(RealClaudeDirWrite):
            canary.open("a")
        assert not canary.parent.exists()
        _assert_guard_hit_and_clear_it()

    def test_a_case_variant_path_is_not_specially_matched_off_darwin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Proves the platform check above actually GATES something: on a
        # case-SENSITIVE platform, a case-swapped path is a genuinely
        # different (and here, nonexistent) location, so this must fail
        # with a plain FileNotFoundError - exactly as it would with no
        # guard installed at all - not RealClaudeDirWrite.
        monkeypatch.setattr(sys, "platform", "linux")
        canary = Path(str(_REAL_CLAUDE_DIR).swapcase()) / "no-such-dir-canary" / "x.jsonl"
        with pytest.raises(FileNotFoundError):
            canary.open("a")

    def test_a_symlink_inside_the_guarded_dir_pointing_outside_is_still_blocked_lexically(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Never touches the real ~/.claude: the guard's own captured
        # directories are redirected to a throwaway FAKE one for this
        # test (the same seam Important 1's hook test uses), so the
        # symlink this test creates lives entirely under tmp_path.
        # All scaffolding (including the symlink itself) is built BEFORE
        # the guard's own captured directories are redirected below - a
        # test setup step, not the operation under test, so it must not
        # itself trip the (not-yet-installed-here) guard.
        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        outside_target = tmp_path / "outside-target"
        outside_target.mkdir()
        link = fake_claude_directory / "skills"
        link.symlink_to(outside_target)  # a symlink INSIDE the fake guarded dir...
        target = link / "escape.md"  # ...pointing OUTSIDE it - resolve() alone would miss this

        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)

        with pytest.raises(RealClaudeDirWrite):
            target.open("a")
        assert not target.exists()
        assert not (outside_target / "escape.md").exists()
        _assert_guard_hit_and_clear_it()


class TestRunningCheckoutIsExempt:
    """Fix round 1, M4: this repo is a Claude Code plugin - a real install
    can land it at ~/.claude/plugins/cache/<name>/, nested INSIDE the very
    directory this guard protects. Python's own import machinery writes a
    .pyc bytecode cache for every module it imports; without an exemption
    for the checkout's own tree, merely running the suite from such a
    location would crash on the first import of one of this repo's own
    modules (confirmed in scratch - see the fix-round report).
    """

    def test_a_write_inside_the_running_checkouts_own_tree_is_exempt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_claude_directory = tmp_path / ".claude"
        fake_repo_root = fake_claude_directory / "plugins" / "cache" / "fake-claude-tokboard"
        fake_repo_root.mkdir(parents=True)
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        monkeypatch.setattr(conftest, "REPO_ROOT", fake_repo_root)

        pycache_file = fake_repo_root / "some_module" / "__pycache__" / "x.cpython-314.pyc"
        pycache_file.parent.mkdir(parents=True)
        pycache_file.write_text("not real bytecode")  # must NOT raise

        pytest_cache_file = fake_repo_root / ".pytest_cache" / "v" / "cache" / "lastfailed"
        pytest_cache_file.parent.mkdir(parents=True)
        pytest_cache_file.write_text("{}")  # must NOT raise

        (fake_repo_root / ".coverage").write_text("")  # must NOT raise

    def test_a_write_elsewhere_inside_the_fake_guarded_dir_is_still_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_claude_directory = tmp_path / ".claude"
        fake_repo_root = fake_claude_directory / "plugins" / "cache" / "fake-claude-tokboard"
        fake_repo_root.mkdir(parents=True)
        # Real user data, NOT inside the checkout's own tree - built BEFORE
        # the guard is redirected below, same reasoning as the symlink test
        # above. The exemption must stay narrow, not become a blanket
        # bypass of the whole fake ~/.claude just because it happens to
        # also hold this checkout somewhere inside it.
        metrics_directory = fake_claude_directory / "metrics"
        metrics_directory.mkdir()

        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        monkeypatch.setattr(conftest, "REPO_ROOT", fake_repo_root)

        with pytest.raises(RealClaudeDirWrite):
            (metrics_directory / "sessions.jsonl").write_text("x")
        assert not (metrics_directory / "sessions.jsonl").exists()
        _assert_guard_hit_and_clear_it()


class TestGuardHitsSurviveExceptClauses:
    """Fix round 1, Important 1: two real call sites in this repo already
    wrap subprocess/file work in `except Exception:` -
    lib/metrics_core/labels.py's _run_label_prompt, and both hooks' own
    main(). Before this fix, a RealClaudeDirWrite there (RuntimeError, an
    Exception) was silently swallowed - the write was still prevented, but
    a test that regressed into one of these paths would pass for the wrong
    reason. Both are now BaseException-safe; these tests prove it against
    the ACTUAL production functions, not a reimplementation of them.
    """

    def test_run_label_prompt_raises_loudly_instead_of_returning_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Empties PATH so the real `nice`/`claude` binaries can never be
        # found or launched - belt and suspenders alongside the guard
        # itself, which fires on argv content before any PATH lookup is
        # attempted (same design as test_launching_the_real_claude_cli_is_
        # blocked above).
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(RealClaudeDirWrite):
            labels_module._run_label_prompt("summarize this")
        _assert_guard_hit_and_clear_it()

    def test_hook_main_that_forgot_to_isolate_raises_loudly_instead_of_returning_none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # This can NEVER touch the real ~/.claude: _REAL_CLAUDE_DIR itself
        # is redirected (monkeypatched) to a throwaway FAKE guarded
        # directory for the duration of this one test - the seam the guard
        # exposes for exactly this purpose - and the hook's own METRICS_FILE
        # is left pointed INSIDE that fake dir, unpatched, reproducing the
        # exact shape of the 2026-09-23 incident this whole file guards
        # against, without ever naming the real directory at all.
        import importlib.util
        import io
        import json
        import sys

        fake_claude_directory = tmp_path / ".claude"
        fake_metrics_directory = fake_claude_directory / "metrics"
        fake_metrics_directory.mkdir(parents=True)
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())

        hook_path = Path(__file__).resolve().parent.parent / "hooks" / "metrics-log.py"
        spec = importlib.util.spec_from_file_location("metrics_log_hook_fix_round_1", hook_path)
        assert spec is not None
        assert spec.loader is not None
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        monkeypatch.setattr(hook, "METRICS_FILE", fake_metrics_directory / "sessions.jsonl")

        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("")  # empty is enough - parse_transcript tolerates it
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"transcript_path": str(transcript), "session_id": "s"})),
        )

        with pytest.raises(RealClaudeDirWrite):
            hook.main()
        assert not fake_metrics_directory.joinpath("sessions.jsonl").exists()
        _assert_guard_hit_and_clear_it()


class TestChmodAndUtimeAreBlocked:
    """Backlog 2026-09-24 #3: os.chmod and os.utime were documented, uncovered
    blind spots - metadata-only mutations of a real file (no data written),
    but still something no test here should ever do to the real ~/.claude.
    Both audit events were confirmed in scratch to fire with a trailing
    dir_fd argument (-1 when unused), same shape as os.mkdir.
    """

    def test_chmod_inside_the_real_claude_dir_is_blocked(self) -> None:
        # no-such-dir-canary does not exist under the real ~/.claude, so
        # without the guard this would raise FileNotFoundError - it can
        # never actually touch anything real, guard or no guard.
        canary = _REAL_CLAUDE_DIR / "no-such-dir-canary" / "x.jsonl"
        assert not canary.parent.exists()
        with pytest.raises(RealClaudeDirWrite):
            canary.chmod(0o600)
        assert not canary.parent.exists()
        _assert_guard_hit_and_clear_it()

    def test_utime_inside_the_real_claude_dir_is_blocked(self) -> None:
        canary = _REAL_CLAUDE_DIR / "no-such-dir-canary" / "x.jsonl"
        assert not canary.parent.exists()
        with pytest.raises(RealClaudeDirWrite):
            os.utime(canary, None)
        assert not canary.parent.exists()
        _assert_guard_hit_and_clear_it()

    def test_chmod_and_utime_outside_the_real_dir_remain_allowed(self, tmp_path: Path) -> None:
        target = tmp_path / "harmless.txt"
        target.write_text("x")
        target.chmod(0o600)  # must not raise
        os.utime(target, None)  # must not raise


class TestSqliteConnectIsBlocked:
    """Backlog 2026-09-24 #3: sqlite3 opens its own file handle in C, below
    the audit-event layer the "open" case above watches - but sqlite3.connect
    fires its own dedicated audit event, carrying only the raw database
    string (confirmed in scratch: no separate mode/uri flag). A plain
    sqlite3.connect can CREATE the file if it does not exist, so it is
    treated as write-capable unless the caller explicitly opted into a
    read-only URI (uri=True, "file:...?mode=ro") - the one shape sqlite3
    itself guarantees never creates or mutates anything.
    """

    def test_connect_inside_the_real_claude_dir_is_blocked(self) -> None:
        # no-such-dir-canary does not exist, so without the guard this
        # would raise sqlite3.OperationalError trying to open the file -
        # it can never actually create anything under the real dir.
        canary = _REAL_CLAUDE_DIR / "no-such-dir-canary" / "x.db"
        assert not canary.parent.exists()
        with pytest.raises(RealClaudeDirWrite):
            sqlite3.connect(str(canary))
        assert not canary.parent.exists()
        _assert_guard_hit_and_clear_it()

    def test_read_only_uri_connect_inside_the_real_claude_dir_is_not_blocked_by_this_guard(
        self,
    ) -> None:
        # Proves the guard is not over-broad: an explicit read-only URI is
        # let through by THIS check (sqlite3 itself still refuses to open a
        # database file that does not exist in read-only mode, so this
        # raises sqlite3.OperationalError, never RealClaudeDirWrite).
        canary = _REAL_CLAUDE_DIR / "no-such-dir-canary" / "x.db"
        assert not canary.parent.exists()
        with pytest.raises(sqlite3.OperationalError):
            sqlite3.connect(f"file:{canary}?mode=ro", uri=True)
        assert not canary.parent.exists()

    def test_connect_outside_the_real_claude_dir_remains_allowed(self, tmp_path: Path) -> None:
        connection = sqlite3.connect(str(tmp_path / "harmless.db"))
        connection.close()


class TestOsSystemIsBlocked:
    """Backlog 2026-09-24 #3: os.system shells out via /bin/sh -c but fires
    its own "os.system" audit event, distinct from subprocess.Popen's - the
    same `claude`/unguarded-python checks Popen already gets are applied
    here too. os.system always runs under the CALLING process's own real
    environment (there is no env= parameter at all), so any Python
    interpreter it launches is unconditionally unguarded - there is no
    "was HOME redirected" question to even ask, unlike the Popen case.
    """

    def test_launching_claude_via_os_system_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Empty PATH: without the guard, /bin/sh -c would fail to find
        # "claude" and os.system would return a nonzero shell exit status -
        # it can never actually launch the real CLI either way.
        monkeypatch.setenv("PATH", str(tmp_path))
        with pytest.raises(RealClaudeDirWrite):
            os.system("claude --version")
        _assert_guard_hit_and_clear_it()

    def test_launching_a_python_child_via_os_system_is_blocked(self) -> None:
        # No PATH manipulation needed: the guard must fire before the shell
        # ever gets a chance to actually resolve or run "python3".
        with pytest.raises(RealClaudeDirWrite):
            os.system("python3 -c 'pass'")
        _assert_guard_hit_and_clear_it()

    def test_an_unrelated_os_system_command_remains_allowed(self) -> None:
        exit_status = os.system("true")
        assert exit_status == 0

    def test_a_claude_lookalike_via_os_system_is_not_blocked(self) -> None:
        # "claudette" is not a whole-word match for "claude" - matches
        # TestClaudeLaunchMatchingIsPrecise's own lookalike coverage above.
        exit_status = os.system("echo claudette")
        assert exit_status == 0


class TestPosixSpawnIsBlocked:
    """Backlog 2026-09-24 #3: os.posix_spawn/os.posix_spawnp fire the SAME
    "os.posix_spawn" audit event (confirmed in scratch - there is no
    separate "os.posix_spawnp" event), shaped exactly like
    subprocess.Popen's own (path, argv, env) - env is a MANDATORY positional
    argument here, unlike Popen's optional env= kwarg, but a caller can
    still hand it one that does not redirect HOME away from the real one,
    so the exact same two checks Popen already gets apply unchanged.
    """

    def test_launching_claude_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # An absolute path that does not exist: without the guard,
        # posix_spawn would raise FileNotFoundError immediately - it can
        # never actually launch anything real either way.
        monkeypatch.setenv("PATH", str(tmp_path))
        nonexistent_claude_path = str(tmp_path / "claude")
        with pytest.raises(RealClaudeDirWrite):
            os.posix_spawn(nonexistent_claude_path, [nonexistent_claude_path], {"PATH": ""})
        _assert_guard_hit_and_clear_it()

    def test_a_child_with_no_home_override_is_blocked(self) -> None:
        with pytest.raises(RealClaudeDirWrite):
            os.posix_spawn(sys.executable, [sys.executable, "-c", "pass"], {"PATH": "/usr/bin"})
        _assert_guard_hit_and_clear_it()

    def test_a_child_with_home_redirected_to_a_tmp_dir_is_allowed(self, tmp_path: Path) -> None:
        environment = dict(os.environ, HOME=str(tmp_path))
        process_id = os.posix_spawn(sys.executable, [sys.executable, "-c", "pass"], environment)
        exit_status = os.waitpid(process_id, 0)[1]
        assert exit_status == 0

    def test_a_non_python_non_claude_child_is_unaffected(self) -> None:
        process_id = os.posix_spawn("/bin/echo", ["/bin/echo", "hi"], {"PATH": "/usr/bin:/bin"})
        exit_status = os.waitpid(process_id, 0)[1]
        assert exit_status == 0


class TestVersionedClaudeCliPathIsBlocked:
    """Backlog 2026-09-24 #3: the real `claude` on the author's own machine
    is a shim (~/.local/bin/claude) whose realpath is a VERSIONED file
    under ~/.local/share/claude/versions/<version> - a basename that is
    never the literal word "claude". Built entirely against a FAKE
    installation directory (monkeypatched onto conftest's own module-level
    seam), never the real one, so this passes identically whether or not
    the real Claude CLI happens to be installed on the machine running the
    suite (e.g. a bare CI runner).
    """

    def test_an_executable_whose_realpath_is_inside_the_fake_installation_directory_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_installation_directory = tmp_path / "versions"
        fake_installation_directory.mkdir()
        fake_versioned_binary = fake_installation_directory / "2.1.999"
        fake_versioned_binary.write_text("not a real binary")
        monkeypatch.setattr(
            conftest, "_CLAUDE_INSTALLATION_DIRECTORY", fake_installation_directory.resolve()
        )
        with pytest.raises(RealClaudeDirWrite):
            subprocess.Popen([str(fake_versioned_binary)])
        _assert_guard_hit_and_clear_it()

    def test_the_same_check_applies_to_posix_spawn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_installation_directory = tmp_path / "versions"
        fake_installation_directory.mkdir()
        fake_versioned_binary = fake_installation_directory / "2.1.999"
        fake_versioned_binary.write_text("not a real binary")
        monkeypatch.setattr(
            conftest, "_CLAUDE_INSTALLATION_DIRECTORY", fake_installation_directory.resolve()
        )
        with pytest.raises(RealClaudeDirWrite):
            os.posix_spawn(str(fake_versioned_binary), [str(fake_versioned_binary)], {})
        _assert_guard_hit_and_clear_it()

    def test_an_executable_outside_the_fake_installation_directory_is_not_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_installation_directory = tmp_path / "versions"
        fake_installation_directory.mkdir()
        monkeypatch.setattr(
            conftest, "_CLAUDE_INSTALLATION_DIRECTORY", fake_installation_directory.resolve()
        )
        process = subprocess.Popen(["/bin/echo", "hi"], stdout=subprocess.DEVNULL)
        assert process.wait(timeout=10) == 0

    def test_no_installation_directory_found_does_not_crash_the_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The real machine running the suite may not have `claude`
        # installed at all (a bare CI runner) - the guard must degrade to
        # the plain basename check, never raise from ITS OWN code.
        monkeypatch.setattr(conftest, "_CLAUDE_INSTALLATION_DIRECTORY", None)
        process = subprocess.Popen(["/bin/echo", "hi"], stdout=subprocess.DEVNULL)
        assert process.wait(timeout=10) == 0


class TestDirFdRelativeOperationsAreBlocked:
    """Backlog 2026-09-24 #3: os.mkdir/os.remove/os.rmdir/os.rename/
    os.symlink/os.link/shutil.rmtree all accept a dir_fd (a bare relative
    name like "victim.txt", resolved against an already-open directory file
    descriptor instead of the current working directory) - and, confirmed
    in scratch, their audit events DO carry that dir_fd as a trailing
    integer argument (unlike "open", which does not carry one at all - see
    this module's own docstring for that honestly-undecidable case). Before
    this fix, _inside_real_claude_dir resolved a bare relative name against
    the test process's OWN current working directory, which is never the
    real ~/.claude - so a dir_fd-relative mutation into the real dir would
    have silently succeeded, guard or no guard. Every fixture here builds
    its own FAKE guarded directory (monkeypatching conftest's own
    _REAL_CLAUDE_DIR/_REAL_CLAUDE_DIR_LEXICAL) and creates any pre-existing
    victim file BEFORE that redirect - the same ordering
    TestCaseAndSymlinkEscapes above uses - so test setup itself never trips
    the guard.

    macOS's os.readlink(f"/dev/fd/{fd}") does NOT resolve a directory fd
    (confirmed in scratch: OSError(22, "Invalid argument")) - the guard
    uses fcntl(fd, F_GETPATH, ...) there instead; these tests exercise that
    production code path directly (they do not run on Linux CI, but the
    guard's own fallback for that platform is a plain os.readlink on
    /proc/self/fd, documented in _directory_from_dir_fd's own docstring).
    """

    def test_mkdir_dir_fd_relative_into_the_real_dir_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        directory_file_descriptor = os.open(str(fake_claude_directory), os.O_RDONLY)
        try:
            with pytest.raises(RealClaudeDirWrite):
                os.mkdir("canary-child", dir_fd=directory_file_descriptor)
        finally:
            os.close(directory_file_descriptor)
        assert not (fake_claude_directory / "canary-child").exists()
        _assert_guard_hit_and_clear_it()

    def test_remove_dir_fd_relative_into_the_real_dir_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        victim = fake_claude_directory / "victim.txt"
        victim.write_text("do not delete")
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        directory_file_descriptor = os.open(str(fake_claude_directory), os.O_RDONLY)
        try:
            with pytest.raises(RealClaudeDirWrite):
                os.remove("victim.txt", dir_fd=directory_file_descriptor)
        finally:
            os.close(directory_file_descriptor)
        assert victim.exists()
        _assert_guard_hit_and_clear_it()

    def test_rmdir_dir_fd_relative_into_the_real_dir_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        victim_directory = fake_claude_directory / "victim_directory"
        victim_directory.mkdir()
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        directory_file_descriptor = os.open(str(fake_claude_directory), os.O_RDONLY)
        try:
            with pytest.raises(RealClaudeDirWrite):
                os.rmdir("victim_directory", dir_fd=directory_file_descriptor)
        finally:
            os.close(directory_file_descriptor)
        assert victim_directory.exists()
        _assert_guard_hit_and_clear_it()

    def test_rename_dir_fd_relative_into_the_real_dir_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        source = fake_claude_directory / "source.txt"
        source.write_text("x")
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        directory_file_descriptor = os.open(str(fake_claude_directory), os.O_RDONLY)
        try:
            with pytest.raises(RealClaudeDirWrite):
                os.rename(
                    "source.txt",
                    "renamed.txt",
                    src_dir_fd=directory_file_descriptor,
                    dst_dir_fd=directory_file_descriptor,
                )
        finally:
            os.close(directory_file_descriptor)
        assert source.exists()
        assert not (fake_claude_directory / "renamed.txt").exists()
        _assert_guard_hit_and_clear_it()

    def test_symlink_dir_fd_relative_into_the_real_dir_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        directory_file_descriptor = os.open(str(fake_claude_directory), os.O_RDONLY)
        try:
            with pytest.raises(RealClaudeDirWrite):
                os.symlink("target.txt", "link.txt", dir_fd=directory_file_descriptor)
        finally:
            os.close(directory_file_descriptor)
        assert not (fake_claude_directory / "link.txt").exists()
        _assert_guard_hit_and_clear_it()

    def test_link_dir_fd_relative_into_the_real_dir_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        source = fake_claude_directory / "hardlink_source.txt"
        source.write_text("x")
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        directory_file_descriptor = os.open(str(fake_claude_directory), os.O_RDONLY)
        try:
            with pytest.raises(RealClaudeDirWrite):
                os.link(
                    "hardlink_source.txt",
                    "hardlink_target.txt",
                    src_dir_fd=directory_file_descriptor,
                    dst_dir_fd=directory_file_descriptor,
                )
        finally:
            os.close(directory_file_descriptor)
        assert not (fake_claude_directory / "hardlink_target.txt").exists()
        _assert_guard_hit_and_clear_it()

    def test_rmtree_dir_fd_relative_into_the_real_dir_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import shutil

        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        victim_directory = fake_claude_directory / "victim_tree"
        victim_directory.mkdir()
        (victim_directory / "inside.txt").write_text("x")
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        directory_file_descriptor = os.open(str(fake_claude_directory), os.O_RDONLY)
        try:
            with pytest.raises(RealClaudeDirWrite):
                shutil.rmtree("victim_tree", dir_fd=directory_file_descriptor)
        finally:
            os.close(directory_file_descriptor)
        assert victim_directory.exists()
        _assert_guard_hit_and_clear_it()

    def test_a_dir_fd_relative_write_outside_the_fake_guarded_dir_remains_allowed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Proves the new dir_fd resolution is not over-broad: a dir_fd
        # pointing OUTSIDE the (fake) guarded directory must not itself
        # become a false positive.
        fake_claude_directory = tmp_path / ".claude"
        fake_claude_directory.mkdir()
        harmless_directory = tmp_path / "harmless"
        harmless_directory.mkdir()
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", fake_claude_directory.resolve())
        monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", fake_claude_directory)
        directory_file_descriptor = os.open(str(harmless_directory), os.O_RDONLY)
        try:
            os.mkdir("harmless-child", dir_fd=directory_file_descriptor)
        finally:
            os.close(directory_file_descriptor)
        assert (harmless_directory / "harmless-child").exists()
