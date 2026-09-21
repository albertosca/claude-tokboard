"""The shape of a recorded project path.

Split out of projects.py (2026-09-21) so records.py can use it too:
projects.py already imports from records.py, so anything both need has to
sit below them both.
"""

from __future__ import annotations

import json
from pathlib import Path

# Where a worktree can live, most specific first. Claude Code's own
# worktree command uses the first; `git worktree add` has no opinion, and a
# plain `.worktrees/` at the repo root is the common hand-rolled choice
# (added 2026-09-21, after worktrees under it turned up in the legend under
# their own bare names, reading like subagents rather than branches of a
# project). Each marker carries its surrounding slashes, which is what
# keeps it a path SEGMENT: a directory merely NAMED "my.worktrees" does not
# match.
WORKTREE_MARKERS = ("/.claude/worktrees/", "/.worktrees/")


def worktree_marker_of(normalized_path: str) -> str | None:
    """Which worktree marker this path uses, or None if it uses none."""
    for candidate in WORKTREE_MARKERS:
        if candidate in normalized_path:
            return candidate
    return None


def split_worktree(normalized_path: str) -> tuple[str, str] | None:
    """(parent project path, worktree name) if this path is inside a
    worktree that has a name, else None."""
    marker = worktree_marker_of(normalized_path)
    if marker is None:
        return None
    parent_path, _, remainder = normalized_path.partition(marker)
    worktree_name = remainder.split("/")[0]
    if not worktree_name:
        return None
    return parent_path, worktree_name


CONFIG_FILE = str(Path("~/.claude/metrics/config.json").expanduser())


def load_project_roots(path: str | None = None) -> list[str]:
    """The directories the reader has declared their projects live in.

    A recorded `project_path` is whatever Claude Code's cwd happened to be,
    which is often a subdirectory of the real project - and nothing in the
    path itself says where the project stops. Measured on one real install
    (2026-09-21): 104 of 163 distinct recorded paths no longer exist on
    disk at all, and the busiest project had been renamed, so resolving
    against the filesystem cannot answer it either. So it is declared, in
    ~/.claude/metrics/config.json:

        {"project_roots": ["/Users/username/code"]}

    Missing file, unreadable file, bad JSON and a non-list value all mean
    the same thing - no roots declared - because this is a convenience for
    naming, and a dashboard that refuses to render over a typo'd config
    would be worse than one that names a few directories awkwardly.
    """
    try:
        with Path(path or CONFIG_FILE).open() as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(config, dict):
        return []
    roots = config.get("project_roots")
    if not isinstance(roots, list):
        return []
    return [str(Path(root).expanduser()) for root in roots if isinstance(root, str) and root]
