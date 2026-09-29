"""Run a command a backfill script printed the way an operator pastes it.

Every command those scripts print is meant to be pasted into a shell in
the directory the printing run was started from - the repository root or
anywhere else - so the tests that check one run it exactly that way: as
its own process, from that directory, rather than by calling main() with
an argv they assembled themselves.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# The program as every printed command names it (see
# metrics_core.backfill.script_invocation).
PASTED_PROGRAM = "python3"

# Where a test starts the printing run and pastes its command: the root,
# and a directory elsewhere (created under tmp_path, with a space in it).
WORKING_DIRECTORIES = ["repository root", "elsewhere"]


def enter_working_directory(where: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """chdir to the directory `where` names, for the rest of the test."""
    directory = REPOSITORY_ROOT
    if where == "elsewhere":
        directory = tmp_path / "some other dir"
        directory.mkdir(exist_ok=True)
    monkeypatch.chdir(directory)
    return directory


def names_script(word: str, script: Path, directory: Path) -> bool:
    """Whether `word`, the printed program path, is relative and names
    `script` when read from `directory`."""
    return not Path(word).is_absolute() and (directory / word).resolve() == script.resolve()


def run_as_pasted(
    words: list[str], base: Path, directory: Path = REPOSITORY_ROOT
) -> subprocess.CompletedProcess[str]:
    """Run `words`, a printed command already split by shlex, from
    `directory`. `python3` becomes this interpreter (a version-manager
    shim on PATH can resolve through HOME), and the environment is only a
    PATH and a HOME under `base` - never the real one, so nothing the
    command reads by default can be the real ~/.claude."""
    assert words[0] == PASTED_PROGRAM, words
    home = base / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, *words[1:]],
        cwd=directory,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )
