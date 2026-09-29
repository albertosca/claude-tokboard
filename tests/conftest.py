"""Generates one isolated dashboard for the whole test session, and isolates
EVERY test in the suite from the author's real ~/.claude/metrics by default.

HOME is overridden so every expanduser() inside the dashboard - the metrics
files, the label caches, get_open_projects, and the output path itself -
resolves inside a throwaway directory.
"""

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import metrics_core as mr
import metrics_core.backfill as backfill_module
import metrics_core.labels as labels_module
import metrics_core.paths as paths_module
from fixtures.build_fixture import build_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent


class RealClaudeDirWrite(BaseException):
    """Raised by the process-wide audit hook below the moment test code is
    about to write to, delete from, rename into/out of, or otherwise mutate
    the AUTHOR'S REAL ~/.claude directory - or launch the real `claude`
    CLI - no matter which metrics_core path constant a given test
    remembered (or forgot) to patch. See _install_real_claude_dir_guard.

    Subclasses BaseException, NOT RuntimeError/Exception (fix round 1,
    Important 1) - a plain `except Exception:` already exists downstream
    of code this guard protects (lib/metrics_core/labels.py's
    _run_label_prompt, hooks/metrics-log.py's and
    hooks/subagent-metrics-log.py's main()), and those would silently
    swallow a RuntimeError, still preventing the write but leaving a
    regressed test green for the wrong reason. Confirmed in scratch that a
    BaseException raised from inside an audit hook propagates past
    `except Exception:` while still aborting the operation. Every raise
    site also appends to REAL_CLAUDE_DIRECTORY_WRITE_HITS below (via
    _raise) as a second line of defense for the rare downstream
    `except BaseException:` - see the autouse
    _fail_loudly_on_swallowed_guard_hits fixture.
    """


# Every message _raise() below ever raised with, appended BEFORE raising -
# so it survives even a downstream `except BaseException:` that would
# otherwise leave no trace at all that the guard ever fired. The autouse
# _fail_loudly_on_swallowed_guard_hits fixture asserts this is empty after
# every test and clears it; a test that deliberately triggers a hit (via
# pytest.raises(RealClaudeDirWrite) or by exercising a swallow site) must
# clear it itself before returning, or that assertion fails LOUDLY instead
# of the hit disappearing unnoticed.
REAL_CLAUDE_DIRECTORY_WRITE_HITS: list[str] = []


def _raise(message: str) -> None:
    REAL_CLAUDE_DIRECTORY_WRITE_HITS.append(message)
    raise RealClaudeDirWrite(message)


# Captured ONCE, at import time of this module - before any fixture in the
# session (this file's own included) gets a chance to monkeypatch HOME or
# any metrics_core constant. Path.home() reads $HOME at CALL time, and
# conftest.py is imported before pytest collects a single test, which is as
# early as pytest offers.
_REAL_CLAUDE_DIR = (Path.home() / ".claude").resolve()
# The real home itself (not its .claude subdirectory) - fix round 1,
# Important 2: a subprocess.Popen'd Python child has its OWN process, and
# sys.addaudithook installs are per-process - a child's writes never pass
# through THIS hook at all. The only thing this hook can check from the
# parent side is whether the child's env redirects HOME away from the real
# one before it ever starts. See _child_env_leaves_home_unguarded.
_REAL_HOME = Path.home().resolve()

# Resolved ONCE, at import time, same reasoning as _REAL_CLAUDE_DIR/_REAL_HOME
# above - several tests in this suite deliberately empty PATH to build a safe
# canary (see test_launching_the_real_claude_cli_is_blocked), and a live
# shutil.which("claude") call from inside the audit hook itself would then
# always return None for THEM despite the real installation still existing.
# On the author's own machine, shutil.which("claude") resolves to a shim
# (~/.local/bin/claude) whose realpath is a VERSIONED file, e.g.
# ~/.local/share/claude/versions/2.1.284 - a basename that is never the
# literal word "claude". _CLAUDE_INSTALLATION_DIRECTORY is that versioned
# file's PARENT directory (the whole "versions" tree), so any executable
# whose own realpath lives anywhere under it is treated as "claude" too -
# see _is_real_claude_cli_name. None when `claude` is not on PATH at all
# (a bare CI runner, which never has it - this repo's own CI matrix is
# ubuntu-latest with no Claude Code install), in which case that check is
# simply skipped and the plain basename check still applies.
_RESOLVED_CLAUDE_CLI_PATH = shutil.which("claude")
_CLAUDE_INSTALLATION_DIRECTORY = (
    Path(os.path.realpath(_RESOLVED_CLAUDE_CLI_PATH)).parent
    if _RESOLVED_CLAUDE_CLI_PATH is not None
    else None
)

# The write-ish bits the "open" audit event's int `flags` argument can
# carry. Confirmed against this exact interpreter (Python 3.14.7, macOS) in
# a throwaway scratch script: `open(p, "w")` reports flags with
# O_CREAT|O_TRUNC|O_WRONLY set; `open(p, "a")` reports
# O_CREAT|O_APPEND|O_WRONLY; a bare os.open(p, os.O_WRONLY | os.O_CREAT)
# reports exactly those two bits back. The interpreter additionally
# OR-s in O_CLOEXEC on every open (including plain reads) - irrelevant
# here since it is not one of the bits below.
_WRITE_OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _resolved_path_or_none(path_argument: object) -> Path | None:
    """Best-effort turn an audit-event path argument into a resolved
    Path. Never raises: an fd (int), None, or anything else this cannot
    turn into a path yields None rather than crashing the audit hook for
    an operation that has nothing to do with ~/.claude - the hook sees
    every open/rename/etc. in the whole suite, so it must be fast and it
    must never itself be the thing that breaks an unrelated test. Not
    coverage-gated (conftest.py is outside the pytest-cov scope in
    pyproject.toml), but every branch here is deliberate, not defensive
    filler: fd ints and bytes/PathLike arguments are real shapes the audit
    events above pass in this very suite (verified in scratch).
    """
    if not isinstance(path_argument, str | bytes | os.PathLike):
        return None  # e.g. an int fd - nothing to resolve
    candidate = os.fspath(path_argument)
    if isinstance(candidate, bytes):
        try:
            candidate = candidate.decode("utf-8", "surrogateescape")
        except UnicodeDecodeError:
            return None
    try:
        return Path(candidate).resolve()
    except (OSError, ValueError):
        return None


def _lexical_abspath_or_none(path_argument: object) -> Path | None:
    """Fix round 1, M1: the same argument, made absolute WITHOUT following
    any symlink (os.path.abspath never resolves one, unlike
    Path.resolve()). A target like ~/.claude/skills/escape.md, where
    skills/ happens to be a symlink pointing OUTSIDE ~/.claude, resolves
    to somewhere harmless - but it still LEXICALLY names a location
    inside the real directory, and a test should not get to rely on some
    symlink there conveniently rerouting a write elsewhere. Never raises,
    for the same reasons as _resolved_path_or_none."""
    if not isinstance(path_argument, str | bytes | os.PathLike):
        return None
    candidate = os.fspath(path_argument)
    if isinstance(candidate, bytes):
        try:
            candidate = candidate.decode("utf-8", "surrogateescape")
        except UnicodeDecodeError:
            return None
    try:
        # os.path.abspath, deliberately NOT Path.resolve() - resolve()
        # follows symlinks, which is exactly the behavior this lexical
        # form exists to avoid (see the docstring above).
        return Path(os.path.abspath(candidate))  # noqa: PTH100
    except (OSError, ValueError):
        return None


def _same_or_within(candidate: Path, base: Path) -> bool:
    """Fix round 1, M1: `candidate.is_relative_to(base)`, but case-folded
    on darwin. macOS's default filesystem (APFS) is case-insensitive but
    CASE-PRESERVING: Path.resolve() does not correct a caller-supplied
    path's case to match what is actually on disk, so ~/.CLAUDE/x and
    ~/.claude/x name the exact same physical file there, while a plain
    (case-sensitive) is_relative_to sees them as unrelated. Checked at
    CALL time, not cached, so a test can monkeypatch sys.platform to
    exercise this branch on any machine."""
    if sys.platform == "darwin":
        candidate_text = str(candidate).casefold()
        base_text = str(base).casefold()
        return candidate_text == base_text or candidate_text.startswith(base_text + os.sep)
    return candidate.is_relative_to(base)


# Fix round 1, M1: the real .claude dir, UNRESOLVED (still absolute -
# Path.home() always is) - the lexical counterpart to _REAL_CLAUDE_DIR,
# compared against a candidate's own lexical form. See
# _lexical_abspath_or_none and _inside_real_claude_dir.
_REAL_CLAUDE_DIR_LEXICAL = Path.home() / ".claude"


def _resolve_directory_file_descriptor(directory_file_descriptor: object) -> Path | None:
    """Backlog 2026-09-24 #3: best-effort resolve a dir_fd audit-event
    argument (an already-open directory file descriptor a caller resolves
    a RELATIVE path against, instead of the current working directory) back
    to the directory it names. Returns None (never raises) for -1 (the C
    default meaning "no dir_fd was passed at all" - most of these events
    fire with it even for an ordinary, non-dir_fd-relative call), a
    non-int, or when resolution fails for any reason (the fd was already
    closed by the time this fires, a permissions error, ...) - callers then
    fall back to resolving the bare path exactly as before this existed,
    honestly unable to prove it does NOT name a location inside the real
    dir (see this module's own docstring for that limit).

    macOS's os.readlink(f"/dev/fd/{fd}") does NOT work for a directory fd -
    confirmed in scratch: it raises OSError(22, "Invalid argument"), unlike
    a Linux /proc/self/fd entry (a real symlink there). The primitive that
    does work on macOS, also confirmed in scratch, is fcntl(fd, F_GETPATH,
    <a writable buffer>) - it returns the fd's canonical, already
    symlink-resolved path as its own return value (NOT written into the
    buffer argument in place, despite the buffer being mutable - also
    confirmed in scratch, and not how every other fcntl() call on a mutable
    buffer behaves, which is why this is spelled out here instead of
    assumed from the man page).
    """
    if not isinstance(directory_file_descriptor, int) or directory_file_descriptor < 0:
        return None
    try:
        if sys.platform == "darwin":
            result_bytes = fcntl.fcntl(directory_file_descriptor, fcntl.F_GETPATH, bytearray(1024))
            resolved_text = result_bytes[: result_bytes.index(0)].decode("utf-8", "surrogateescape")
            resolved_path = Path(resolved_text)
        else:
            resolved_path = Path(f"/proc/self/fd/{directory_file_descriptor}").readlink()
        return resolved_path.resolve()
    except (OSError, ValueError):
        return None


