"""Tests for scripts/verify-detectors.py.

The first group covers clear_pycache()'s worktree fix (Task 1 of the
coverage phase, a real production bug); the rest covers the whole script.

SAFETY, first, because this is the most dangerous module in the repository.
main() walks a module-level DEFECTS list whose entries hold ABSOLUTE paths
to two real repo source files (dashboard_html.py and
dashboard_html_assets.py). For each defect it copies that file, rewrites it,
runs the ENTIRE pytest suite in a subprocess with a 300-second timeout, and
only then restores it. A test that reaches main() without substituting
DEFECTS rewrites this repo's own source while a suite is running inside it,
and the restore path is itself one of the things under test, so it cannot be
relied on as the undo.

Every test here that reaches main() therefore replaces all five dangerous
seams: DEFECTS (wholesale, with defects whose target lives under tmp_path -
never a filtered or mutated copy of the real list), run_suite (a stub that
returns a fabricated CompletedProcess and counts its calls), REPO_ROOT (used
by clear_pycache and by the final git check), subprocess.run (the git check)
and clear_pycache itself. The module-scoped `real_sources_untouched` fixture
below hashes both real files before the first test in this module and again
after the last one, so a leak cannot pass unnoticed even if a seam is
forgotten.
"""

import hashlib
import importlib.util
import shutil
import subprocess
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "verify-detectors.py"
REPO_ROOT = SCRIPT_PATH.parent.parent
BACKUP_SUFFIX = ".verify-detectors.bak"
# The two files the real DEFECTS point at. Named here only so the guard
# fixture can prove they were never touched - no test may use them as a
# patch target.
REAL_TARGETS = (REPO_ROOT / "dashboard_html.py", REPO_ROOT / "dashboard_html_assets.py")


def _backup_of(target: Path) -> Path:
    return target.with_suffix(target.suffix + BACKUP_SUFFIX)


