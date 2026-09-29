"""Run a command a backfill script printed the way an operator pastes it.

Every command those scripts print is meant to be pasted into a shell at
the repository root, so the tests that check one run it exactly that way:
as its own process, from the repository root, rather than by calling
main() with an argv they assembled themselves.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# The program as every printed command names it (see
# metrics_core.backfill.script_invocation).
PASTED_PROGRAM = "python3"


def run_as_pasted(words: list[str], base: Path) -> subprocess.CompletedProcess[str]:
    """Run `words`, a printed command already split by shlex, from the
    repository root. `python3` becomes this interpreter (a version-manager
    shim on PATH can resolve through HOME), and the environment is only a
    PATH and a HOME under `base` - never the real one, so nothing the
    command reads by default can be the real ~/.claude."""
    assert words[0] == PASTED_PROGRAM, words
    home = base / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, *words[1:]],
        cwd=REPOSITORY_ROOT,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )
