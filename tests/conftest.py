"""Generates one isolated dashboard for the whole test session.

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
from fixtures.build_fixture import build_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent


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