def _joined_with_directory_file_descriptor(
    path_argument: object, directory_file_descriptor: object
) -> Path | None:
    """Backlog 2026-09-24 #3: when `path_argument` is a RELATIVE path and
    `directory_file_descriptor` names a real, resolvable directory (see
    _resolve_directory_file_descriptor above), returns the absolute
    location the pair actually names together - joining that directory
    with the relative path. POSIX ignores a dir_fd entirely once
    `path_argument` is already absolute (the ordinary resolved/lexical
    checks in _inside_real_claude_dir already handle an absolute path
    correctly on their own), so this returns None for that case, and for a
    dir_fd that cannot be resolved at all.
    """
    if not isinstance(path_argument, str | bytes | os.PathLike):
        return None
    raw_path = os.fspath(path_argument)
    if isinstance(raw_path, bytes):
        try:
            raw_path = raw_path.decode("utf-8", "surrogateescape")
        except UnicodeDecodeError:
            return None
    if Path(raw_path).is_absolute():
        return None
    directory = _resolve_directory_file_descriptor(directory_file_descriptor)
    if directory is None:
        return None
    try:
        return (directory / raw_path).resolve()
    except (OSError, ValueError):
        return None


def _inside_real_claude_dir(
    path_argument: object, directory_file_descriptor: object = None
) -> bool:
    """True if EITHER the resolved or the lexical form of `path_argument`
    names a location inside the real ~/.claude - except for the running
    checkout's own tree (fix round 1, M4: REPO_ROOT, and everything under
    it - .pytest_cache, .coverage, __pycache__ included, since they all
    live inside REPO_ROOT), which is exempt so the suite can run from an
    installed copy under ~/.claude/plugins/cache/<name>/ without the
    guard crashing on Python's own bytecode-cache writes for its imports.
    That exemption is checked per-candidate, not globally: a write
    elsewhere inside a REAL ~/.claude that merely happens to CONTAIN such
    an installed checkout is still blocked.

    `directory_file_descriptor`, when given (backlog 2026-09-24 #3), is an
    audit event's own dir_fd argument (-1/None meaning "not dir_fd-relative") - a THIRD
    candidate is checked, joining the directory it names with
    `path_argument` when the latter is relative. Without this, a bare
    relative name like "victim.txt" would only ever be resolved against
    this test PROCESS's own current working directory, which is never the
    real ~/.claude - silently missing a dir_fd-relative mutation into it.
    """
    candidates: list[tuple[Path | None, Path]] = [
        (_resolved_path_or_none(path_argument), _REAL_CLAUDE_DIR),
        (_lexical_abspath_or_none(path_argument), _REAL_CLAUDE_DIR_LEXICAL),
    ]
    if directory_file_descriptor is not None:
        candidates.append(
            (
                _joined_with_directory_file_descriptor(path_argument, directory_file_descriptor),
                _REAL_CLAUDE_DIR,
            )
        )
    for candidate, real_directory in candidates:
        if candidate is None:
            continue
        if _same_or_within(candidate, REPO_ROOT):
            continue
        if _same_or_within(candidate, real_directory):
            return True
    return False


_real_os_open = os.open
# Captured ONCE, at import time, before _install_real_claude_dir_guard ever
# rebinds os.open below - the same "capture the real thing before wrapping
# it" pattern _RESOLVED_CLAUDE_CLI_PATH/_CLAUDE_INSTALLATION_DIRECTORY use
# above for shutil.which("claude"). Kept as a plain module-level name (not
# read back through os.open at call time) so _guarded_os_open still calls
# the genuine os.open even after it has replaced os.open on the os module.


def _guarded_os_open(
    path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    flags: int,
    mode: int = 0o777,
    **keyword_arguments: int | None,
) -> int:
    """Backlog 2026-09-28 #3 (Package N, second round): closes the one
    remaining, previously-documented gap - a dir_fd-relative WRITE through
    os.open() itself. Confirmed in scratch (and unchanged by this fix): the
    "open" audit event _audit_real_claude_dir_guard reacts to never carries
    a dir_fd argument, so that hook alone can never see this case. os.open
    is also the ONLY entry point a dir_fd-relative open can go through in
    the first place - the builtin open()/Path.open() accept no dir_fd
    parameter at all - so wrapping this one function closes the gap
    completely, without needing a second audit hook or a second event.

    Takes **keyword_arguments rather than declaring the real parameter by
    its own name: that name is CPython's own os.open API, fixed by the
    interpreter, not a choice made in this file - so it is read only as a
    string key out of keyword_arguments, and forwarded to the real os.open
    unchanged below, never bound as an identifier in this module's own
    namespace (which is what this repo's naming rule actually governs).

    Reuses _inside_real_claude_dir/_resolve_directory_file_descriptor
    exactly as os.mkdir/os.remove/os.rename/... already do for their own
    directory-file-descriptor argument (see _audit_real_claude_dir_guard
    above) - the same fake-guarded-directory seam those tests use
    (monkeypatching _REAL_CLAUDE_DIR/_REAL_CLAUDE_DIR_LEXICAL) works
    unchanged here.

    Transparent for every other call: no directory file descriptor (the
    overwhelming majority - every plain os.open() elsewhere in this suite
    included) or a target this resolves as allowed both fall straight
    through to the real os.open, with the exact same arguments, return
    value, and error behavior (argument validation included - an
    unexpected keyword still fails exactly where the real os.open itself
    would reject it) as if this wrapper did not exist.
    """
    directory_file_descriptor = keyword_arguments.get("dir_fd")
    if (
        directory_file_descriptor is not None
        and _is_write_open(None, flags)
        and _inside_real_claude_dir(path, directory_file_descriptor)
    ):
        _raise(f"dir_fd-relative os.open() write into real {path!r}")
    return _real_os_open(path, flags, mode, **keyword_arguments)


