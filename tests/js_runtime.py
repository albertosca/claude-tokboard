"""Locating the Node runtime for the rendering tests.

A missing runtime FAILS rather than skips. That is deliberate: this suite
exists because a check that cannot fail was mistaken for a passing one, and
a quietly-skipped rendering test is the same mistake wearing a different
hat. Opting out is possible but has to be said out loud, by setting
METRICS_SKIP_JS_TESTS=1.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

NODE_SKIP_ENV = "METRICS_SKIP_JS_TESTS"

_INSTALL_HINT = (
    "The rendering tests need Node and jsdom.\n"
    "  Install:  npm install\n"
    "  Skip explicitly (this is not free - nothing checks the rendering "
    f"while it is set):  {NODE_SKIP_ENV}=1 pytest\n"
)


def require_node_with_jsdom() -> str:
    if os.environ.get(NODE_SKIP_ENV):
        pytest.skip(f"{NODE_SKIP_ENV} is set - rendering is NOT being checked")
    node = shutil.which("node")
    if not node:
        pytest.fail(f"node not found on PATH.\n{_INSTALL_HINT}")
    repo_root = Path(__file__).resolve().parent.parent
    probe = subprocess.run(
        [node, "-e", "require.resolve('jsdom')"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.fail(f"node is present but jsdom is not installed.\n{_INSTALL_HINT}")
    return node
