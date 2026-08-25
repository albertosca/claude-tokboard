"""Generates one isolated dashboard for the whole test session.

HOME is overridden so every expanduser() inside the dashboard - the metrics
files, the label caches, get_open_projects, and the output path itself -
resolves inside a throwaway directory.
"""

import os
import subprocess
import sys
import tempfile
from datetime import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from fixtures.build_fixture import build_fixture

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class DashboardPage:
    def __init__(self, home, html_path, expected):
        self.home = home
        self.html_path = html_path
        self.expected = expected


@pytest.fixture(scope="session")
def dashboard_page():
    with tempfile.TemporaryDirectory() as home:
        now = datetime.now().astimezone()
        built = build_fixture(home, now)

        env = dict(os.environ, HOME=home, PYTHONPATH=os.path.join(REPO_ROOT, "lib"))
        # sys.executable, NOT "python3": python3 on PATH is an asdf shim
        # that resolves through $HOME/.asdf and exits 126 the moment HOME
        # is overridden. This cost real debugging time - do not "simplify"
        # it back to a bare python3.
        result = subprocess.run(
            [
                sys.executable,
                os.path.join(REPO_ROOT, "dashboard_html.py"),
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
        html_path = os.path.join(home, ".claude", "metrics", "dashboard.html")
        assert os.path.exists(html_path), f"no dashboard at {html_path}; stdout: {result.stdout}"
        yield DashboardPage(home, html_path, built["expected"])