def _is_real_claude_cli_name(name: str) -> bool:
    """Backlog 2026-09-24 #3: True for the literal basename "claude" (the
    common case), OR for any executable whose realpath lives inside the
    real Claude installation's own directory tree - closing the gap where
    `claude` resolves (via a shim at ~/.local/bin/claude) to a VERSIONED
    path whose basename is a bare version number, e.g.
    ~/.local/share/claude/versions/2.1.284, never the word "claude" at
    all. See _CLAUDE_INSTALLATION_DIRECTORY's own docstring for why that
    is resolved once, at import time, rather than here.
    """
    if Path(name).name == "claude":
        return True
    if _CLAUDE_INSTALLATION_DIRECTORY is None:
        return False
    try:
        return _same_or_within(Path(os.path.realpath(name)), _CLAUDE_INSTALLATION_DIRECTORY)
    except (OSError, ValueError):
        return False


def _is_write_open(mode: object, flags: object) -> bool:
    if isinstance(mode, str) and any(character in mode for character in "wax+"):
        return True
    if isinstance(flags, int):
        return bool(flags & _WRITE_OPEN_FLAGS)
    return False


def _decoded_or_none(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "surrogateescape")
        except UnicodeDecodeError:  # pragma: no cover - defensive
            return None
    return None


def _decoded_item_or_none(item: object) -> str | None:
    """Like _decoded_or_none, plus os.PathLike - an argv entry (or
    `executable`) can legitimately be any of str/bytes/os.PathLike."""
    if isinstance(item, os.PathLike):
        item = os.fspath(item)
    return _decoded_or_none(item)


def _argv_tokens(popen_arguments: object) -> list[str]:
    """The actual argv, decoded to str where possible: every element of
    `args` if it is a list/tuple (the shape subprocess.Popen's own audit
    event always carries by the time it fires - confirmed in scratch:
    even a bare string command or shell=True is normalized to this by
    CPython itself first), or a single-element list built from `args`
    itself if it is a str/bytes/PathLike. Entries that cannot be decoded
    are dropped rather than crashing the hook. Deliberately does NOT
    include `executable` - see _launches_real_claude_cli, which merging
    them here used to double-count when executable already equals
    argv[0] (the overwhelmingly common case), throwing off the
    nice/env wrapper-skipping logic below."""
    raw: list[object] = []
    if isinstance(popen_arguments, list | tuple):
        raw.extend(popen_arguments)
    elif isinstance(popen_arguments, str | bytes | os.PathLike):
        raw.append(popen_arguments)
    return [token for item in raw if (token := _decoded_item_or_none(item))]


_WRAPPER_BASENAMES = frozenset({"nice", "env"})
_SHELL_BASENAMES = frozenset({"sh", "bash", "zsh"})
_CLAUDE_WORD = re.compile(r"\bclaude\b")


def _wrapper_target(argv: list[str]) -> str | None:
    """The first token after a `nice`/`env` wrapper's own leading flags
    (`-n`, `-15`, ...), bare numeric arguments (`nice -n 15 claude ...`),
    and (for `env`) `VAR=value` assignments - the command such a wrapper
    ultimately runs. argv[0] is the wrapper itself; the caller has
    already checked it."""
    for token in argv[1:]:
        if token.startswith("-") or token.isdigit():
            continue
        if "=" in token and token.split("=", 1)[0].isidentifier():
            continue
        return token
    return None


def _launches_real_claude_cli(executable: object, popen_arguments: object) -> bool:
    """Fix round 1, M3: matches the ACTUAL command being run, not any
    argument that merely MENTIONS "claude" - `["echo", "claude"]` and
    `["grep", "-c", "claude", "/dev/null"]` must never match; the earlier
    version scanned every argv position and did. Three shapes count:
    `executable` itself (checked independently of argv - Popen lets
    `executable` differ from argv[0] entirely, e.g.
    Popen(["x"], executable="claude")); a `nice`/`env` wrapper's real
    target within argv (skipping the wrapper's own flags); or, for a
    shell invoked with -c (covers both a literal `sh -c ...` argv and
    Popen's own shell=True, which CPython normalizes to exactly this
    shape before this audit event ever fires - confirmed in scratch), a
    whole-word regex match against the shell command string - a coarser,
    deliberately OVER-blocking check for that one case, since a shell
    string cannot be parsed safely without a real shell parser and the
    guard would rather block a false positive than miss a real launch
    inside one.

    Backlog 2026-09-24 #3: `executable`/argv[0]/a wrapper target are all
    checked via _is_real_claude_cli_name rather than a bare basename
    comparison, so a `claude` launched through its resolved VERSIONED
    install path (basename a bare version number, not the word "claude")
    is caught too - see that function's own docstring.
    """
    argv = _argv_tokens(popen_arguments)
    exec_name = _decoded_item_or_none(executable) if executable is not None else None
    if exec_name is None and argv:
        exec_name = argv[0]  # CPython always populates `executable` in practice, but defensively
    if exec_name is not None and _is_real_claude_cli_name(exec_name):
        return True
    if not argv:
        return False
    argv_basename = Path(argv[0]).name
    if argv_basename in _WRAPPER_BASENAMES:
        target = _wrapper_target(argv)
        if target is not None and _is_real_claude_cli_name(target):
            return True
    is_shell_invoked_with_command_flag = (
        argv_basename in _SHELL_BASENAMES and len(argv) >= 3 and argv[1] == "-c"
    )
    return bool(is_shell_invoked_with_command_flag and _CLAUDE_WORD.search(argv[2]))


