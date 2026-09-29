"""Backlog 2026-09-28 #3 (Package N, coverage round): tests/conftest.py was
brought into the --cov gate (pyproject.toml's addopts) for the first time
here, and doing so surfaced a genuinely surprising, previously-invisible
fact about how this whole guard was tested: CODE THAT RUNS ONLY INSIDE A
sys.addaudithook CALLBACK IS NEVER OBSERVED BY coverage.py.

Confirmed with a throwaway scratch script (not committed): a plain
sys.addaudithook callback that increments a counter, verified via that
counter's own final value to have run exactly twice, was reported by
`coverage report -m` as 0% covered for every line inside it - with both
coverage.py's legacy CTracer and its default sys.monitoring-based core
(COVERAGE_CORE=ctrace made no difference). This means every one of
test_real_metrics_isolation.py's existing guard tests - which all trigger
the guard by performing a REAL os.mkdir/os.remove/subprocess.Popen/etc. and
asserting RealClaudeDirWrite - proves the guard's real-world BEHAVIOR
(that CPython's actual audit events have the shapes this file assumes, and
that the guard's net effect really does block/allow the right things), but
contributes ZERO line or branch coverage of _audit_real_claude_dir_guard or
any helper only reachable through it. That is essentially all of this
file's actual guard logic.

This file closes the coverage gap a different way, WITHOUT weakening or
duplicating those existing behavioral tests: sys.addaudithook is the only
thing that is coverage-invisible, not the functions it calls - a
DIRECT, ordinary Python call to conftest._audit_real_claude_dir_guard(...)
(or to any of the smaller private helpers it delegates to) with exactly
the same argument shapes those existing tests already confirmed CPython
produces, runs in normal call context and IS traced. So every test below
calls a private helper directly - never through a real os.*/subprocess
call - covering the exact same code, argument-shape for argument-shape, as
the behavioral tests above already proved is what CPython actually sends.
Nothing here performs a real filesystem mutation or process launch: with
no real audit event ever firing, there is nothing for the dispatcher to
prevent, so unlike test_real_metrics_isolation.py's fixtures, these do not
need a canary constructed so its own failure mode is impossible - there is
no failure mode to construct against.
"""

import fcntl
import os
import sys
from pathlib import Path

import conftest
import pytest
from conftest import RealClaudeDirWrite


@pytest.fixture
def fake_guarded_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Same seam test_real_metrics_isolation.py's own dir_fd fixtures use:
    a throwaway directory standing in for the real ~/.claude, monkeypatched
    onto conftest's own module constants for the duration of one test."""
    directory = tmp_path / ".claude"
    directory.mkdir()
    monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR", directory.resolve())
    monkeypatch.setattr(conftest, "_REAL_CLAUDE_DIR_LEXICAL", directory)
    return directory