@pytest.fixture(scope="module", autouse=True)
def real_sources_untouched() -> Iterator[None]:
    """Records both real targets' sha256 before the first test and asserts
    them unchanged after the last one, plus that no .verify-detectors.bak
    was left beside either. This is the backstop for a forgotten seam: a
    test that rewrites a real source file fails the module, loudly, even if
    its own assertions passed."""
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in REAL_TARGETS}
    yield
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in REAL_TARGETS}
    changed = sorted(str(path) for path in REAL_TARGETS if before[path] != after[path])
    assert changed == [], f"a test rewrote real repo source: {changed}"
    leaked = sorted(str(_backup_of(path)) for path in REAL_TARGETS if _backup_of(path).exists())
    assert leaked == [], f"a test left a backup behind: {leaked}"


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    # The filename has a hyphen, so it cannot be imported with a normal
    # `import` statement - load it as a module by path instead.
    spec = importlib.util.spec_from_file_location("verify_detectors", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# clear_pycache()
# --------------------------------------------------------------------------


def _make_pycache(directory: Path) -> Path:
    cache_directory = directory / "__pycache__"
    cache_directory.mkdir(parents=True)
    (cache_directory / "mod.cpython-314.pyc").write_bytes(b"")
    return cache_directory


def test_clears_a_cache_directly_under_a_worktree_root(tmp_path, script, monkeypatch):
    # REPO_ROOT itself lives under a .claude path, matching this project's
    # own worktree convention (.claude/worktrees/<name>/) - a cache
    # directly under it must still be removed.
    root = tmp_path / ".claude" / "worktrees" / "coverage-phase3d"
    root.mkdir(parents=True)
    cache_directory = _make_pycache(root / "lib")
    monkeypatch.setattr(script, "REPO_ROOT", root)

    script.clear_pycache()

    assert not cache_directory.exists()


def test_preserves_a_nested_worktree_cache_below_the_root(tmp_path, script, monkeypatch):
    # A worktree root that ITSELF lives under .claude can also contain a
    # nested .claude/worktrees/... subtree of its own (a worktree spawned
    # from inside another worktree). That nested scratch must stay
    # untouched, while a cache directly under the root is still cleared -
    # proving the fix narrows the skip rather than removing it entirely.
    root = tmp_path / ".claude" / "worktrees" / "coverage-phase3d"
    root.mkdir(parents=True)
    own_cache = _make_pycache(root / "lib")
    nested_cache = _make_pycache(root / ".claude" / "worktrees" / "nested" / "lib")
    monkeypatch.setattr(script, "REPO_ROOT", root)

    script.clear_pycache()

    assert not own_cache.exists()
    assert nested_cache.exists()


def test_clears_caches_when_the_root_has_no_claude_anywhere(tmp_path, script, monkeypatch):
    # The original, unaffected case: a plain checkout with no .claude
    # path involved at all.
    root = tmp_path / "main-checkout"
    root.mkdir()
    cache_directory = _make_pycache(root / "lib")
    monkeypatch.setattr(script, "REPO_ROOT", root)

    script.clear_pycache()

    assert not cache_directory.exists()


def test_tolerates_a_cache_directory_vanishing_before_rmtree_runs(tmp_path, script, monkeypatch):
    # A cache directory can disappear between rglob() yielding it and
    # shutil.rmtree() acting on it (e.g. a concurrent cleanup process).
    # clear_pycache() already passes ignore_errors=True to cover exactly
    # this; this pins that it actually works, rather than adding new code
    # to handle it.
    root = tmp_path / "main-checkout"
    root.mkdir()
    cache_directory = _make_pycache(root / "lib")
    monkeypatch.setattr(script, "REPO_ROOT", root)
    real_rmtree = shutil.rmtree

    def rmtree_after_it_vanished(path: Path, ignore_errors: bool = False) -> None:
        real_rmtree(path, ignore_errors=True)  # simulate the concurrent removal
        real_rmtree(path, ignore_errors=ignore_errors)  # what clear_pycache() actually asked for

    monkeypatch.setattr(script.shutil, "rmtree", rmtree_after_it_vanished)

    script.clear_pycache()  # must not raise

    assert not cache_directory.exists()


# --------------------------------------------------------------------------
# sha256()
# --------------------------------------------------------------------------


def test_sha256_matches_hashlib_over_the_same_bytes(tmp_path, script):
    target = tmp_path / "mod.py"
    target.write_bytes(b"one line\nand another\n")

    assert script.sha256(target) == hashlib.sha256(b"one line\nand another\n").hexdigest()


def test_sha256_changes_after_a_single_byte_edit(tmp_path, script):
    # A same-length edit is the case the script's own docstring worries
    # about: the restore check must notice it.
    target = tmp_path / "mod.py"
    target.write_bytes(b"one line\n")
    before = script.sha256(target)
    target.write_bytes(b"one lino\n")

    assert script.sha256(target) != before


# --------------------------------------------------------------------------
# failed_test_names()
# --------------------------------------------------------------------------


def test_failed_test_names_extracts_failed_and_error_node_ids_sorted_and_deduplicated(script):
    output = (
        "=== short test summary info ===\n"
        "FAILED tests/test_b.py::test_two\n"
        "FAILED tests/test_a.py::test_one\n"
        "FAILED tests/test_b.py::test_two\n"
        "ERROR tests/test_c.py::test_three\n"
    )

    assert script.failed_test_names(output) == [
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_two",
        "tests/test_c.py::test_three",
    ]


def test_failed_test_names_is_empty_when_the_output_carries_no_summary_lines(script):
    assert script.failed_test_names("collected 12 items\n12 passed in 3.1s\n") == []


def test_failed_test_names_ignores_the_word_failed_in_the_middle_of_a_line(script):
    # Assertion messages and progress lines mention FAILED constantly; only
    # pytest's own summary lines, anchored to the start of a line, name a
    # node id. Matching mid-line would invent failures that never happened.
    output = "E   assert FAILED tests/test_a.py::test_one in report\n1 failed\n"

    assert script.failed_test_names(output) == []


# --------------------------------------------------------------------------
# Defect.patch() / Defect.missing_evidence()
# --------------------------------------------------------------------------


def _defect(
    script: types.ModuleType,
    root: Path,
    name: str = "the fake defect",
    filename: str = "mod.py",
    body: str = "before ANCHOR after\n",
    anchor: str = "ANCHOR",
    replacement: str = "REPLACED",
    expected_tests: tuple[str, ...] = ("tests/test_fake.py::test_it",),
    expected_labels: tuple[str, ...] = ("fake label",),
) -> Any:
    """Builds a Defect whose target is a real file under tmp_path. Every
    Defect in this module is built here; the real DEFECTS list is never
    filtered, mutated or partially reused."""
    target = root / filename
    if not target.exists():
        target.write_text(body)
    return script.Defect(
        name=name,
        target=target,
        description=f"description of {name}",
        anchor=anchor,
        replacement=replacement,
        expected_tests=list(expected_tests),
        expected_labels=list(expected_labels),
    )


def test_patch_replaces_the_anchor_when_it_occurs_exactly_once(tmp_path, script):
    defect = _defect(script, tmp_path)

    assert defect.patch("before ANCHOR after\n") == "before REPLACED after\n"


def test_patch_refuses_and_names_the_defect_when_the_anchor_is_absent(tmp_path, script):
    # Matching nothing is the false-green shape the whole script exists to
    # eliminate: the defect would look "undetectable" when it was never
    # injected at all.
    defect = _defect(script, tmp_path, name="the vanished anchor")

    with pytest.raises(RuntimeError) as excinfo:
        defect.patch("nothing to match here\n")

    assert "the vanished anchor" in str(excinfo.value)
    assert "0 time(s)" in str(excinfo.value)


def test_patch_refuses_when_the_anchor_occurs_twice(tmp_path, script):
    # Two matches means the file drifted: replacing only the first would
    # inject a different defect than the one described.
    defect = _defect(script, tmp_path, name="the ambiguous anchor")

    with pytest.raises(RuntimeError) as excinfo:
        defect.patch("ANCHOR and again ANCHOR\n")

    assert "the ambiguous anchor" in str(excinfo.value)
    assert "2 time(s)" in str(excinfo.value)


def test_missing_evidence_is_empty_when_the_test_failed_and_the_label_appeared(tmp_path, script):
    defect = _defect(script, tmp_path)

    missing = defect.missing_evidence(
        ["tests/test_fake.py::test_it"],
        "E   FAIL fake label: 3 != 4\n",
    )

    assert missing == []


def test_missing_evidence_reports_the_expected_test_that_did_not_fail(tmp_path, script):
    # The label is present, so the suite is red for a related reason - but
    # not through the test this defect claims proves it.
    defect = _defect(script, tmp_path)

    missing = defect.missing_evidence(
        ["tests/test_other.py::test_unrelated"],
        "E   FAIL fake label: 3 != 4\n",
    )

    assert missing == ["expected failing test tests/test_fake.py::test_it"]


def test_missing_evidence_reports_the_harness_label_that_never_fired(tmp_path, script):
    # The right test failed, but for some other assertion - a syntax error
    # in the patched file, or an unrelated ratchet. Red for the wrong
    # reason is not detection.
    defect = _defect(script, tmp_path)

    missing = defect.missing_evidence(
        ["tests/test_fake.py::test_it"],
        "E   FAIL some other label: boom\n",
    )

    assert missing == ["expected harness assertion 'fake label' to fail"]


def test_missing_evidence_reports_both_when_neither_appeared(tmp_path, script):
    defect = _defect(script, tmp_path)

    missing = defect.missing_evidence([], "collected 12 items\n12 passed\n")

    assert missing == [
        "expected failing test tests/test_fake.py::test_it",
        "expected harness assertion 'fake label' to fail",
    ]


# --------------------------------------------------------------------------
# The real DEFECTS list
# --------------------------------------------------------------------------


def test_every_real_defect_anchor_still_matches_its_target(script):
    # The class guard that would have caught the unscoped-totals anchor
    # going stale after the mr -> metrics_core_module rename: the anchor
    # matched 0 times and nothing here noticed until this test was written.
    # Pure: reads each defect's real target off disk and calls patch() on
    # the text in memory only - never writes anything back, never touches
    # main() or run_suite(), never runs the suite.
    for defect in script.DEFECTS:
        original_text = defect.target.read_text()
        patched_text = defect.patch(original_text)
        assert patched_text != original_text


# --------------------------------------------------------------------------
# run_suite()
# --------------------------------------------------------------------------


def test_run_suite_invokes_this_interpreter_on_tests_with_the_pythonpath_and_timeout(
    tmp_path, script, monkeypatch
):
    # The one test that touches run_suite's body. subprocess.run is stubbed,
    # so no pytest process is ever spawned; what is pinned is the exact
    # command it WOULD spawn, because each argument here was chosen for a
    # documented reason (sys.executable over "python3" because plain python3
    # is an asdf shim that exits 126 under an overridden HOME; -vv over -v
    # because -v truncates the harness FAIL lines the script needs).
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setenv("METRICS_SKIP_JS_TESTS", "1")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(
        command: list[str], **keyword_arguments: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append((command, keyword_arguments))
        return subprocess.CompletedProcess(args=command, returncode=7, stdout="out", stderr="err")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    result = script.run_suite()

    assert result.returncode == 7
    (command, keyword_arguments) = calls[0]
    assert command == [sys.executable, "-m", "pytest", "tests/", "-vv"]
    assert keyword_arguments["cwd"] == tmp_path
    assert keyword_arguments["timeout"] == 300
    assert keyword_arguments["capture_output"] is True
    assert keyword_arguments["text"] is True
    environment = keyword_arguments["env"]
    assert isinstance(environment, dict)
    assert environment["PYTHONPATH"] == "lib:tests"
    # A skipped rendering test leaves the suite green on an injected defect
    # - the same false green as a patch that matched nothing.
    assert "METRICS_SKIP_JS_TESTS" not in environment


# --------------------------------------------------------------------------
# restore()
# --------------------------------------------------------------------------


def test_restore_copies_the_backup_back_verifies_and_unlinks_it(tmp_path, script, monkeypatch):
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    target = tmp_path / "mod.py"
    target.write_text("original\n")
    backup = _backup_of(target)
    shutil.copy2(target, backup)
    original_hash = script.sha256(target)
    target.write_text("patched, and a different length\n")

    assert script.restore(target, backup, original_hash) is True
    assert target.read_text() == "original\n"
    assert not backup.exists()


def test_restore_reports_false_and_still_unlinks_the_backup_when_the_hash_disagrees(
    tmp_path, script, monkeypatch
):
    # A leaked backup is what trips the stale-backup guard on the NEXT run,
    # turning one failure into a permanently wedged script - so the unlink
    # has to happen on the failing path too.
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    target = tmp_path / "mod.py"
    target.write_text("what the backup holds\n")
    backup = _backup_of(target)
    shutil.copy2(target, backup)
    hash_of_something_else = hashlib.sha256(b"a different original").hexdigest()

    assert script.restore(target, backup, hash_of_something_else) is False
    assert not backup.exists()


# --------------------------------------------------------------------------
# main()
# --------------------------------------------------------------------------


class _SuiteStub:
    """Stands in for run_suite(), which really shells out to a full pytest
    run with a 300-second timeout. Returns canned results (the last one
    repeats) and counts its calls, so a test can prove the loop continued
    past a defect - or broke before reaching the next one."""

    def __init__(self, *results: subprocess.CompletedProcess[str]) -> None:
        self._results = list(results)
        self.calls = 0

    def __call__(self) -> subprocess.CompletedProcess[str]:
        self.calls += 1
        return self._results[min(self.calls - 1, len(self._results) - 1)]


class _GitStub:
    """Stands in for the final `git diff --exit-code` check."""

    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(
        self, command: list[str], **keyword_arguments: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, keyword_arguments))
        return subprocess.CompletedProcess(
            args=command, returncode=self.returncode, stdout=self.stdout, stderr=""
        )


class _ClearStub:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def _red(stdout: str, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["pytest"], returncode=1, stdout=stdout, stderr=stderr)


def _green(stdout: str = "12 passed\n") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["pytest"], returncode=0, stdout=stdout, stderr="")


