"""Tests for scripts/setup-tui-venv.sh's Python version gate.

dashboard_tui.py needs Python 3.10+ (it evaluates `str | tuple[...]` type
unions at import time and uses `zip(strict=)`), but a stock macOS still
ships python3 3.9.6, and textual>=8 itself accepts 3.9 - so nothing
upstream refuses on our behalf. These tests never touch the real plugin
.venv: each one runs the script against a throwaway copy of the plugin
tree (script + requirements-tui.txt) under tmp_path, with a fake `python3`
placed first on PATH.

The fake `python3` only implements `--version` (real python3's output,
"Python X.Y.Z", which the script parses) - it deliberately does not
implement `-m venv`, so a run that gets PAST the version gate fails at
venv creation instead with a distinct error. That is how
test_new_enough_python_proceeds_past_the_version_gate tells "proceeded"
apart from "the version check was accidentally skipped and it happened to
work" without actually building a venv in a test.
"""

import stat
import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "setup-tui-venv.sh"
REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements-tui.txt"

FAKE_PYTHON3_TEMPLATE = """#!/usr/bin/env bash
if [ "$1" = "--version" ]; then
  echo "Python {version}"
  exit 0
fi
echo "fake python3 does not support: $*" >&2
exit 1
"""


def _write_fake_python3(bin_dir: Path, version: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    fake = bin_dir / "python3"
    fake.write_text(FAKE_PYTHON3_TEMPLATE.format(version=version))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_plugin_tree(tmp_path: Path) -> Path:
    """A throwaway copy of just enough of the plugin layout for the script
    to resolve PLUGIN_ROOT/VENV_DIR under tmp_path, never the real repo -
    scripts/setup-tui-venv.sh derives PLUGIN_ROOT from its own path."""
    plugin_root = tmp_path / "plugin"
    scripts_dir = plugin_root / "scripts"
    scripts_dir.mkdir(parents=True)
    script_copy = scripts_dir / "setup-tui-venv.sh"
    script_copy.write_text(SCRIPT_PATH.read_text())
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IEXEC)
    (plugin_root / "requirements-tui.txt").write_text(REQUIREMENTS_PATH.read_text())
    return plugin_root


def _safe_bin(tmp_path_factory_dir: Path) -> Path:
    """A directory with just `dirname` (the one external, non-/bin
    coreutil the script's own PLUGIN_ROOT resolution shells out to)
    symlinked in from the real /usr/bin - never python3 - so PATH can
    stay otherwise minimal without the script dying on `dirname: command
    not found` before it even gets to the version check."""
    safe_bin = tmp_path_factory_dir / "safe-bin"
    if not safe_bin.exists():
        safe_bin.mkdir()
        (safe_bin / "dirname").symlink_to("/usr/bin/dirname")
    return safe_bin


def _run_script(plugin_root: Path, fake_bin_dir: Path) -> subprocess.CompletedProcess[str]:
    # PATH is fake_bin_dir, a safe-bin with only `dirname`, then /bin -
    # never the real PATH. This has every command the script shells out
    # to (rm, cat, dirname) but no python3 of its own on macOS, so
    # fake_bin_dir is the sole source of a `python3` unless a test leaves
    # it empty. That is what lets
    # test_missing_python3_on_path_still_refuses_clearly and the old-venv
    # test prove "no usable python3 was found" instead of silently
    # falling through to the real interpreter that ran pytest.
    safe_bin = _safe_bin(fake_bin_dir.parent)
    env = {"PATH": f"{fake_bin_dir}:{safe_bin}:/bin"}
    return subprocess.run(
        ["/bin/bash", str(plugin_root / "scripts" / "setup-tui-venv.sh")],
        capture_output=True,
        text=True,
        env=env,
    )


def test_old_python_on_path_refuses_with_a_clear_message(tmp_path: Path) -> None:
    plugin_root = _make_plugin_tree(tmp_path)
    fake_bin = tmp_path / "fakebin-old"
    _write_fake_python3(fake_bin, "3.9.6")

    result = _run_script(plugin_root, fake_bin)

    assert result.returncode == 1
    assert "3.9.6" in result.stderr
    assert "3.10" in result.stderr
    assert not (plugin_root / ".venv").exists()


def test_new_enough_python_proceeds_past_the_version_gate(tmp_path: Path) -> None:
    plugin_root = _make_plugin_tree(tmp_path)
    fake_bin = tmp_path / "fakebin-new"
    _write_fake_python3(fake_bin, "3.10.1")

    result = _run_script(plugin_root, fake_bin)

    # The fake python3 cannot actually build a venv (it only understands
    # --version), so a run that got past the gate fails here instead - a
    # different, later error than the version refusal above.
    assert result.returncode == 1
    assert "needs Python" not in result.stderr  # the version gate did not fire
    assert "failed to create the virtual environment" in result.stderr


def test_an_existing_venv_built_with_an_old_python_is_not_treated_as_ready(
    tmp_path: Path,
) -> None:
    plugin_root = _make_plugin_tree(tmp_path)
    old_venv_bin = plugin_root / ".venv" / "bin"
    _write_fake_python3(old_venv_bin, "3.9.6")

    # No python3 at all on PATH once past the existing-venv check, so the
    # script has no way to silently succeed by falling through to a real
    # rebuild - if it treated the stale venv as ready, this would exit 0.
    empty_bin = tmp_path / "empty-path"
    empty_bin.mkdir()

    result = _run_script(plugin_root, empty_bin)

    assert result.returncode == 1
    assert "3.9.6" in result.stderr
    assert not (plugin_root / ".venv").exists(), (
        "a venv built with an old Python must be removed, never left in place looking ready"
    )


def test_missing_python3_on_path_still_refuses_clearly(tmp_path: Path) -> None:
    plugin_root = _make_plugin_tree(tmp_path)
    empty_bin = tmp_path / "empty-path"
    empty_bin.mkdir()

    result = _run_script(plugin_root, empty_bin)

    assert result.returncode == 1
    assert "python3 not found on PATH" in result.stderr
