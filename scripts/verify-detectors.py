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
  1. Snapshots each defect's own target file to a backup file.
  2. Applies an ANCHORED patch - matched against a unique, exact source
     string. If the anchor's count in the file is not exactly 1, the patch
     is refused loudly instead of silently matching nothing (matching
     nothing would make a defect look "undetectable" when it was in fact
     never injected - the same false-green shape this whole effort exists
     to eliminate).
  3. Clears every __pycache__ under the repo, both before and after running
     the suite - a same-byte-length edit within the same second can reuse a
     stale .pyc and make a real restore look like a failed one.
  4. Runs `PYTHONPATH=lib:tests <sys.executable> -m pytest tests/ -vv` from
     the repo root. Always sys.executable, never the bare string "python3":
     plain python3 on this machine's PATH is an asdf shim that exits 126 the
     moment HOME is overridden, which is exactly what the suite's own
     dashboard_page fixture does internally.
  5. Restores each target by copying its own backup's bytes back over it -
     never `git checkout --`, which is a destructive operation wearing the
     costume of a restore on a file that is modified but not yet committed.
  6. Verifies the restore two independent ways: a sha256 comparison between
     the pre-patch backup and the restored file, and (belt and suspenders,
     since the file is tracked and was clean before this script ran)
     `git diff --exit-code` against every distinct target file.

"Detected" means the RIGHT assertion fired, not that the suite went red.
Every defect below names the pytest node id that must fail and the harness
assertion label that must appear in the failure output, and both are
required. "Any red counts" was the earlier criterion and it could not tell
"the assertion that exists for this defect caught it" from "something else
broke": a syntax error in the patched file, or only the MINIMUM_ASSERTIONS
ratchet tripping because a section quietly stopped running, would both have
printed DETECTED while proving nothing. A criterion that cannot distinguish
success from failure is not a verification.

Usage:
    PYTHONPATH=lib:tests python3 scripts/verify-detectors.py
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Matches pytest's own "short test summary info" lines ("FAILED <nodeid>",
# and, in case a patch ever breaks collection instead of an assertion,
# "ERROR <nodeid>") - the one place pytest names tests unambiguously,
# regardless of -v output width or truncation.
SUMMARY_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clear_pycache() -> None:
    for cache_directory in REPO_ROOT.rglob("__pycache__"):
        # Skip decisions are relative to REPO_ROOT, not its absolute parts:
        # this project's own convention puts worktrees under
        # .claude/worktrees/<name>/, so REPO_ROOT itself may contain
        # ".claude" while still being the root we are meant to clean. Only
        # a NESTED .claude/ subtree below REPO_ROOT is unrelated scratch.
        if ".claude" in cache_directory.relative_to(REPO_ROOT).parts:
            continue  # leave any nested worktree scratch alone
        shutil.rmtree(cache_directory, ignore_errors=True)


def run_suite() -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, PYTHONPATH="lib:tests")
    # Defensive, not expected to trigger here: a rendering test that
    # SKIPS instead of running would also leave the suite green on an
    # injected defect, which is the same false-green shape as a patch
    # that matched nothing. Force the real check to run.
    environment.pop("METRICS_SKIP_JS_TESTS", None)
    # -vv, not -v: pytest truncates a long assertion message under -v, and
    # the harness's own FAIL lines - the evidence this script now requires
    # to call a defect DETECTED - travel inside exactly such a message.
    # Under -v they are replaced by "...Full output truncated".
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-vv"],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
    )


def failed_test_names(output: str) -> list[str]:
    return sorted(set(SUMMARY_RE.findall(output)))


class Defect:
    def __init__(
        self,
        name: str,
        target: Path,
        description: str,
        anchor: str,
        replacement: str,
        expected_tests: list[str],
        expected_labels: list[str],
    ) -> None:
        self.name = name
        self.target = target
        self.description = description
        self.anchor = anchor
        self.replacement = replacement
        # The pytest node id(s) that must be among the failures, and the
        # harness assertion label(s) that must appear as FAIL lines inside
        # them. Both pinned so a red suite only counts when it is red for
        # this defect's own reason.
        self.expected_tests = expected_tests
        self.expected_labels = expected_labels

    def missing_evidence(self, caught: list[str], output: str) -> list[str]:
        missing = [
            f"expected failing test {test_name}"
            for test_name in self.expected_tests
            if test_name not in caught
        ]
        # The harness's stdout reaches here only through the assertion
        # message of the test that failed, and pytest prefixes those lines
        # ("E   FAIL ..."), so this looks for the label anywhere on a line
        # rather than anchoring it to the start.
        missing += [
            f"expected harness assertion {label!r} to fail"
            for label in self.expected_labels
            if f"FAIL {label}:" not in output
        ]
        return missing

    def patch(self, text: str) -> str:
        count = text.count(self.anchor)
        if count != 1:
            raise RuntimeError(
                f"anchor for defect {self.name!r} matched {count} time(s) in "
                f"{self.target.name} (expected exactly 1) - refusing to patch. "
                "The file has drifted from what this script expects; update "
                "the anchor rather than let this pass silently."
            )
        return text.replace(self.anchor, self.replacement, 1)