class TestResolvedPathOrNoneAndLexicalAbspathOrNone:
    """Direct calls to the two path-normalizing helpers - both are also
    exercised through _guarded_os_open's own tests
    (test_real_metrics_isolation.py's TestOsOpenDirFdRelativeWritesAreBlocked),
    but only for well-formed string paths. These cover the two remaining,
    genuinely reachable branches each still has."""

    def test_resolved_path_or_none_rejects_a_non_path_argument(self) -> None:
        # An int - the fd shape the audit events above sometimes carry
        # (confirmed in scratch, per this function's own docstring).
        assert conftest._resolved_path_or_none(12345) is None

    def test_lexical_abspath_or_none_rejects_a_non_path_argument(self) -> None:
        assert conftest._lexical_abspath_or_none(12345) is None

    def test_resolved_path_or_none_returns_none_for_an_embedded_null_byte(self) -> None:
        # Confirmed in scratch: Path(...).resolve() raises
        # ValueError("embedded null character in path") for this - a real,
        # if rare, shape a caller could hand any of the audit events above.
        assert conftest._resolved_path_or_none("bad\x00path") is None

    def test_resolved_path_or_none_decodes_a_bytes_argument(self, tmp_path: Path) -> None:
        # bytes is a real shape the audit events above pass (confirmed in
        # scratch, per this function's own docstring) - the isinstance
        # check above only lets THIS function reach the decode step when
        # the argument really is bytes, which the null-byte test above
        # (a str) never exercises.
        assert conftest._resolved_path_or_none(os.fsencode(str(tmp_path))) == tmp_path.resolve()

    def test_lexical_abspath_or_none_decodes_a_bytes_argument(self, tmp_path: Path) -> None:
        result = conftest._lexical_abspath_or_none(os.fsencode(str(tmp_path)))
        # Deliberately os.path.abspath, not Path(...).resolve() - this
        # asserts the LEXICAL form the function under test itself returns,
        # same reasoning as conftest.py's own matching noqa comment on the
        # equivalent production line.
        assert result == Path(os.path.abspath(str(tmp_path)))  # noqa: PTH100

    def test_lexical_abspath_or_none_returns_none_when_os_getcwd_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Backlog 2026-09-28 #5 (Package P2, folded in from Package N's
        # review): _lexical_abspath_or_none's `except (OSError, ValueError)`
        # was marked `# pragma: no cover - defensive` because os.getcwd()
        # failing was judged untestable in-process. A reviewer reached it
        # for real in a CHILD process (chdir into a temp dir, rmdir it out
        # from under the process, then call os.path.abspath on a relative
        # path -> FileNotFoundError, an OSError subclass) - not reproduced
        # here the same way (would corrupt every later test in this same
        # session, same constraint as _resolved_path_or_none's identical
        # branch, per this file's own module docstring). Monkeypatching
        # os.getcwd to raise exercises the REAL except clause instead:
        # os.path.abspath calls os.getcwd() itself for a relative argument
        # (confirmed in scratch - posixpath.abspath joins os.getcwd() onto
        # any non-absolute path before normalizing), so this is a genuine
        # call into the function under test, not a fake dispatch.
        def _raise_getcwd() -> str:
            raise FileNotFoundError("simulated: cwd no longer exists")

        monkeypatch.setattr(os, "getcwd", _raise_getcwd)

        assert conftest._lexical_abspath_or_none("relative/path") is None


class TestSameOrWithinLinuxBranch:
    """_same_or_within's own docstring says it is checked at CALL time, not
    cached, specifically so a test can monkeypatch sys.platform to exercise
    the non-darwin branch on any machine - this is that test."""

    def test_relative_path_matches_on_the_non_darwin_branch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        base = tmp_path
        candidate = tmp_path / "child"
        assert conftest._same_or_within(candidate, base) is True

    def test_unrelated_path_does_not_match_on_the_non_darwin_branch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        base = tmp_path / "base"
        candidate = tmp_path / "elsewhere"
        assert conftest._same_or_within(candidate, base) is False


class TestSameOrWithinDarwinBranch:
    """The darwin branch, forced the same way on any machine. On macOS the
    suite reaches it for real, but this repo's CI runs on Linux, where only
    these tests measure it - without them the coverage gate fails there
    and nowhere else."""

    def test_a_differently_cased_child_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        base = Path("/Users/someone/.claude")
        assert conftest._same_or_within(Path("/Users/someone/.CLAUDE/metrics"), base) is True

    def test_the_base_itself_matches_in_any_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        base = Path("/Users/someone/.claude")
        assert conftest._same_or_within(Path("/Users/someone/.Claude"), base) is True

    def test_a_sibling_sharing_the_prefix_does_not_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        base = Path("/Users/someone/.claude")
        assert conftest._same_or_within(Path("/Users/someone/.claude-backup"), base) is False

    def test_an_unrelated_path_does_not_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        base = Path("/Users/someone/.claude")
        assert conftest._same_or_within(Path("/Users/someone/projects"), base) is False


