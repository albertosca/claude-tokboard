"""Generates one isolated dashboard for the whole test session, and isolates
EVERY test in the suite from the author's real ~/.claude/metrics by default.

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
import metrics_core as mr
import metrics_core.backfill as backfill_module
import metrics_core.labels as labels_module
import metrics_core.paths as paths_module
from fixtures.build_fixture import build_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolate_from_real_metrics_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Every test starts isolated from ~/.claude/metrics - the author's
    real, irreplaceable session/label-cache data, which other live Claude
    sessions append to continuously - by default.

    Added 2026-09-23 (Task 5, Fix round 1) after two tests in
    tests/test_tui_unbound.py called dashboard_tui.load_data() with no
    isolation at all, migrating the real task_labels.json/agent_labels.json
    to the new per-language shape and creating their one-time .pre-i18n
    backups - see tests/test_real_metrics_isolation.py for the guard this
    fixture exists to satisfy, and docs/superpowers for the incident.

    METRICS_FILE, SUBAGENTS_FILE, TASK_LABELS_FILE, AGENT_LABELS_FILE,
    BACKFILL_STATE_FILE and CONFIG_FILE are module-level constants resolved
    from HOME at IMPORT time (each module's own docstring says so) - a bare
    monkeypatch.setenv("HOME", ...) does nothing to them, since metrics_core
    was already imported by the time any test runs. They are patched
    directly here instead, mirroring tests/test_tui_state.py's
    isolate_tui_from_the_real_filesystem (which this fixture does not
    replace - a test that calls it, or seeded_tui_home, still does so
    inside its own body, AFTER this fixture has already run, so its own
    throwaway HOME simply overrides the default set here).

    This fixture does not build fixture data, seed a config, or pin the
    locale - it only guarantees no test can reach the real directory by
    omission. A test that needs real session data, a real config, or a
    pinned language still calls isolate_tui_from_the_real_filesystem /
    seeded_tui_home / builds its own tmp_path, exactly as before.
    """
    metrics = tmp_path_factory.mktemp("default-metrics-isolation") / ".claude" / "metrics"
    metrics.mkdir(parents=True)
    monkeypatch.setattr(mr, "METRICS_FILE", str(metrics / "sessions.jsonl"))
    monkeypatch.setattr(mr, "SUBAGENTS_FILE", str(metrics / "subagents.jsonl"))
    monkeypatch.setattr(labels_module, "TASK_LABELS_FILE", str(metrics / "task_labels.json"))
    monkeypatch.setattr(labels_module, "AGENT_LABELS_FILE", str(metrics / "agent_labels.json"))
    monkeypatch.setattr(
        backfill_module, "BACKFILL_STATE_FILE", str(metrics / "backfill_state.json")
    )
    monkeypatch.setattr(paths_module, "CONFIG_FILE", str(metrics / "config.json"))


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