DEFECTS = [
    Defect(
        name="the inert row filter",
        target=REPO_ROOT / "dashboard_html_assets.py",
        description=(
            "Reproduces the shipped bug's shape: toggleGroup starts writing "
            "an inline style.display again on expand/collapse. An inline "
            "declaration beats any non-!important class rule regardless of "
            "specificity, so an expanded row's inline 'display: flex' wins "
            "over '.session-row.period-hidden { display: none }' no matter "
            "which period is active - the original defect made 11 sessions "
            "on the chart coexist with 4530 rows in the list under 'ontem'."
        ),
        anchor=("rows.forEach(function(el) { el.classList.toggle('collapsed', !collapsed); });"),
        replacement=(
            "rows.forEach(function(el) { el.classList.toggle('collapsed', "
            "!collapsed); el.style.display = collapsed ? 'flex' : 'none'; });"
        ),
        expected_tests=[
            "tests/test_dashboard_rendering.py::test_every_period_shows_exactly_the_sessions_that_belong",
        ],
        # Rows that do not belong to the period stay on screen, so the very
        # first thing the harness measures per period disagrees.
        expected_labels=["visible session ids"],
    ),
    Defect(
        name="the unscoped totals",
        target=REPO_ROOT / "dashboard_html.py",
        description=(
            "render_composition_legend goes back to being fed the full, "
            "unfiltered `records` instead of the period-scoped window, so "
            "every period's legend reports the same all-time token total - "
            "the original defect reported 29.3B under every period, "
            "'ontem' included, when 'ontem' was actually 5.9B."
        ),
        anchor="mr.compute_token_type_totals(mr.filter_sessions_by_window(records, window))",
        replacement="mr.compute_token_type_totals(records)",
        expected_tests=[
            "tests/test_dashboard_rendering.py::test_every_period_shows_exactly_the_sessions_that_belong",
        ],
        # The legend's own chips are what carries the wrong total; the sum
        # check fails alongside them, but the chips are the direct reading.
        expected_labels=["composition legend chips"],
    ),
]


def restore(target: Path, backup: Path, original_hash: str) -> bool:
    """Copies the backup's bytes back over the target - never `git checkout
    --`. Returns whether the restore verified byte-identical to the
    original."""
    shutil.copy2(backup, target)
    clear_pycache()
    matches_original = sha256(target) == original_hash
    backup.unlink()
    return matches_original


def main() -> int:
    for defect in DEFECTS:
        stale_backup = defect.target.with_suffix(defect.target.suffix + ".verify-detectors.bak")
        if stale_backup.exists():
            print(
                f"stale backup already at {stale_backup} - a previous run did "
                "not clean up. Resolve that by hand before running this script.",
                file=sys.stderr,
            )
            return 2

    results: list[tuple[Defect, str, list[str], str | None]] = []
    overall_ok = True

    for defect in DEFECTS:
        print(f"\n=== {defect.name} ===")
        print(defect.description)

        original_hash = sha256(defect.target)
        backup_path = defect.target.with_suffix(defect.target.suffix + ".verify-detectors.bak")
        shutil.copy2(defect.target, backup_path)
        assert sha256(backup_path) == original_hash  # the backup itself is trustworthy

        try:
            patched_text = defect.patch(defect.target.read_text())
        except RuntimeError as injection_error:
            print(f"INJECTION FAILED: {injection_error}", file=sys.stderr)
            backup_path.unlink()
            results.append((defect, "INJECTION FAILED", [], str(injection_error)))
            overall_ok = False
            continue

        defect.target.write_text(patched_text)
        print(f"patch applied:\n  - {defect.anchor}\n  + {defect.replacement}")

        clear_pycache()
        result = run_suite()
        output = result.stdout + result.stderr
        went_red = result.returncode != 0
        caught = failed_test_names(output)
        missing = defect.missing_evidence(caught, output)

        restore_ok = restore(defect.target, backup_path, original_hash)
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

        detected = went_red and not missing
        status = "DETECTED" if detected else "NOT DETECTED"
        if not detected:
            overall_ok = False
            print("--- full pytest output (defect was NOT caught) ---")
            print(result.stdout)
            print(result.stderr)

        print(f"suite went red: {went_red}")
        print(f"caught by: {caught if caught else '(none)'}")
        print(f"expected failing test(s): {defect.expected_tests}")
        print(f"expected harness assertion(s): {defect.expected_labels}")
        if missing:
            print("MISSING EVIDENCE - the suite went red for some other reason:")
            for item in missing:
                print(f"  - {item}")
        print(f"restore verified byte-identical (sha256): {restore_ok}")
        print(f"=> {status}")

        results.append((defect, status, caught, "; ".join(missing) or None))

    # Final, independent confirmation: every file this script touched is
    # tracked by git and was clean before this script ran, so git itself
    # should agree nothing is left behind - even if every sha256 check
    # above already passed.
    distinct_targets = sorted({defect.target for defect in DEFECTS})
    for target in distinct_targets:
        git_check = subprocess.run(
            ["git", "diff", "--exit-code", "--", str(target)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if git_check.returncode != 0:
            print(
                f"\ngit diff --exit-code disagrees: {target.name} is NOT clean after restore.",
                file=sys.stderr,
            )
            print(git_check.stdout, file=sys.stderr)
            overall_ok = False

    print("\n" + "=" * 70)
    print("SUMMARY")
    for defect, status, caught, error in results:
        print(f"  {defect.name}: {status}")
        for caught_test in caught:
            print(f"    - {caught_test}")
        if error:
            print(f"    error: {error}")

    if not overall_ok:
        print(
            "\nFAILURE: at least one defect went undetected, failed to "
            "inject, or the restore did not verify clean. This is a hole "
            "in the suite's teeth (or in this script), not something to "
            "shrug off - do not lower the bar to make this pass."
        )
        return 1

    print("\nBoth defects DETECTED; all patched files restored byte-identical; git status clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