def _is_python_interpreter(token: str) -> bool:
    """Fix round 1, Important 2: sys.executable itself, or anything whose
    basename starts with "python" (python3, python3.14, a version-manager
    shim named plainly "python", ...). Deliberately loose - the guard
    would rather flag a non-Python binary that happens to be named
    "python-something" than miss a real interpreter under a name it
    didn't expect."""
    return token == sys.executable or Path(token).name.lower().startswith("python")


def _child_env_leaves_home_unguarded(environment: object) -> bool:
    """Fix round 1, Important 2: True unless `env` is a mapping that
    overrides HOME to somewhere other than the real one. subprocess.Popen's
    `env` REPLACES the child's entire environment when given (it does not
    merge with the parent's) - so `env=None` (inherits the parent's real
    HOME) and an `env` dict with no "HOME" key at all are both unguarded,
    same as one that explicitly sets HOME back to the real value.
    """
    if not isinstance(environment, dict):
        return True  # None (inherits the parent's real env), or an unrecognized shape
    home = environment.get("HOME")
    if not isinstance(home, str | bytes | os.PathLike):
        return True
    for candidate in (_resolved_path_or_none(home), _lexical_abspath_or_none(home)):
        if candidate is not None and _same_or_within(candidate, _REAL_HOME):
            return True
    return False


def _launches_an_unguarded_python_child(
    executable: object, popen_arguments: object, environment: object
) -> bool:
    """Fix round 1, Important 2: sys.addaudithook installs are per-process
    - this guard cannot see a single thing a subprocess.Popen'd Python
    child does with the real ~/.claude, no matter what it writes. The only
    thing checkable from the PARENT side is whether the child's env
    redirects HOME away from the real one before it ever starts; if it
    doesn't (or `env` is not given at all, so the child simply inherits
    the parent's real HOME), the child is unguarded and this refuses to
    launch it. A non-Python child is still only covered by
    _launches_real_claude_cli's own argv check above - see this module's
    docstring for that honestly-stated limit.
    """
    argv = _argv_tokens(popen_arguments)
    exec_name = _decoded_item_or_none(executable) if executable is not None else None
    if exec_name is None and argv:
        exec_name = argv[0]
    if exec_name is None or not _is_python_interpreter(exec_name):
        return False
    return _child_env_leaves_home_unguarded(environment)


def _is_read_only_sqlite_uri(database: object) -> bool:
    """Backlog 2026-09-24 #3: True only for the one shape sqlite3 itself
    guarantees never creates or mutates a file - an explicit
    "file:...?mode=ro" URI. sqlite3.connect's own audit event carries only
    the raw database string (confirmed in scratch: no separate mode/uri
    flag at all), so a plain path string - even one the caller INTENDED to
    open read-only via some other mechanism - is conservatively treated as
    write-capable, since a plain sqlite3.connect() call, by default,
    CREATES the file if it does not already exist.
    """
    if not isinstance(database, str) or not database.startswith("file:"):
        return False
    query = urllib.parse.urlsplit(database).query
    return urllib.parse.parse_qs(query).get("mode") == ["ro"]


_PYTHON_WORD = re.compile(r"\bpython3?(\.\d+)?\b")


def _system_command_invokes_an_unguarded_python(command_text: str) -> bool:
    """Backlog 2026-09-24 #3: os.system always runs via `/bin/sh -c` under
    the CALLING process's OWN environment - there is no env= parameter to
    redirect HOME the way subprocess.Popen's does, so ANY Python
    interpreter this launches is unconditionally unguarded; unlike
    _launches_an_unguarded_python_child, there is no "was HOME redirected"
    question to even ask. A word-boundary match against the whole command
    text, not argv-shape parsing - the string can be arbitrarily complex
    shell syntax (pipes, subshells, quoting) that cannot be parsed safely
    without a real shell parser, so this deliberately over-blocks rather
    than risk missing a real unguarded launch, the same preference
    _launches_real_claude_cli's own shell-string check states.
    """
    return bool(_PYTHON_WORD.search(command_text))


