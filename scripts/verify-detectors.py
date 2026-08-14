#!/usr/bin/env python3
"""Proves the presentation-layer suite still has teeth.

This is the spec's success criterion made executable: reverting either of
the two Criticals below must turn `tests/` red. Both defects shipped once,
past five reviews, because every check that existed asserted the markup
CONTAINED an attribute rather than asking what the page actually rendered.
Task 3 built the suite that finally asks the second question; this script
re-injects both original defects, one at a time, and confirms the suite
still catches them - so that proof stays versioned and re-runnable instead
of living only in a transcript that eventually rots.

If a defect comes back NOT DETECTED, that is a real regression in the
suite's coverage, not an expected or acceptable outcome. This script exits
non-zero in that case and never softens the report.

For each defect this script:
  1. Snapshots dashboard_html.py to a backup file.
  2. Applies an ANCHORED patch - matched against a unique, exact source
     string. If the anchor's count in the file is not exactly 1, the patch
     is refused loudly instead of silently matching nothing (matching
     nothing would make a defect look "undetectable" when it was in fact
     never injected - the same false-green shape this whole effort exists
     to eliminate).
  3. Clears every __pycache__ under the repo, both before and after running
     the suite - a same-byte-length edit within the same second can reuse a
     stale .pyc and make a real restore look like a failed one.
  4. Runs `PYTHONPATH=lib:tests <sys.executable> -m pytest tests/ -v` from
     the repo root. Always sys.executable, never the bare string "python3":
     plain python3 on this machine's PATH is an asdf shim that exits 126 the
     moment HOME is overridden, which is exactly what the suite's own
     dashboard_page fixture does internally.
  5. Restores dashboard_html.py by copying the backup's bytes back over it -
     never `git checkout --`, which is a destructive operation wearing the
     costume of a restore on a file that is modified but not yet committed.
  6. Verifies the restore two independent ways: a sha256 comparison between
     the pre-patch backup and the restored file, and (belt and suspenders,
     since the file is tracked and was clean before this script ran)
     `git diff --exit-code -- dashboard_html.py`.

Usage:
    PYTHONPATH=lib:tests python3 scripts/verify-detectors.py
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "dashboard_html.py"
BACKUP = REPO_ROOT / "dashboard_html.py.verify-detectors.bak"

# Matches pytest's own "short test summary info" lines ("FAILED <nodeid>",
# and, in case a patch ever breaks collection instead of an assertion,
# "ERROR <nodeid>") - the one place pytest names tests unambiguously,
# regardless of -v output width or truncation.
SUMMARY_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clear_pycache() -> None:
    for cache_dir in REPO_ROOT.rglob("__pycache__"):
        if ".claude" in cache_dir.parts:
            continue  # leave any unrelated worktree scratch alone
        shutil.rmtree(cache_dir, ignore_errors=True)


def run_suite() -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH="lib:tests")
    # Defensive, not expected to trigger here: a rendering test that
    # SKIPS instead of running would also leave the suite green on an
    # injected defect, which is the same false-green shape as a patch
    # that matched nothing. Force the real check to run.
    env.pop("METRICS_SKIP_JS_TESTS", None)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )


def failed_test_names(output: str):
    return sorted(set(SUMMARY_RE.findall(output)))


class Defect:
    def __init__(self, name, description, anchor, replacement):
        self.name = name
        self.description = description
        self.anchor = anchor
        self.replacement = replacement

    def patch(self, text: str) -> str:
        count = text.count(self.anchor)
        if count != 1:
            raise RuntimeError(
                f"anchor for defect {self.name!r} matched {count} time(s) in "
                "dashboard_html.py (expected exactly 1) - refusing to patch. "
                "The file has drifted from what this script expects; update "
                "the anchor rather than let this pass silently."
            )
        return text.replace(self.anchor, self.replacement, 1)


DEFECTS = [
    Defect(
        name="the inert row filter",
        description=(
            "Reproduces the shipped bug's shape: toggleGroup starts writing "
            "an inline style.display again on expand/collapse. An inline "
            "declaration beats any non-!important class rule regardless of "
            "specificity, so an expanded row's inline 'display: flex' wins "
            "over '.session-row.period-hidden { display: none }' no matter "
            "which period is active - the original defect made 11 sessions "
            "on the chart coexist with 4530 rows in the list under 'ontem'."
        ),
        anchor=(
            "rows.forEach(function(el) { el.classList.toggle('collapsed', "
            "!collapsed); });"
        ),
        replacement=(
            "rows.forEach(function(el) { el.classList.toggle('collapsed', "
            "!collapsed); el.style.display = collapsed ? 'flex' : 'none'; });"
        ),
    ),
    Defect(
        name="the unscoped totals",
        description=(
            "render_composition_legend goes back to being fed the full, "
            "unfiltered `records` instead of the period-scoped window, so "
            "every period's legend reports the same all-time token total - "
            "the original defect reported 29.3B under every period, "
            "'ontem' included, when 'ontem' was actually 5.9B."
        ),
        anchor="mr.compute_token_type_totals(mr.filter_sessions_by_window(records, window))",
        replacement="mr.compute_token_type_totals(records)",
    ),
]


def restore(original_hash: str) -> bool:
    """Copies the backup's bytes back over the target - never `git checkout
    --`. Returns whether the restore verified byte-identical to the
    original."""
    shutil.copy2(BACKUP, TARGET)
    clear_pycache()
    ok = sha256(TARGET) == original_hash
    BACKUP.unlink()
    return ok


def main() -> int:
    if BACKUP.exists():
        print(
            f"stale backup already at {BACKUP} - a previous run did not "
            "clean up. Resolve that by hand before running this script.",
            file=sys.stderr,
        )
        return 2

    original_hash = sha256(TARGET)
    results = []
    overall_ok = True

    for defect in DEFECTS:
        print(f"\n=== {defect.name} ===")
        print(defect.description)

        shutil.copy2(TARGET, BACKUP)
        assert sha256(BACKUP) == original_hash  # the backup itself is trustworthy

        try:
            patched_text = defect.patch(TARGET.read_text())
        except RuntimeError as exc:
            print(f"INJECTION FAILED: {exc}", file=sys.stderr)
            BACKUP.unlink()
            results.append((defect, "INJECTION FAILED", [], str(exc)))
            overall_ok = False
            continue

        TARGET.write_text(patched_text)
        print(f"patch applied:\n  - {defect.anchor}\n  + {defect.replacement}")

        clear_pycache()
        result = run_suite()
        went_red = result.returncode != 0
        caught = failed_test_names(result.stdout + result.stderr)

        restore_ok = restore(original_hash)
        if not restore_ok:
            print(
                "RESTORE FAILED: the restored file is not byte-identical to "
                "the original - stopping immediately rather than testing the "
                "next defect against a corrupted baseline.",
                file=sys.stderr,
            )
            results.append((defect, "RESTORE FAILED", caught, None))
            overall_ok = False
            break

        detected = went_red and bool(caught)
        status = "DETECTED" if detected else "NOT DETECTED"
        if not detected:
            overall_ok = False
            print("--- full pytest output (defect was NOT caught) ---")
            print(result.stdout)
            print(result.stderr)

        print(f"suite went red: {went_red}")
        print(f"caught by: {caught if caught else '(none)'}")
        print(f"restore verified byte-identical (sha256): {restore_ok}")
        print(f"=> {status}")

        results.append((defect, status, caught, None))

    # Final, independent confirmation: the file is tracked by git and was
    # clean before this script ran, so git itself should agree nothing is
    # left behind - even if every sha256 check above already passed.
    git_check = subprocess.run(
        ["git", "diff", "--exit-code", "--", str(TARGET)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if git_check.returncode != 0:
        print(
            "\ngit diff --exit-code disagrees: dashboard_html.py is NOT "
            "clean after restore.",
            file=sys.stderr,
        )
        print(git_check.stdout, file=sys.stderr)
        overall_ok = False

    print("\n" + "=" * 70)
    print("SUMMARY")
    for defect, status, caught, err in results:
        print(f"  {defect.name}: {status}")
        for t in caught:
            print(f"    - {t}")
        if err:
            print(f"    error: {err}")

    if not overall_ok:
        print(
            "\nFAILURE: at least one defect went undetected, failed to "
            "inject, or the restore did not verify clean. This is a hole "
            "in the suite's teeth (or in this script), not something to "
            "shrug off - do not lower the bar to make this pass."
        )
        return 1

    print(
        "\nBoth defects DETECTED; dashboard_html.py restored byte-identical; "
        "git status clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