def _evidence_for(defect: Any) -> str:
    """The pytest output a genuinely-detected defect produces: its expected
    node id in a summary line, and its harness label as a FAIL line inside
    the assertion message."""
    lines = [f"FAILED {name}" for name in defect.expected_tests]
    lines += [f"E   FAIL {label}: 3 != 4" for label in defect.expected_labels]
    return "=== short test summary info ===\n" + "\n".join(lines) + "\n"


def _isolate(
    monkeypatch: pytest.MonkeyPatch,
    script: types.ModuleType,
    root: Path,
    defects: list[Any],
    suite: _SuiteStub,
    git: _GitStub | None = None,
) -> tuple[_GitStub, _ClearStub]:
    """Installs all five dangerous seams at once. Every main() test calls
    this; none of them may reach the real DEFECTS, the real run_suite, the
    real REPO_ROOT, the real subprocess.run or the real clear_pycache."""
    git = git if git is not None else _GitStub()
    clear = _ClearStub()
    monkeypatch.setattr(script, "REPO_ROOT", root)
    monkeypatch.setattr(script, "DEFECTS", defects)
    monkeypatch.setattr(script, "run_suite", suite)
    monkeypatch.setattr(script, "clear_pycache", clear)
    monkeypatch.setattr(script.subprocess, "run", git)
    return (git, clear)