class TestResolveDirectoryFileDescriptorBothPlatformBranches:
    """_resolve_directory_file_descriptor's real macOS branch (fcntl
    F_GETPATH) and real Linux branch (/proc/self/fd readlink) are each
    exercised FOR REAL by test_real_metrics_isolation.py's
    TestOsOpenDirFdRelativeWritesAreBlocked - but only for whichever
    platform actually runs the suite (macOS locally, Linux on this repo's
    own ubuntu-latest CI). Getting 100% branch coverage on EITHER single
    platform therefore needs the OTHER branch exercised by injected fakes,
    exactly as this repo's own instructions call for - no real fd needed
    for any test here, since the platform-specific OS call itself is
    mocked out.

    Patches this file's OWN `sys`/`fcntl`/`Path` imports below, never
    `conftest.sys`/`conftest.fcntl`/`conftest.Path` - both name the exact
    same module/class objects (a module is a process-wide singleton in
    sys.modules, and Path is one class shared by every `from pathlib
    import Path`), so conftest.py's own `sys.platform`/`fcntl.fcntl(...)`/
    `Path(...).readlink()` calls see the patch either way. Going through
    conftest's own re-imported names instead trips mypy --strict's
    implicit-reexport check (conftest.py never declared them as its own
    public API - it merely imports them for its own use).
    """

    def test_darwin_branch_calls_fcntl_f_getpath_and_decodes_its_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        seen_calls: list[tuple[int, int]] = []

        def fake_fcntl(file_descriptor: int, request: int, buffer: bytearray) -> bytes:
            seen_calls.append((file_descriptor, request))
            return b"/fake/darwin/directory\x00\x00\x00"

        monkeypatch.setattr(fcntl, "fcntl", fake_fcntl)
        monkeypatch.setattr(fcntl, "F_GETPATH", 50, raising=False)
        resolved = conftest._resolve_directory_file_descriptor(7)
        assert resolved == Path("/fake/darwin/directory").resolve()
        assert seen_calls == [(7, 50)]

    def test_darwin_branch_fcntl_oserror_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")

        def raising_fcntl(file_descriptor: int, request: int, buffer: bytearray) -> bytes:
            raise OSError("bad file descriptor")

        monkeypatch.setattr(fcntl, "fcntl", raising_fcntl)
        monkeypatch.setattr(fcntl, "F_GETPATH", 50, raising=False)
        assert conftest._resolve_directory_file_descriptor(7) is None

    def test_linux_branch_reads_proc_self_fd_via_readlink(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        seen_paths: list[str] = []

        def fake_readlink(self: Path) -> Path:
            seen_paths.append(str(self))
            return Path("/fake/linux/directory")

        monkeypatch.setattr(Path, "readlink", fake_readlink)
        resolved = conftest._resolve_directory_file_descriptor(9)
        assert resolved == Path("/fake/linux/directory").resolve()
        assert seen_paths == ["/proc/self/fd/9"]

    def test_linux_branch_readlink_oserror_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")

        def raising_readlink(self: Path) -> Path:
            raise OSError("no such file descriptor")

        monkeypatch.setattr(Path, "readlink", raising_readlink)
        assert conftest._resolve_directory_file_descriptor(9) is None

    def test_a_non_int_argument_returns_none_without_touching_either_platform_call(
        self,
    ) -> None:
        assert conftest._resolve_directory_file_descriptor("not-an-fd") is None

    def test_the_c_default_of_negative_one_returns_none(self) -> None:
        # -1 means "no dir_fd was passed at all" - the shape most non-dir_fd-
        # relative calls carry (confirmed in scratch, per this function's
        # own docstring).
        assert conftest._resolve_directory_file_descriptor(-1) is None


class TestJoinedWithDirectoryFileDescriptor:
    def test_rejects_a_non_path_argument(self) -> None:
        assert conftest._joined_with_directory_file_descriptor(12345, -1) is None

    def test_an_already_absolute_path_ignores_the_directory_file_descriptor(
        self, tmp_path: Path
    ) -> None:
        # POSIX ignores dir_fd once the path is already absolute - and -1
        # (never resolvable) proves this branch returns None from the
        # absolute-path check itself, not from a failed resolution.
        assert conftest._joined_with_directory_file_descriptor(str(tmp_path / "x.txt"), -1) is None

    def test_an_unresolvable_directory_file_descriptor_returns_none(self) -> None:
        assert conftest._joined_with_directory_file_descriptor("relative.txt", -1) is None

    def test_joins_a_bytes_relative_path_against_a_resolved_directory(self, tmp_path: Path) -> None:
        directory_file_descriptor = os.open(str(tmp_path), os.O_RDONLY)
        try:
            joined = conftest._joined_with_directory_file_descriptor(
                b"child.txt", directory_file_descriptor
            )
        finally:
            os.close(directory_file_descriptor)
        assert joined == (tmp_path / "child.txt").resolve()

    def test_joins_a_relative_path_against_a_resolved_directory(self, tmp_path: Path) -> None:
        directory_file_descriptor = os.open(str(tmp_path), os.O_RDONLY)
        try:
            joined = conftest._joined_with_directory_file_descriptor(
                "child.txt", directory_file_descriptor
            )
        finally:
            os.close(directory_file_descriptor)
        assert joined == (tmp_path / "child.txt").resolve()

    def test_an_embedded_null_byte_in_the_relative_path_returns_none(self, tmp_path: Path) -> None:
        # The directory itself resolves fine; it is the FINAL (directory /
        # raw_path).resolve() that raises (confirmed in scratch: the same
        # ValueError("embedded null character in path") as
        # _resolved_path_or_none's own null-byte test above).
        directory_file_descriptor = os.open(str(tmp_path), os.O_RDONLY)
        try:
            joined = conftest._joined_with_directory_file_descriptor(
                "bad\x00name", directory_file_descriptor
            )
        finally:
            os.close(directory_file_descriptor)
        assert joined is None


class TestInsideRealClaudeDirDirectCalls:
    """_inside_real_claude_dir's own two remaining gaps: the
    directory_file_descriptor-is-None branch (the overwhelming majority of
    real callers - every event except the dir_fd-carrying ones), and a
    candidate that resolves to None for every candidate slot at once (a
    path_argument that is not a path at all)."""

    def test_a_non_path_argument_with_no_directory_file_descriptor_is_never_inside(
        self,
    ) -> None:
        assert conftest._inside_real_claude_dir(12345) is False

    def test_a_normal_relative_path_with_no_directory_file_descriptor_is_never_inside(
        self,
    ) -> None:
        # Exercises the directory_file_descriptor-is-None branch with a
        # REAL path argument too (not just the non-path case above) -
        # candidate is not None here, only the third (dir_fd) slot is
        # skipped.
        assert conftest._inside_real_claude_dir("some-relative-name.txt") is False


class TestIsRealClaudeCliNameDirectCalls:
    def test_the_literal_basename_claude_matches_regardless_of_installation_directory(
        self,
    ) -> None:
        assert conftest._is_real_claude_cli_name("claude") is True

    def test_a_lookalike_name_does_not_match_without_an_installation_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(conftest, "_CLAUDE_INSTALLATION_DIRECTORY", None)
        assert conftest._is_real_claude_cli_name("claudette") is False

    def test_a_realpath_inside_the_installation_directory_matches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        installation_directory = tmp_path / "versions"
        installation_directory.mkdir()
        versioned_binary = installation_directory / "2.1.999"
        versioned_binary.write_text("not a real binary")
        monkeypatch.setattr(
            conftest, "_CLAUDE_INSTALLATION_DIRECTORY", installation_directory.resolve()
        )
        assert conftest._is_real_claude_cli_name(str(versioned_binary)) is True

    def test_a_realpath_outside_the_installation_directory_does_not_match(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        installation_directory = tmp_path / "versions"
        installation_directory.mkdir()
        monkeypatch.setattr(
            conftest, "_CLAUDE_INSTALLATION_DIRECTORY", installation_directory.resolve()
        )
        assert conftest._is_real_claude_cli_name(str(tmp_path / "unrelated")) is False

    def test_an_embedded_null_byte_name_does_not_match(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Confirmed in scratch: os.path.realpath raises
        # ValueError("embedded null character in path") for this.
        installation_directory = tmp_path / "versions"
        installation_directory.mkdir()
        monkeypatch.setattr(
            conftest, "_CLAUDE_INSTALLATION_DIRECTORY", installation_directory.resolve()
        )
        assert conftest._is_real_claude_cli_name("bad\x00name") is False


class TestIsWriteOpenDirectCalls:
    def test_a_text_write_mode_string_is_write_open(self) -> None:
        assert conftest._is_write_open("w", None) is True

    def test_a_text_read_mode_string_with_non_int_flags_is_not_write_open(self) -> None:
        assert conftest._is_write_open("r", None) is False

    def test_neither_a_mode_string_nor_int_flags_is_not_write_open(self) -> None:
        assert conftest._is_write_open(None, None) is False


class TestDecodedOrNoneAndDecodedItemOrNone:
    def test_a_str_value_passes_through_unchanged(self) -> None:
        assert conftest._decoded_or_none("already text") == "already text"

    def test_a_bytes_value_decodes(self) -> None:
        assert conftest._decoded_or_none(b"raw bytes") == "raw bytes"

    def test_an_unrelated_type_decodes_to_none(self) -> None:
        assert conftest._decoded_or_none(12345) is None

    def test_a_pathlike_item_is_converted_before_decoding(self, tmp_path: Path) -> None:
        assert conftest._decoded_item_or_none(tmp_path) == str(tmp_path)

    def test_a_plain_str_item_decodes_directly(self) -> None:
        assert conftest._decoded_item_or_none("plain") == "plain"


class TestArgvTokensDirectCalls:
    def test_a_list_of_strings_is_kept_as_is(self) -> None:
        assert conftest._argv_tokens(["claude", "--version"]) == ["claude", "--version"]

    def test_a_tuple_is_accepted_the_same_as_a_list(self) -> None:
        assert conftest._argv_tokens(("claude", "--version")) == ["claude", "--version"]

    def test_a_single_string_argument_becomes_a_one_element_list(self) -> None:
        assert conftest._argv_tokens("claude") == ["claude"]

    def test_an_undecodable_item_is_dropped_rather_than_kept_as_none(self) -> None:
        assert conftest._argv_tokens(["claude", 12345, "--version"]) == ["claude", "--version"]

    def test_an_unrelated_type_yields_an_empty_list(self) -> None:
        assert conftest._argv_tokens(12345) == []


class TestWrapperTargetDirectCalls:
    def test_skips_a_leading_flag_and_a_bare_numeric_argument(self) -> None:
        assert conftest._wrapper_target(["nice", "-n", "15", "claude"]) == "claude"

    def test_skips_a_var_equals_value_assignment(self) -> None:
        assert conftest._wrapper_target(["env", "HOME=/tmp/x", "claude"]) == "claude"

    def test_returns_none_when_every_token_is_skipped(self) -> None:
        assert conftest._wrapper_target(["nice", "-n", "15"]) is None


class TestLaunchesRealClaudeCliDirectCalls:
    def test_the_executable_argument_alone_can_match(self) -> None:
        assert conftest._launches_real_claude_cli("claude", ["something-else"]) is True

    def test_argv_zero_is_used_when_executable_is_none(self) -> None:
        assert conftest._launches_real_claude_cli(None, ["claude", "--version"]) is True

    def test_empty_argv_and_no_executable_does_not_match(self) -> None:
        assert conftest._launches_real_claude_cli(None, []) is False

    def test_a_nice_wrapper_around_claude_matches(self) -> None:
        assert conftest._launches_real_claude_cli(None, ["nice", "-n", "15", "claude"]) is True

    def test_a_nice_wrapper_around_something_else_does_not_match(self) -> None:
        assert conftest._launches_real_claude_cli(None, ["nice", "-n", "15", "echo"]) is False

    def test_a_wrapper_with_no_resolvable_target_does_not_match(self) -> None:
        assert conftest._launches_real_claude_cli(None, ["env"]) is False

    def test_a_shell_dash_c_command_mentioning_claude_matches(self) -> None:
        assert conftest._launches_real_claude_cli(None, ["sh", "-c", "echo claude here"]) is True

    def test_a_shell_dash_c_command_not_mentioning_claude_does_not_match(self) -> None:
        assert conftest._launches_real_claude_cli(None, ["sh", "-c", "echo nothing here"]) is False

    def test_an_unrelated_command_does_not_match(self) -> None:
        assert conftest._launches_real_claude_cli(None, ["ls", "-la"]) is False

    def test_a_shell_invocation_without_a_dash_c_flag_does_not_match(self) -> None:
        assert conftest._launches_real_claude_cli(None, ["sh", "script.sh"]) is False


class TestIsPythonInterpreterDirectCalls:
    def test_the_running_interpreters_own_executable_matches(self) -> None:
        assert conftest._is_python_interpreter(sys.executable) is True

    def test_a_python_prefixed_basename_matches(self) -> None:
        assert conftest._is_python_interpreter("python3.14") is True

    def test_an_unrelated_name_does_not_match(self) -> None:
        assert conftest._is_python_interpreter("/bin/echo") is False


class TestChildEnvLeavesHomeUnguardedDirectCalls:
    def test_a_non_dict_environment_is_unguarded(self) -> None:
        assert conftest._child_env_leaves_home_unguarded(None) is True

    def test_a_dict_with_no_home_key_is_unguarded(self) -> None:
        assert conftest._child_env_leaves_home_unguarded({"PATH": "/usr/bin"}) is True

    def test_a_non_path_home_value_is_unguarded(self) -> None:
        assert conftest._child_env_leaves_home_unguarded({"HOME": 12345}) is True

    def test_home_redirected_to_a_tmp_dir_is_guarded(self, tmp_path: Path) -> None:
        assert conftest._child_env_leaves_home_unguarded({"HOME": str(tmp_path)}) is False

    def test_home_left_pointing_at_the_real_home_is_unguarded(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(conftest, "_REAL_HOME", tmp_path.resolve())
        assert conftest._child_env_leaves_home_unguarded({"HOME": str(tmp_path)}) is True


class TestLaunchesAnUnguardedPythonChildDirectCalls:
    def test_a_non_python_child_is_never_flagged(self) -> None:
        assert conftest._launches_an_unguarded_python_child("/bin/echo", ["hi"], {}) is False

    def test_a_python_child_with_no_env_override_is_flagged(self) -> None:
        assert (
            conftest._launches_an_unguarded_python_child(sys.executable, ["-c", "pass"], None)
            is True
        )

    def test_argv_zero_is_used_as_the_executable_name_when_executable_is_none(self) -> None:
        assert (
            conftest._launches_an_unguarded_python_child(None, [sys.executable, "-c", "pass"], None)
            is True
        )

    def test_a_python_child_with_home_redirected_is_not_flagged(self, tmp_path: Path) -> None:
        assert (
            conftest._launches_an_unguarded_python_child(
                sys.executable, ["-c", "pass"], {"HOME": str(tmp_path)}
            )
            is False
        )


class TestIsReadOnlySqliteUriDirectCalls:
    def test_a_non_string_database_is_not_read_only(self) -> None:
        assert conftest._is_read_only_sqlite_uri(12345) is False

    def test_a_plain_path_string_is_not_read_only(self) -> None:
        assert conftest._is_read_only_sqlite_uri("/tmp/x.db") is False

    def test_a_read_only_uri_is_read_only(self) -> None:
        assert conftest._is_read_only_sqlite_uri("file:/tmp/x.db?mode=ro") is True

    def test_a_read_write_uri_is_not_read_only(self) -> None:
        assert conftest._is_read_only_sqlite_uri("file:/tmp/x.db?mode=rw") is False


class TestSystemCommandInvokesAnUnguardedPythonDirectCalls:
    def test_a_command_mentioning_python_matches(self) -> None:
        assert conftest._system_command_invokes_an_unguarded_python("python3 -c 'pass'") is True

    def test_an_unrelated_command_does_not_match(self) -> None:
        assert conftest._system_command_invokes_an_unguarded_python("true") is False


class TestAuditRealClaudeDirGuardDirectDispatch:
    """Calls conftest._audit_real_claude_dir_guard(event, audit_arguments)
    directly - the exact function sys.addaudithook invokes, with the exact
    argument shapes test_real_metrics_isolation.py's real, end-to-end tests
    already confirmed CPython produces for each event (see that module's
    per-event docstrings: "confirmed in scratch" appears on every one of
    them) - so every case here matches a shape already proven real, never a
    guess about what an audit event might look like.
    """

    def test_unrecognized_event_names_are_ignored(self) -> None:
        # Falls through every branch untouched - the implicit "no match"
        # arm of the whole if/elif chain.
        conftest._audit_real_claude_dir_guard("some.other.event", ())

    def test_write_mode_open_inside_the_real_dir_is_blocked(
        self, fake_guarded_directory: Path
    ) -> None:
        target = str(fake_guarded_directory / "canary.txt")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("open", (target, None, os.O_WRONLY))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_read_mode_open_inside_the_real_dir_is_allowed(
        self, fake_guarded_directory: Path
    ) -> None:
        target = str(fake_guarded_directory / "canary.txt")
        conftest._audit_real_claude_dir_guard("open", (target, None, os.O_RDONLY))

    def test_write_mode_open_outside_the_real_dir_is_allowed(self, tmp_path: Path) -> None:
        conftest._audit_real_claude_dir_guard(
            "open", (str(tmp_path / "harmless.txt"), None, os.O_WRONLY)
        )

    @pytest.mark.parametrize("event", ["os.mkdir", "os.chmod"])
    def test_three_element_events_inside_the_real_dir_are_blocked(
        self, fake_guarded_directory: Path, event: str
    ) -> None:
        target = str(fake_guarded_directory / "canary")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard(event, (target, 0o777, -1))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    @pytest.mark.parametrize("event", ["os.mkdir", "os.chmod"])
    def test_three_element_events_without_a_trailing_dir_fd_are_still_checked(
        self, fake_guarded_directory: Path, event: str
    ) -> None:
        # Shorter tuple than CPython actually sends (len <= 2): exercises
        # the ternary's "len(audit_arguments) > 2 else None" false arm.
        target = str(fake_guarded_directory / "canary")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard(event, (target, 0o777))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    @pytest.mark.parametrize("event", ["os.mkdir", "os.chmod"])
    def test_three_element_events_outside_the_real_dir_are_allowed(
        self, fake_guarded_directory: Path, tmp_path: Path, event: str
    ) -> None:
        conftest._audit_real_claude_dir_guard(event, (str(tmp_path / "harmless"), 0o777, -1))

    @pytest.mark.parametrize("event", ["os.rmdir", "os.remove", "shutil.rmtree"])
    def test_two_element_events_inside_the_real_dir_are_blocked(
        self, fake_guarded_directory: Path, event: str
    ) -> None:
        target = str(fake_guarded_directory / "victim")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard(event, (target, -1))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    @pytest.mark.parametrize("event", ["os.rmdir", "os.remove", "shutil.rmtree"])
    def test_two_element_events_without_a_trailing_dir_fd_are_still_checked(
        self, fake_guarded_directory: Path, event: str
    ) -> None:
        target = str(fake_guarded_directory / "victim")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard(event, (target,))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    @pytest.mark.parametrize("event", ["os.rmdir", "os.remove", "shutil.rmtree"])
    def test_two_element_events_outside_the_real_dir_are_allowed(
        self, tmp_path: Path, event: str
    ) -> None:
        conftest._audit_real_claude_dir_guard(event, (str(tmp_path / "harmless"), -1))

    def test_os_truncate_inside_the_real_dir_is_blocked(self, fake_guarded_directory: Path) -> None:
        target = str(fake_guarded_directory / "canary")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.truncate", (target, 0))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_truncate_outside_the_real_dir_is_allowed(self, tmp_path: Path) -> None:
        conftest._audit_real_claude_dir_guard("os.truncate", (str(tmp_path / "harmless"), 0))

    def test_os_utime_inside_the_real_dir_is_blocked(self, fake_guarded_directory: Path) -> None:
        target = str(fake_guarded_directory / "canary")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.utime", (target, None, 0, -1))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_utime_without_a_trailing_dir_fd_is_still_checked(
        self, fake_guarded_directory: Path
    ) -> None:
        target = str(fake_guarded_directory / "canary")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.utime", (target, None, 0))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_utime_outside_the_real_dir_is_allowed(self, tmp_path: Path) -> None:
        conftest._audit_real_claude_dir_guard("os.utime", (str(tmp_path / "harmless"), None, 0, -1))

    def test_os_rename_source_inside_the_real_dir_is_blocked(
        self, fake_guarded_directory: Path, tmp_path: Path
    ) -> None:
        source = str(fake_guarded_directory / "source")
        destination = str(tmp_path / "elsewhere" / "dest")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.rename", (source, destination, -1, -1))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_rename_destination_inside_the_real_dir_is_blocked(
        self, fake_guarded_directory: Path, tmp_path: Path
    ) -> None:
        source = str(tmp_path / "elsewhere" / "source")
        destination = str(fake_guarded_directory / "dest")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.rename", (source, destination))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_rename_neither_side_inside_the_real_dir_is_allowed(self, tmp_path: Path) -> None:
        source = str(tmp_path / "a")
        destination = str(tmp_path / "b")
        conftest._audit_real_claude_dir_guard("os.rename", (source, destination, -1, -1))

    def test_os_symlink_link_path_inside_the_real_dir_is_blocked(
        self, fake_guarded_directory: Path
    ) -> None:
        link_path = str(fake_guarded_directory / "link.txt")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.symlink", ("target.txt", link_path, -1))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_symlink_without_a_trailing_dir_fd_is_still_checked(
        self, fake_guarded_directory: Path
    ) -> None:
        link_path = str(fake_guarded_directory / "link.txt")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.symlink", ("target.txt", link_path))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_symlink_outside_the_real_dir_is_allowed(self, tmp_path: Path) -> None:
        conftest._audit_real_claude_dir_guard(
            "os.symlink", ("target.txt", str(tmp_path / "link.txt"), -1)
        )

    def test_os_link_destination_inside_the_real_dir_is_blocked(
        self, fake_guarded_directory: Path, tmp_path: Path
    ) -> None:
        source = str(tmp_path / "source.txt")
        destination = str(fake_guarded_directory / "hardlink.txt")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.link", (source, destination, -1, -1))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_link_without_a_trailing_dir_fd_is_still_checked(
        self, fake_guarded_directory: Path, tmp_path: Path
    ) -> None:
        source = str(tmp_path / "source.txt")
        destination = str(fake_guarded_directory / "hardlink.txt")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.link", (source, destination))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_link_outside_the_real_dir_is_allowed(self, tmp_path: Path) -> None:
        source = str(tmp_path / "source.txt")
        destination = str(tmp_path / "hardlink.txt")
        conftest._audit_real_claude_dir_guard("os.link", (source, destination, -1, -1))

    def test_shutil_copyfile_destination_inside_the_real_dir_is_blocked(
        self, fake_guarded_directory: Path, tmp_path: Path
    ) -> None:
        source = str(tmp_path / "source.txt")
        destination = str(fake_guarded_directory / "copied.txt")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("shutil.copyfile", (source, destination))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_shutil_copyfile_destination_outside_the_real_dir_is_allowed(
        self, tmp_path: Path
    ) -> None:
        source = str(tmp_path / "source.txt")
        destination = str(tmp_path / "copied.txt")
        conftest._audit_real_claude_dir_guard("shutil.copyfile", (source, destination))

    def test_sqlite3_connect_non_read_only_inside_the_real_dir_is_blocked(
        self, fake_guarded_directory: Path
    ) -> None:
        database = str(fake_guarded_directory / "x.db")
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("sqlite3.connect", (database,))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_sqlite3_connect_read_only_uri_inside_the_real_dir_is_allowed(
        self, fake_guarded_directory: Path
    ) -> None:
        database = f"file:{fake_guarded_directory / 'x.db'}?mode=ro"
        conftest._audit_real_claude_dir_guard("sqlite3.connect", (database,))

    def test_sqlite3_connect_outside_the_real_dir_is_allowed(self, tmp_path: Path) -> None:
        conftest._audit_real_claude_dir_guard("sqlite3.connect", (str(tmp_path / "x.db"),))

    def test_os_system_launching_claude_is_blocked(self) -> None:
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.system", (b"claude --version",))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_system_launching_an_unguarded_python_is_blocked(self) -> None:
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard("os.system", (b"python3 -c 'pass'",))
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_system_an_unrelated_command_is_allowed(self) -> None:
        conftest._audit_real_claude_dir_guard("os.system", (b"true",))

    def test_os_system_an_undecodable_command_returns_early(self) -> None:
        # _decoded_or_none(12345) is None - the early `return` right after
        # the command_text assignment, before either check below it runs.
        conftest._audit_real_claude_dir_guard("os.system", (12345,))

    def test_os_posix_spawn_launching_claude_is_blocked(self) -> None:
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard(
                "os.posix_spawn", ("/usr/bin/claude", ["claude"], {})
            )
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_posix_spawn_an_unguarded_python_child_is_blocked(self) -> None:
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard(
                "os.posix_spawn", (sys.executable, [sys.executable, "-c", "pass"], {})
            )
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_os_posix_spawn_an_unrelated_child_is_allowed(self) -> None:
        conftest._audit_real_claude_dir_guard(
            "os.posix_spawn", ("/bin/echo", ["/bin/echo", "hi"], {"PATH": "/usr/bin"})
        )

    def test_subprocess_popen_launching_claude_is_blocked(self) -> None:
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard(
                "subprocess.Popen", ("claude", ["claude"], None, None)
            )
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_subprocess_popen_an_unguarded_python_child_is_blocked(self) -> None:
        with pytest.raises(RealClaudeDirWrite):
            conftest._audit_real_claude_dir_guard(
                "subprocess.Popen",
                (sys.executable, [sys.executable, "-c", "pass"], None, None),
            )
        conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()

    def test_subprocess_popen_an_unrelated_child_is_allowed(self) -> None:
        conftest._audit_real_claude_dir_guard(
            "subprocess.Popen", ("/bin/echo", ["/bin/echo", "hi"], None, {"PATH": "/usr/bin"})
        )
