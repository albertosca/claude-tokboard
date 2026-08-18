"""Cycling the period must not disturb UI state it did not change.

Regression coverage for two defects found by hand (2026-08-18), both side
effects of the perf fix in test_tui_responsiveness.py: action_cycle_period
now ends in self.refresh(recompose=True), which rebuilds the whole widget
tree and, unless something restores it, drops state the rebuild has no
business dropping.

  Defect 1 - Footer goes blank after "p", Tab brings it back.
  Defect 2 - pressing "p" while on the "Panorama" tab bounced back to
             "Sessões".

Defect 2 did NOT reproduce from the app's default tab (Sessões) - a fresh,
un-recomposed TabbedContent already defaults to its first pane, so that
starting point could not tell "state preserved" from "state defaulted".
It DID reproduce reliably starting from Panorama (toggle with "g", then
press "p"), which is also the sequence the user actually hit. Both starting
points are driven below so a regression in either branch of the fix (the
Sessões case restores Tree focus, the Panorama case restores TrendChart
focus) is caught.
"""
import json
import os
import subprocess

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
DRIVER = os.path.join(os.path.dirname(__file__), "tui_state_driver.py")

TUI_SKIP_ENV = "METRICS_SKIP_TUI_TESTS"

_INSTALL_HINT = (
    "The TUI tests need the dashboard's own virtualenv (textual is not, and "
    "should not be, installed on the interpreter running pytest).\n"
    "  Create it:  scripts/setup-tui-venv.sh\n"
    "  Skip explicitly (this is not free - nothing checks that \"p\" leaves the "
    f"footer and the active tab alone):  {TUI_SKIP_ENV}=1 pytest\n"
)


@pytest.fixture(scope="session")
def state_after_p(dashboard_page):
    """Runs the headless driver against the same throwaway HOME the rest of
    the suite uses, so this measures the committed fixture and never the
    user's real data."""
    if os.environ.get(TUI_SKIP_ENV):
        pytest.skip(f"{TUI_SKIP_ENV} is set - the TUI is NOT being checked")
    if not os.path.exists(VENV_PYTHON):
        pytest.fail(f"no TUI virtualenv at {VENV_PYTHON}.\n{_INSTALL_HINT}")

    env = dict(os.environ, HOME=dashboard_page.home)
    # Inherited PYTHONPATH would put the pytest interpreter's lib directory
    # ahead of the venv's own; the driver adds what it needs to sys.path.
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [VENV_PYTHON, DRIVER],
        env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, (
        f"the TUI state driver failed ({result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("case", ["sessions_case", "panorama_case"])
def test_active_tab_survives_a_period_cycle(state_after_p, case):
    result = state_after_p[case]
    assert result["active"] == result["active_before"], (
        f"[{case}] pressing 'p' changed the active tab from "
        f"{result['active_before']!r} to {result['active']!r} - a period "
        "cycle must not touch which tab is open"
    )


@pytest.mark.parametrize("case", ["sessions_case", "panorama_case"])
def test_focus_is_not_dropped_after_a_period_cycle(state_after_p, case):
    result = state_after_p[case]
    assert result["focused_type"] is not None, (
        f"[{case}] app.focused is None after 'p' - the Footer has nothing "
        "focused to show bindings for, so it renders blank until the user "
        "presses Tab by hand"
    )


@pytest.mark.parametrize("case", ["sessions_case", "panorama_case"])
def test_focused_widget_lives_in_the_active_pane(state_after_p, case):
    result = state_after_p[case]
    assert result["focused_inside_active_pane"], (
        f"[{case}] focus landed on a {result['focused_type']} that is not a "
        f"descendant of the active pane ({result['active']!r}) - it must be "
        "focus that actually belongs to what the user is looking at"
    )


@pytest.mark.parametrize("case", ["sessions_case", "panorama_case"])
def test_footer_is_not_blank_after_a_period_cycle(state_after_p, case):
    result = state_after_p[case]
    assert result["footer_keys"] > 0, (
        f"[{case}] the Footer rendered 0 FooterKey children after 'p' - "
        "this is the exact symptom reported by hand: shortcuts vanish "
        "until Tab is pressed"
    )


def test_sessions_case_restores_the_tree(state_after_p):
    assert state_after_p["sessions_case"]["focused_type"] == "Tree"


def test_panorama_case_restores_the_trend_chart(state_after_p):
    assert state_after_p["panorama_case"]["focused_type"] == "TrendChart"