def test_main_returns_two_and_touches_nothing_when_any_defect_has_a_stale_backup(
    tmp_path, script, monkeypatch, capsys
):
    # The stale backup sits on the SECOND defect, so passing this requires
    # scanning the whole list before starting - not just checking the first.
    # Exit code 2 is distinct from 1 on purpose: this is "a previous run
    # left a mess, resolve it by hand", not "a defect went undetected".
    first = _defect(script, tmp_path, name="first", filename="one.py")
    second = _defect(script, tmp_path, name="second", filename="two.py")
    stale = _backup_of(second.target)
    stale.write_text("left over from a crashed run\n")
    suite = _SuiteStub(_red(_evidence_for(first)))
    _isolate(monkeypatch, script, tmp_path, [first, second], suite)

    assert script.main() == 2

    captured = capsys.readouterr()
    assert "stale backup already at" in captured.err
    assert str(stale) in captured.err
    assert suite.calls == 0
    assert first.target.read_text() == "before ANCHOR after\n"
    assert second.target.read_text() == "before ANCHOR after\n"
    assert not _backup_of(first.target).exists()


def test_main_detects_a_defect_and_returns_zero_when_everything_verifies(
    tmp_path, script, monkeypatch, capsys
):
    defect = _defect(script, tmp_path)
    suite = _SuiteStub(_red(_evidence_for(defect)))
    (git, clear) = _isolate(monkeypatch, script, tmp_path, [defect], suite)

    assert script.main() == 0

    captured = capsys.readouterr()
    assert "=> DETECTED" in captured.out
    assert "MISSING EVIDENCE" not in captured.out
    # The banner is the run's only record of WHICH edit produced the
    # result below it; without it a DETECTED verdict names no injection.
    assert "patch applied:\n  - ANCHOR\n  + REPLACED\n" in captured.out
    assert "Both defects DETECTED" in captured.out
    assert suite.calls == 1
    # The target was really patched and really restored, by this run.
    assert defect.target.read_text() == "before ANCHOR after\n"
    assert not _backup_of(defect.target).exists()
    # Once before the suite runs and once inside restore(): a same-length
    # edit within the same second can otherwise reuse a stale .pyc.
    assert clear.calls == 2
    assert git.calls[0][0] == ["git", "diff", "--exit-code", "--", str(defect.target)]
    assert git.calls[0][1]["cwd"] == tmp_path