def _audit_real_claude_dir_guard(event: str, audit_arguments: tuple[object, ...]) -> None:
    if event == "open":
        # Confirmed in scratch: this event never carries a dir_fd, even
        # for a dir_fd-relative os.open() call - only the bare relative
        # path and the flags. That gap is closed separately, by wrapping
        # os.open itself rather than by this audit hook - see
        # _guarded_os_open and _install_real_claude_dir_guard's docstring
        # (backlog 2026-09-28 #3). This branch is left exactly as it was:
        # it still cannot see a dir_fd, and does not need to any more.
        path, mode, flags = audit_arguments
        if _is_write_open(mode, flags) and _inside_real_claude_dir(path):
            _raise(f"write-mode open() of real {path!r}")
    elif event == "os.mkdir":
        # (path, mode, dir_fd) - confirmed in scratch.
        path = audit_arguments[0]
        directory_file_descriptor = audit_arguments[2] if len(audit_arguments) > 2 else None
        if _inside_real_claude_dir(path, directory_file_descriptor):
            _raise(f"{event} of real {path!r}")
    elif event in ("os.rmdir", "os.remove", "shutil.rmtree"):
        # All three: (path, dir_fd) - confirmed in scratch. shutil.rmtree
        # fires before any recursive walk starts, so raising here
        # pre-empts the whole tree rather than racing it file by file.
        path = audit_arguments[0]
        directory_file_descriptor = audit_arguments[1] if len(audit_arguments) > 1 else None
        if _inside_real_claude_dir(path, directory_file_descriptor):
            _raise(f"{event} of real {path!r}")
    elif event == "os.truncate":
        # (path, length) - confirmed in scratch: os.truncate has NO dir_fd
        # parameter at all (unlike every other event in this function), so
        # treating audit_arguments[1] as one here would misread a byte
        # count as a file descriptor number.
        if _inside_real_claude_dir(audit_arguments[0]):
            _raise(f"{event} of real {audit_arguments[0]!r}")
    elif event == "os.chmod":
        # (path, mode, dir_fd) - confirmed in scratch. Metadata-only (no
        # data written), but still a mutation of a real file this guard
        # must refuse, same as os.utime below.
        path = audit_arguments[0]
        directory_file_descriptor = audit_arguments[2] if len(audit_arguments) > 2 else None
        if _inside_real_claude_dir(path, directory_file_descriptor):
            _raise(f"{event} of real {path!r}")
    elif event == "os.utime":
        # (path, times, ns, dir_fd) - confirmed in scratch.
        path = audit_arguments[0]
        directory_file_descriptor = audit_arguments[3] if len(audit_arguments) > 3 else None
        if _inside_real_claude_dir(path, directory_file_descriptor):
            _raise(f"{event} of real {path!r}")
    elif event == "os.rename":
        # Fired by BOTH os.rename() AND os.replace() (verified in scratch -
        # there is no separate "os.replace" event). Shape confirmed in
        # scratch: (src, dst, src_dir_fd, dst_dir_fd). Checks both sides:
        # this covers a real file being moved OUT of ~/.claude just as
        # much as something being moved in.
        source, destination = audit_arguments[0], audit_arguments[1]
        source_directory_file_descriptor = audit_arguments[2] if len(audit_arguments) > 2 else None
        destination_directory_file_descriptor = (
            audit_arguments[3] if len(audit_arguments) > 3 else None
        )
        if _inside_real_claude_dir(
            source, source_directory_file_descriptor
        ) or _inside_real_claude_dir(destination, destination_directory_file_descriptor):
            _raise(f"os.rename touching real {source!r} / {destination!r}")
    elif event == "os.symlink":
        # (link_target_text, link_path, dir_fd) - confirmed in scratch.
        # The path actually CREATED is args[1]; args[0] is just the
        # link's target text and need not exist or resolve.
        link_path = audit_arguments[1]
        directory_file_descriptor = audit_arguments[2] if len(audit_arguments) > 2 else None
        if _inside_real_claude_dir(link_path, directory_file_descriptor):
            _raise(f"{event} creating real {link_path!r}")
    elif event == "os.link":
        # (src, dst, src_dir_fd, dst_dir_fd) - confirmed in scratch. src is
        # only read; only dst (args[1]), resolved against dst_dir_fd
        # (args[3]), is a path being created.
        destination = audit_arguments[1]
        destination_directory_file_descriptor = (
            audit_arguments[3] if len(audit_arguments) > 3 else None
        )
        if _inside_real_claude_dir(destination, destination_directory_file_descriptor):
            _raise(f"{event} creating real {destination!r}")
    elif event == "shutil.copyfile":
        # (src, dst) - no dir_fd parameter at all; fires before the
        # destination is ever opened, and covers shutil.copy2 too (it
        # calls copyfile internally).
        destination = audit_arguments[1]
        if _inside_real_claude_dir(destination):
            _raise(f"{event} creating real {destination!r}")
    elif event == "sqlite3.connect":
        # (database,) - confirmed in scratch: no separate mode/uri flag.
        (database,) = audit_arguments
        if not _is_read_only_sqlite_uri(database) and _inside_real_claude_dir(database):
            _raise(f"sqlite3.connect (non-read-only) into real {database!r}")
    elif event == "os.system":
        # (command,) - confirmed in scratch: the command is reported as
        # BYTES, not str, even when called with a str argument.
        (command,) = audit_arguments
        command_text = _decoded_or_none(command)
        if command_text is None:
            return
        if _launches_real_claude_cli(None, ["/bin/sh", "-c", command_text]):
            _raise(f"os.system launch of the real `claude` CLI: {command_text!r}")
        elif _system_command_invokes_an_unguarded_python(command_text):
            _raise(
                "os.system launch of a Python interpreter - os.system always inherits "
                "the calling process's real environment with no way to redirect HOME "
                f"(unlike subprocess.Popen's env= kwarg): {command_text!r}"
            )
    elif event == "os.posix_spawn":
        # (path, argv, env) - confirmed in scratch: fired identically by
        # both os.posix_spawn and os.posix_spawnp (there is no separate
        # "os.posix_spawnp" event), and always exactly these three
        # elements regardless of which optional keyword-only arguments
        # (setsid, file_actions, ...) were also passed. Shape matches
        # subprocess.Popen's own (executable, args, env) closely enough to
        # reuse both of its checks directly - env here is a MANDATORY
        # positional argument (unlike Popen's optional env= kwarg), but a
        # caller can still hand it one that does not redirect HOME.
        path, argv, environment = audit_arguments
        if _launches_real_claude_cli(path, argv):
            _raise("os.posix_spawn launch of the real `claude` CLI from a test")
        elif _launches_an_unguarded_python_child(path, argv, environment):
            _raise(
                "os.posix_spawn launch of a Python child whose HOME was not redirected "
                "away from the real one - sys.addaudithook installs are per-process, so "
                "this guard cannot see what such a child does; pass env=dict(os.environ, "
                "HOME=<tmp dir>, ...) the same way subprocess.Popen callers must"
            )
    elif event == "subprocess.Popen":
        executable, popen_arguments, _cwd, environment = audit_arguments
        if _launches_real_claude_cli(executable, popen_arguments):
            _raise("subprocess launch of the real `claude` CLI from a test")
        elif _launches_an_unguarded_python_child(executable, popen_arguments, environment):
            _raise(
                "subprocess launch of a Python child whose HOME was not redirected away "
                "from the real one - sys.addaudithook installs are per-process, so this "
                "guard cannot see what such a child does; pass env=dict(os.environ, "
                "HOME=<tmp dir>, ...) the way tests/conftest.py's own dashboard_page "
                "fixture and tests/test_language_leak.py's fixture_page_html do"
            )


