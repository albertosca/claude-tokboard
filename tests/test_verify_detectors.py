"""Tests for scripts/verify-detectors.py's clear_pycache() worktree fix.

clear_pycache() walks REPO_ROOT for stale __pycache__ directories and skips
any that sit under a nested .claude/ subtree, so cleaning a worktree does
not reach into scratch left by ANOTHER worktree nested inside it. The
original implementation computed that skip from the cache directory's
absolute path parts, which also matched whenever REPO_ROOT ITSELF lived
under a .claude path - this project's own convention for working branches
(.claude/worktrees/<name>/) - silently turning the function into a no-op
every time it ran from inside one.
"""

import importlib.util
import shutil
import types
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "verify-detectors.py"


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


def _make_pycache(directory: Path) -> Path:
    cache_dir = directory / "__pycache__"
    cache_dir.mkdir(parents=True)
    (cache_dir / "mod.cpython-314.pyc").write_bytes(b"")
    return cache_dir


def test_clears_a_cache_directly_under_a_worktree_root(tmp_path, script, monkeypatch):
    # REPO_ROOT itself lives under a .claude path, matching this project's
    # own worktree convention (.claude/worktrees/<name>/) - a cache
    # directly under it must still be removed.
    root = tmp_path / ".claude" / "worktrees" / "coverage-phase3d"
    root.mkdir(parents=True)
    cache_dir = _make_pycache(root / "lib")
    monkeypatch.setattr(script, "REPO_ROOT", root)

    script.clear_pycache()

    assert not cache_dir.exists()


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
    cache_dir = _make_pycache(root / "lib")
    monkeypatch.setattr(script, "REPO_ROOT", root)

    script.clear_pycache()

    assert not cache_dir.exists()


def test_tolerates_a_cache_directory_vanishing_before_rmtree_runs(tmp_path, script, monkeypatch):
    # A cache directory can disappear between rglob() yielding it and
    # shutil.rmtree() acting on it (e.g. a concurrent cleanup process).
    # clear_pycache() already passes ignore_errors=True to cover exactly
    # this; this pins that it actually works, rather than adding new code
    # to handle it.
    root = tmp_path / "main-checkout"
    root.mkdir()
    cache_dir = _make_pycache(root / "lib")
    monkeypatch.setattr(script, "REPO_ROOT", root)
    real_rmtree = shutil.rmtree

    def rmtree_after_it_vanished(path: Path, ignore_errors: bool = False) -> None:
        real_rmtree(path, ignore_errors=True)  # simulate the concurrent removal
        real_rmtree(path, ignore_errors=ignore_errors)  # what clear_pycache() actually asked for

    monkeypatch.setattr(script.shutil, "rmtree", rmtree_after_it_vanished)

    script.clear_pycache()  # must not raise

    assert not cache_dir.exists()