def test_main_reports_not_detected_when_the_suite_went_red_for_another_reason(
    tmp_path, script, monkeypatch, capsys
):
    # The central claim of this whole script: red is not detection. The
    # suite failed, but neither the node id this defect names nor its
    # harness label is in the output, so something else broke.
    defect = _defect(script, tmp_path)
    suite = _SuiteStub(
        _red(
            "STDOUT-MARKER\nFAILED tests/test_unrelated.py::test_other\n",
            "STDERR-MARKER\n",
        )
    )
    _isolate(monkeypatch, script, tmp_path, [defect], suite)

    assert script.main() == 1

    captured = capsys.readouterr()
    assert "=> NOT DETECTED" in captured.out
    assert "MISSING EVIDENCE" in captured.out
    assert "expected failing test tests/test_fake.py::test_it" in captured.out
    assert "expected harness assertion 'fake label' to fail" in captured.out
    # The full pytest output is printed so the reader can see what DID break.
    assert "--- full pytest output (defect was NOT caught) ---" in captured.out
    assert "STDOUT-MARKER" in captured.out
    assert "STDERR-MARKER" in captured.out
    assert "FAILURE: at least one defect went undetected" in captured.out


def test_main_reports_not_detected_for_a_green_suite_even_with_the_evidence_present(
    tmp_path, script, monkeypatch, capsys
):
    # The mirror image of the test above: all the evidence strings are
    # there, but the suite exited 0. A defect nobody's suite failed on is
    # not detected, whatever the text says.
    defect = _defect(script, tmp_path)
    suite = _SuiteStub(_green(_evidence_for(defect)))
    _isolate(monkeypatch, script, tmp_path, [defect], suite)

    assert script.main() == 1

    captured = capsys.readouterr()
    assert "suite went red: False" in captured.out
    assert "=> NOT DETECTED" in captured.out
    assert "MISSING EVIDENCE" not in captured.out