def _install_real_claude_dir_guard() -> None:
    """Installed ONCE, at import time of this module, for the whole test
    session - never per test - because sys.addaudithook hooks can only be
    ADDED, never removed; a per-test install would stack one hook per test
    and slow every open() in the suite down for nothing.

    This is the class fix for the 2026-09-23 incident (see
    tests/test_real_metrics_isolation.py): patching metrics_core path
    constants one by one will always miss the next one; this hook fires on
    the underlying filesystem/process primitives directly, in THIS
    process, covering every test that runs inside it regardless of what
    that test does or forgets to do.

    Stated honestly (fix round 1, Important 2 review comment: the
    original wording here claimed this guard blocks "regardless of what
    any test does," which is not true) - what this guard still cannot
    close, after backlog 2026-09-24 #3 closed every OTHER documented gap
    (os.chmod/os.utime, sqlite3.connect, os.system, os.posix_spawn/
    os.posix_spawnp, and a `claude` launched through its resolved
    VERSIONED install path):

    - sys.addaudithook installs are PER-PROCESS. A subprocess.Popen'd (or
      os.posix_spawn'd) Python child runs in its own process, entirely
      outside this hook - _launches_an_unguarded_python_child only checks,
      from the parent side, whether such a child's env redirects HOME away
      from the real one before it starts; it cannot see anything the child
      does once it has. A non-Python child launched this way is covered
      only by _launches_real_claude_cli's own argv check, nothing more. A
      Python child launched via os.system is ALWAYS unguarded - os.system
      has no env= parameter at all to redirect HOME with, so this guard
      refuses any command that looks like it invokes one outright (see
      _system_command_invokes_an_unguarded_python), rather than trying to
      ask a question os.system's own API makes impossible to answer.
    - A read-only sqlite3.connect is detected only via the one shape
      sqlite3 itself guarantees never creates or mutates anything - an
      explicit "file:...?mode=ro" URI (see _is_read_only_sqlite_uri). The
      audit event does not carry a `uri=` flag at all, so a plain path
      string that some OTHER, unenforced convention intended as read-only
      is still (correctly, conservatively) treated as write-capable.

    Every audit event this hook DOES act on was confirmed to actually
    fire, with the exact argument shapes assumed above, by a throwaway
    scratch experiment against this interpreter (Python 3.14.7 / macOS) -
    not from documentation alone. That included the dir_fd-resolution
    primitive itself: macOS's own os.readlink(f"/dev/fd/{fd}") does NOT
    work for a directory fd (confirmed in scratch: OSError(22, "Invalid
    argument")) - _resolve_directory_file_descriptor uses fcntl(fd, F_GETPATH, ...)
    there instead, falling back to the simpler /proc/self/fd readlink on
    Linux. Both branches now run for real, each on its own platform: the
    Linux one on this repo's own ubuntu-latest CI (confirmed by a real
    green CI run exercising TestDirFdRelativeOperationsAreBlocked - backlog
    2026-09-28 #3's original question, closed by that evidence, not by a
    test written here), the macOS one on the author's own machine.

    One gap this docstring used to list here - a dir_fd-relative WRITE
    through os.open() itself - is closed too, but NOT by this hook: the
    "open" audit event's own arguments are still exactly (path, mode,
    flags), with no dir_fd, so this function could never see it. Instead,
    _install_real_claude_dir_guard (below) also replaces os.open itself
    with _guarded_os_open, which resolves a dir_fd the same way os.mkdir/
    os.remove/... already do. See _guarded_os_open's own docstring.
    """
    sys.addaudithook(_audit_real_claude_dir_guard)
    os.open = _guarded_os_open


_install_real_claude_dir_guard()


