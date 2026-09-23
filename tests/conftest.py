"""Generates one isolated dashboard for the whole test session, and isolates
EVERY test in the suite from the author's real ~/.claude/metrics by default.

HOME is overridden so every expanduser() inside the dashboard - the metrics
files, the label caches, get_open_projects, and the output path itself -
resolves inside a throwaway directory.
"""

import json
import os
import subprocess
import sys
import tempfile
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
    site also appends to REAL_CLAUDE_DIR_WRITE_HITS below (via _raise) as
    a second line of defense for the rare downstream `except BaseException:`
    - see the autouse _fail_loudly_on_swallowed_guard_hits fixture.
    """


# Every message _raise() below ever raised with, appended BEFORE raising -
# so it survives even a downstream `except BaseException:` that would
# otherwise leave no trace at all that the guard ever fired. The autouse
# _fail_loudly_on_swallowed_guard_hits fixture asserts this is empty after
# every test and clears it; a test that deliberately triggers a hit (via
# pytest.raises(RealClaudeDirWrite) or by exercising a swallow site) must
# clear it itself before returning, or that assertion fails LOUDLY instead
# of the hit disappearing unnoticed.
REAL_CLAUDE_DIR_WRITE_HITS: list[str] = []


def _raise(message: str) -> None:
    REAL_CLAUDE_DIR_WRITE_HITS.append(message)
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

# The write-ish bits the "open" audit event's int `flags` argument can
# carry. Confirmed against this exact interpreter (Python 3.14.7, macOS) in
# a throwaway scratch script: `open(p, "w")` reports flags with
# O_CREAT|O_TRUNC|O_WRONLY set; `open(p, "a")` reports
# O_CREAT|O_APPEND|O_WRONLY; a bare os.open(p, os.O_WRONLY | os.O_CREAT)
# reports exactly those two bits back. The interpreter additionally
# OR-s in O_CLOEXEC on every open (including plain reads) - irrelevant
# here since it is not one of the bits below.
_WRITE_OPEN_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _resolved_path_or_none(path_arg: object) -> Path | None:
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
    if not isinstance(path_arg, str | bytes | os.PathLike):
        return None  # e.g. an int fd - nothing to resolve
    candidate = os.fspath(path_arg)
    if isinstance(candidate, bytes):
        try:
            candidate = candidate.decode("utf-8", "surrogateescape")
        except UnicodeDecodeError:
            return None
    try:
        return Path(candidate).resolve()
    except (OSError, ValueError):
        return None


def _lexical_abspath_or_none(path_arg: object) -> Path | None:
    """Fix round 1, M1: the same argument, made absolute WITHOUT following
    any symlink (os.path.abspath never resolves one, unlike
    Path.resolve()). A target like ~/.claude/skills/escape.md, where
    skills/ happens to be a symlink pointing OUTSIDE ~/.claude, resolves
    to somewhere harmless - but it still LEXICALLY names a location
    inside the real directory, and a test should not get to rely on some
    symlink there conveniently rerouting a write elsewhere. Never raises,
    for the same reasons as _resolved_path_or_none."""
    if not isinstance(path_arg, str | bytes | os.PathLike):
        return None
    candidate = os.fspath(path_arg)
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
        candidate_s = str(candidate).casefold()
        base_s = str(base).casefold()
        return candidate_s == base_s or candidate_s.startswith(base_s + os.sep)
    return candidate.is_relative_to(base)


# Fix round 1, M1: the real .claude dir, UNRESOLVED (still absolute -
# Path.home() always is) - the lexical counterpart to _REAL_CLAUDE_DIR,
# compared against a candidate's own lexical form. See
# _lexical_abspath_or_none and _inside_real_claude_dir.
_REAL_CLAUDE_DIR_LEXICAL = Path.home() / ".claude"


def _inside_real_claude_dir(path_arg: object) -> bool:
    """True if EITHER the resolved or the lexical form of `path_arg` names
    a location inside the real ~/.claude - except for the running
    checkout's own tree (fix round 1, M4: REPO_ROOT, and everything under
    it - .pytest_cache, .coverage, __pycache__ included, since they all
    live inside REPO_ROOT), which is exempt so the suite can run from an
    installed copy under ~/.claude/plugins/cache/<name>/ without the
    guard crashing on Python's own bytecode-cache writes for its imports.
    That exemption is checked per-candidate, not globally: a write
    elsewhere inside a REAL ~/.claude that merely happens to CONTAIN such
    an installed checkout is still blocked.
    """
    for candidate, real_dir in (
        (_resolved_path_or_none(path_arg), _REAL_CLAUDE_DIR),
        (_lexical_abspath_or_none(path_arg), _REAL_CLAUDE_DIR_LEXICAL),
    ):
        if candidate is None:
            continue
        if _same_or_within(candidate, REPO_ROOT):
            continue
        if _same_or_within(candidate, real_dir):
            return True
    return False


def _is_write_open(mode: object, flags: object) -> bool:
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
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


def _launches_real_claude_cli(executable: object, popen_args: object) -> bool:
    """subprocess.Popen(executable, args, cwd, env): argv entries can be
    str, bytes, or (rarely) os.PathLike - never assume str. Matches a bare
    `claude ...` and the `nice claude ...` form (argv[0] == "nice" with
    "claude" elsewhere in argv), per the brief."""
    names = [executable] if executable is not None else []
    if isinstance(popen_args, list | tuple):
        names.extend(popen_args)
    basenames = []
    for name in names:
        decoded = _decoded_or_none(name) if not isinstance(name, os.PathLike) else os.fspath(name)
        if decoded:
            basenames.append(Path(decoded).name)
    if any(b == "claude" for b in basenames):
        return True
    return bool(basenames) and basenames[0] == "nice" and "claude" in basenames[1:]


def _audit_real_claude_dir_guard(event: str, args: tuple[object, ...]) -> None:
    if event == "open":
        path, mode, flags = args
        if _is_write_open(mode, flags) and _inside_real_claude_dir(path):
            _raise(f"write-mode open() of real {path!r}")
    elif event in ("os.mkdir", "os.rmdir", "os.remove", "os.truncate", "shutil.rmtree"):
        # All four single-path mutating events; shutil.rmtree fires before
        # any recursive walk starts, so raising here pre-empts the whole
        # tree rather than racing it file by file.
        if _inside_real_claude_dir(args[0]):
            _raise(f"{event} of real {args[0]!r}")
    elif event == "os.rename":
        # Fired by BOTH os.rename() AND os.replace() (verified in scratch -
        # there is no separate "os.replace" event). Check both sides: this
        # covers a real file being moved OUT of ~/.claude just as much as
        # something being moved in.
        src, dst = args[0], args[1]
        if _inside_real_claude_dir(src) or _inside_real_claude_dir(dst):
            _raise(f"os.rename touching real {src!r} / {dst!r}")
    elif event in ("os.symlink", "os.link", "shutil.copyfile"):
        # os.symlink: (link_target_text, link_path, dir_fd) - the path
        # actually CREATED is args[1]; args[0] is just the link's target
        # text and need not exist or resolve.
        # os.link: (src, dst, src_dir_fd, dst_dir_fd) - src is only read.
        # shutil.copyfile: (src, dst) - fires before the destination is
        # ever opened, and covers shutil.copy2 too (it calls copyfile
        # internally).
        # All three: only the SECOND element is a path being created/
        # written.
        if _inside_real_claude_dir(args[1]):
            _raise(f"{event} creating real {args[1]!r}")
    elif event == "subprocess.Popen":
        executable, popen_args = args[0], args[1]
        if _launches_real_claude_cli(executable, popen_args):
            _raise("subprocess launch of the real `claude` CLI from a test")


def _install_real_claude_dir_guard() -> None:
    """Installed ONCE, at import time of this module, for the whole test
    session - never per test - because sys.addaudithook hooks can only be
    ADDED, never removed; a per-test install would stack one hook per test
    and slow every open() in the suite down for nothing.

    This is the class fix for the 2026-09-23 incident (see
    tests/test_real_metrics_isolation.py): patching metrics_core path
    constants one by one will always miss the next one; this hook fires on
    the underlying filesystem/process primitives directly; no test can
    reach the real ~/.claude by writing to it or by shelling out to the
    real `claude` CLI, regardless of what any test does or forgets to do.

    Every audit event named in _audit_real_claude_dir_guard was confirmed
    to actually fire, with the exact argument shapes assumed above, by a
    throwaway scratch experiment against this interpreter (Python 3.14.7 /
    macOS) - not from documentation alone.
    """
    sys.addaudithook(_audit_real_claude_dir_guard)


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
    main()) MUST clear REAL_CLAUDE_DIR_WRITE_HITS itself before returning -
    otherwise this fixture fails even a test that behaved exactly as
    expected, which is deliberate: an uncleared hit means the test asserted
    on the exception but never confirmed the guard was the one that
    prevented the write, which is the whole point of recording hits at all.
    """
    REAL_CLAUDE_DIR_WRITE_HITS.clear()
    yield
    hits = list(REAL_CLAUDE_DIR_WRITE_HITS)
    REAL_CLAUDE_DIR_WRITE_HITS.clear()
    assert not hits, (
        "the real-~/.claude write guard fired during this test but nothing "
        f"propagated it all the way out: {hits} - if this test means to trigger "
        "a hit on purpose, it must assert on RealClaudeDirWrite AND clear "
        "conftest.REAL_CLAUDE_DIR_WRITE_HITS itself before returning"
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

        env = dict(
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
            env=env,
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