def test_main_continues_to_the_next_defect_when_an_anchor_does_not_match(
    tmp_path, script, monkeypatch, capsys
):
    # An anchor that matched nothing is a script problem, not a suite
    # problem: report it, clean up, and still exercise the other defects
    # rather than abandoning the run.
    broken = _defect(
        script, tmp_path, name="the drifted anchor", filename="one.py", anchor="NOT-IN-THE-FILE"
    )
    good = _defect(script, tmp_path, name="the good one", filename="two.py")
    suite = _SuiteStub(_red(_evidence_for(good)))
    _isolate(monkeypatch, script, tmp_path, [broken, good], suite)

    assert script.main() == 1

    captured = capsys.readouterr()
    assert "INJECTION FAILED" in captured.err
    assert "the drifted anchor" in captured.err
    # The loop continued: the second defect ran, and detection is reported
    # for it even though the run as a whole fails.
    assert suite.calls == 1
    assert "the drifted anchor: INJECTION FAILED" in captured.out
    assert "the good one: DETECTED" in captured.out
    assert "error: anchor for defect 'the drifted anchor'" in captured.out
    # The backup taken before the failed injection is cleaned up, or the
    # next run trips the stale-backup guard.
    assert not _backup_of(broken.target).exists()
    assert broken.target.read_text() == "before ANCHOR after\n"


def test_main_breaks_out_of_the_loop_when_a_restore_does_not_verify(
    tmp_path, script, monkeypatch, capsys
):
    # A failed restore means the working tree is corrupted. Continuing
    # would test the next defect against a baseline that is already wrong,
    # so the loop must BREAK - the second defect is never touched.
    first = _defect(script, tmp_path, name="first", filename="one.py")
    second = _defect(script, tmp_path, name="second", filename="two.py")
    suite = _SuiteStub(_red(_evidence_for(first)), _red(_evidence_for(second)))
    _isolate(monkeypatch, script, tmp_path, [first, second], suite)
    monkeypatch.setattr(script, "restore", lambda target, backup, original_hash: False)

    assert script.main() == 1

    captured = capsys.readouterr()
    assert "RESTORE FAILED" in captured.err
    assert suite.calls == 1, "the loop continued past a corrupted baseline"
    assert "first: RESTORE FAILED" in captured.out
    assert "second" not in captured.out.split("SUMMARY")[1]
    assert second.target.read_text() == "before ANCHOR after\n"
    assert not _backup_of(second.target).exists()