@pytest.fixture(autouse=True)
def _fail_loudly_on_swallowed_guard_hits() -> Iterator[None]:
    """Fix round 1, Important 1(b): a second, independent line of defense
    on top of RealClaudeDirWrite being a BaseException. Even a downstream
    `except BaseException:` (rare, but the guard cannot rule it out any
    more than it could rule out `except Exception:` before this fix) would
    still leave a regressed test looking green - the write is still
    prevented, but nothing says so. This fixture makes that failure LOUD
    instead: any test whose body (or whose OTHER fixtures' teardown) causes
    a guard hit that nothing re-raised past THIS fixture's own teardown
    fails here, by name, with the recorded message(s).

    Declared before _isolate_from_real_metrics_by_default on purpose, so it
    is the OUTERMOST autouse fixture: pytest tears fixtures down in the
    reverse of their setup order, so this one's own teardown (the assert
    below) runs LAST - after every other fixture in the test, not just the
    test body itself, has already torn down. A guard hit recorded during
    another fixture's cleanup would otherwise go unnoticed by a narrower
    check.

    A test that intentionally triggers a hit (pytest.raises(RealClaudeDirWrite),
    or exercising a known swallow site like _run_label_prompt or a hook's
    main()) MUST clear REAL_CLAUDE_DIRECTORY_WRITE_HITS itself before returning -
    otherwise this fixture fails even a test that behaved exactly as
    expected, which is deliberate: an uncleared hit means the test asserted
    on the exception but never confirmed the guard was the one that
    prevented the write, which is the whole point of recording hits at all.
    """
    REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()
    yield
    hits = list(REAL_CLAUDE_DIRECTORY_WRITE_HITS)
    REAL_CLAUDE_DIRECTORY_WRITE_HITS.clear()
    assert not hits, (
        "the real-~/.claude write guard fired during this test but nothing "
        f"propagated it all the way out: {hits} - if this test means to trigger "
        "a hit on purpose, it must assert on RealClaudeDirWrite AND clear "
        "conftest.REAL_CLAUDE_DIRECTORY_WRITE_HITS itself before returning"
    )


@pytest.fixture(autouse=True)
def _isolate_from_real_metrics_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Every test starts with metrics_core's own path constants pointed at
    a throwaway HOME by default, so ordinary test code (which never means
    to touch ~/.claude at all) doesn't have to think about isolation.

    This is a CONVENIENCE, not the safety net - that is the process-level
    sys.addaudithook guard installed above (_install_real_claude_dir_guard/
    RealClaudeDirWrite), which blocks a real write or a real `claude`
    launch regardless of what this fixture does. This fixture only saves
    every ordinary test from having to patch six constants by hand; see
    tests/test_real_metrics_isolation.py for the guard itself.

    Added 2026-09-23 (Task 5, Fix round 1) after two tests in
    tests/test_tui_unbound.py called dashboard_tui.load_data() with no
    isolation at all, migrating the real task_labels.json/agent_labels.json
    to the new per-language shape and creating their one-time .pre-i18n
    backups - see docs/superpowers for the incident.

    METRICS_FILE, SUBAGENTS_FILE, TASK_LABELS_FILE, AGENT_LABELS_FILE,
    BACKFILL_STATE_FILE and CONFIG_FILE are module-level constants resolved
    from HOME at IMPORT time (each module's own docstring says so) - a bare
    monkeypatch.setenv("HOME", ...) does nothing to them, since metrics_core
    was already imported by the time any test runs. They are patched
    directly here instead, mirroring tests/test_tui_state.py's
    isolate_tui_from_the_real_filesystem (which this fixture does not
    replace - a test that calls it, or seeded_tui_home, still does so
    inside its own body, AFTER this fixture has already run, so its own
    throwaway HOME simply overrides the default set here).

    This fixture does not build fixture data, seed a config, or pin the
    locale - it only guarantees no test can reach the real directory by
    omission. A test that needs real session data, a real config, or a
    pinned language still calls isolate_tui_from_the_real_filesystem /
    seeded_tui_home / builds its own tmp_path, exactly as before.
    """
    metrics = tmp_path_factory.mktemp("default-metrics-isolation") / ".claude" / "metrics"
    metrics.mkdir(parents=True)
    monkeypatch.setattr(mr, "METRICS_FILE", str(metrics / "sessions.jsonl"))
    monkeypatch.setattr(mr, "SUBAGENTS_FILE", str(metrics / "subagents.jsonl"))
    monkeypatch.setattr(labels_module, "TASK_LABELS_FILE", str(metrics / "task_labels.json"))
    monkeypatch.setattr(labels_module, "AGENT_LABELS_FILE", str(metrics / "agent_labels.json"))
    monkeypatch.setattr(
        backfill_module, "BACKFILL_STATE_FILE", str(metrics / "backfill_state.json")
    )
    monkeypatch.setattr(paths_module, "CONFIG_FILE", str(metrics / "config.json"))


class DashboardPage:
    def __init__(self, home: str, html_path: str, expected: dict[str, Any]) -> None:
        self.home = home
        self.html_path = html_path
        self.expected = expected


@pytest.fixture(scope="session")
def dashboard_page() -> Iterator[DashboardPage]:
    with tempfile.TemporaryDirectory() as home:
        now = datetime.now().astimezone()
        built = build_fixture(home, now)
        # Pinned the same two ways tests/test_tui_state.py pins the TUI's
        # HOME - config.json (the real mechanism) and the locale variables
        # (for symmetry, and so this fixture never depends on the locale of
        # the machine running the suite).
        (Path(home) / ".claude" / "metrics" / "config.json").write_text(
            json.dumps({"language": "pt"})
        )

        environment = dict(
            os.environ,
            HOME=home,
            PYTHONPATH=str(REPO_ROOT / "lib"),
            LC_ALL="pt_BR.UTF-8",
            LC_MESSAGES="",
            LANG="",
        )
        # sys.executable, NOT "python3": python3 on PATH is an asdf shim
        # that resolves through $HOME/.asdf and exits 126 the moment HOME
        # is overridden. This cost real debugging time - do not "simplify"
        # it back to a bare python3.
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "dashboard_html.py"),
                "--html",
                "--label-budget",
                "0",
            ],
            env=environment,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"dashboard generation failed ({result.returncode})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        html_path = Path(home) / ".claude" / "metrics" / "dashboard.html"
        assert html_path.exists(), f"no dashboard at {html_path}; stdout: {result.stdout}"
        yield DashboardPage(home, str(html_path), built["expected"])