def test_main_fails_when_git_disagrees_even_though_the_defect_was_detected(
    tmp_path, script, monkeypatch, capsys
):
    # The independent second opinion. Every sha256 check above can pass and
    # git can still see a modified file; when it does, the run fails.
    defect = _defect(script, tmp_path)
    suite = _SuiteStub(_red(_evidence_for(defect)))
    git = _GitStub(returncode=1, stdout="diff --git a/mod.py b/mod.py\n-before\n+after\n")
    _isolate(monkeypatch, script, tmp_path, [defect], suite, git=git)

    assert script.main() == 1

    captured = capsys.readouterr()
    assert "=> DETECTED" in captured.out
    assert "restore verified byte-identical (sha256): True" in captured.out
    assert "git diff --exit-code disagrees: mod.py is NOT clean after restore." in captured.err
    assert "diff --git a/mod.py b/mod.py" in captured.err
    assert "FAILURE: at least one defect went undetected" in captured.out


def test_main_runs_the_git_check_once_per_distinct_target(tmp_path, script, monkeypatch):
    # Two defects can share one target file; the git check is per FILE, not
    # per defect, so it must be asked once.
    body = "before ANCHOR after\nand ALSO here\n"
    first = _defect(script, tmp_path, name="first", filename="shared.py", body=body)
    second = _defect(
        script,
        tmp_path,
        name="second",
        filename="shared.py",
        body=body,
        anchor="ALSO",
        replacement="INSTEAD",
    )
    suite = _SuiteStub(_red(_evidence_for(first)), _red(_evidence_for(second)))
    (git, _clear) = _isolate(monkeypatch, script, tmp_path, [first, second], suite)

    assert script.main() == 0

    assert len(git.calls) == 1
    assert git.calls[0][0][-1] == str(first.target)
    assert first.target.read_text() == body


def test_module_guard_calls_main_and_exits_with_its_return_code(script, monkeypatch):
    # `if __name__ == "__main__": sys.exit(main())` is never exercised by
    # loading this module under any other name, including the shared
    # `script` fixture. Re-loading it with __name__ pre-set to "__main__"
    # runs the real file (so it counts, and so no production code changes),
    # but that means the REAL DEFECTS are rebuilt from the REAL REPO_ROOT -
    # there is no chance to monkeypatch anything between exec and main().
    #
    # The only safe way in is main()'s own first gate: it scans every defect
    # for a leftover *.verify-detectors.bak and returns 2 BEFORE touching
    # anything at all. This test creates those backups itself, in a
    # try/finally, and belt-and-suspenders neuters shutil.copy2 and
    # subprocess.run first, so that if the gate ever stopped firing the test
    # would explode instead of rewriting the repo and running the suite.
    assert script.DEFECTS  # the real list is what the re-loaded copy will rebuild
    for path in REAL_TARGETS:
        assert not _backup_of(path).exists(), "a real backup already exists - resolve by hand"

    def refuse_copy(*arguments: object, **keyword_arguments: object) -> None:
        raise AssertionError("real repo source copied - the stale-backup gate did not fire")

    def refuse_run(*arguments: object, **keyword_arguments: object) -> None:
        raise AssertionError("real subprocess invoked - the stale-backup gate did not fire")

    monkeypatch.setattr(shutil, "copy2", refuse_copy)
    monkeypatch.setattr(subprocess, "run", refuse_run)

    try:
        for path in REAL_TARGETS:
            _backup_of(path).write_text("placed by test_module_guard, removed below\n")

        spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)

        with pytest.raises(SystemExit) as excinfo:
            spec.loader.exec_module(module)
    finally:
        for path in REAL_TARGETS:
            _backup_of(path).unlink(missing_ok=True)

    assert excinfo.value.code == 2
