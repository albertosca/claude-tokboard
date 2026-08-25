"""Cycling the period must stay a re-window, never become a reload.

WHY A TIMING ASSERTION IS HERE, WHEN TIMING ASSERTIONS ARE USUALLY A BAD IDEA

Every other test in this suite asks whether the dashboard is CORRECT. All of
them passed while pressing "p" froze the terminal for seconds at a time,
because a headless Pilot waits patiently and a human does not: the defect
was invisible to correctness testing by construction. The shipped bug was
action_cycle_period calling load_data on the event loop, so each press
re-read both JSONL files, re-deduped, re-labelled ~4500 sessions and
re-folded subagent tokens just to show a different slice of records it
already had - 2.1-2.7s of frozen UI per press on the real dataset, enough
that the tmux pane had to be killed and reopened. Responsiveness is the
property that broke, so responsiveness is what has to be asserted; nothing
else in this file's absence would have caught it.

WHAT EACH ASSERTION CAN AND CANNOT CATCH - stated because a check that
cannot fail is worse than no check:

  * the reload counter is the sharp one. It is exact, size-independent and
    cannot flake: during the presses, the data layer must not read the
    JSONL files at all. A return to per-keypress reloading fails it on the
    first press whatever the dataset.
  * the absolute ceiling is the human-facing claim. It is the only one that
    speaks in seconds, and the only one that would catch a NEW blocking
    call (a subprocess, a network read) added to the keypress path.

There used to be a third assertion here: a ratio requiring a re-window to
cost at most half of a full reload. It was deleted (2026-08-18) after
measuring a 33% failure rate over six consecutive runs - see git history
for the exact numbers. On the committed eight-record fixture a full reload
costs under a millisecond, so the "reload" and "re-window" samples it
compared were both sub-millisecond noise dominated by scheduler jitter, GC
pauses and OS-level timer resolution, not by the code path under test; no
amount of raising the threshold fixes a comparison where both operands are
noise. The reload counter above already carries this regression, exactly
and without flaking: it does not matter whether a reload is fast or slow,
only whether it happened, and it fails deterministically on the first
press if action_cycle_period ever goes back to load_data.

The absolute ceiling is deliberately loose: the fixture is eight records,
so on it even a full reload costs about a millisecond and no wall-clock
bound could tell a reload from a re-window. That is a limit of running
hermetically, and it is why the reload counter carries the regression and
the ceiling only carries the "no new second-long work" guarantee. Applied
to the real 4545-session dataset the same 1.5s ceiling would have failed on
the shipped defect (2.19-2.82s per press), which is the calibration that
makes the number a bound and not a decoration.
"""

import json
import os
import subprocess

import pytest
from test_fixture_isolation import EXPECTED_MEMBERSHIP

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENV_PYTHON = os.path.join(REPO_ROOT, ".venv", "bin", "python3")
DRIVER = os.path.join(os.path.dirname(__file__), "tui_latency_driver.py")

TUI_SKIP_ENV = "METRICS_SKIP_TUI_TESTS"

_INSTALL_HINT = (
    "The TUI tests need the dashboard's own virtualenv (textual is not, and "
    "should not be, installed on the interpreter running pytest).\n"
    "  Create it:  scripts/setup-tui-venv.sh\n"
    "  Skip explicitly (this is not free - nothing checks that pressing 'p' "
    f"stays responsive while it is set):  {TUI_SKIP_ENV}=1 pytest\n"
)

# A press must finish well inside this. Twelve times the fixture's measured
# round-trip and roughly half the shipped defect's real-data latency, so it
# is loose enough to survive a busy machine and still name the failure.
MAX_PRESS_SECONDS = 1.5


@pytest.fixture(scope="session")
def cycle_timings(dashboard_page):
    """Runs the headless driver against the same throwaway HOME the rest of
    the suite uses, so this measures the committed fixture and never the
    real ~/.claude/metrics."""
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
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"the TUI driver failed ({result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_the_period_actually_cycles(cycle_timings):
    """Guards the timings above from the cheapest way to pass them: an
    action that does nothing at all is instantaneous AND reads no files."""
    assert cycle_timings["periods"] == ["ontem", "desde-segunda", "15-dias", "mes", "tudo"], (
        f"pressing 'p' produced {cycle_timings['periods']} - the cycle order changed, "
        "or a press did not land"
    )
    seen = cycle_timings["records_per_period"]
    # Only the windows that are anchored to today, never to the calendar
    # week: "desde-segunda" spans one day on a Monday and seven on a Sunday.
    for period, ids in EXPECTED_MEMBERSHIP.items():
        assert seen[period] == len(ids), (
            f"{period} showed {seen[period]} records, not {len(ids)} - the re-windowed view "
            "no longer matches the window"
        )


def test_cycling_the_period_never_re_reads_the_data(cycle_timings):
    """The regression itself: the period changes what is DISPLAYED, and the
    raw data does not change between periods, so a press has no business
    going back to disk."""
    assert cycle_timings["load_records_calls_during_presses"] == 0, (
        f"pressing 'p' re-read the metrics files "
        f"{cycle_timings['load_records_calls_during_presses']} time(s). The period-independent "
        "work belongs in load_base_data, once, at startup - see apply_period"
    )


def test_every_press_stays_responsive(cycle_timings):
    slowest = max(cycle_timings["presses"])
    assert slowest < MAX_PRESS_SECONDS, (
        f"the slowest 'p' press took {slowest:.2f}s (ceiling {MAX_PRESS_SECONDS}s). "
        f"All presses: {[round(p, 3) for p in cycle_timings['presses']]}"
    )


def test_the_driver_measured_the_fixture_and_not_the_real_data(cycle_timings):
    """The timings are only meaningful if they came from the eight-record
    fixture; against the real dataset this file would measure something
    else entirely and its numbers would drift daily."""
    assert cycle_timings["records_per_period"]["tudo"] == len(EXPECTED_MEMBERSHIP["tudo"]), (
        f"the driver saw {cycle_timings['records_per_period']['tudo']} sessions under 'tudo' - "
        "that is not the fixture, so HOME was not isolated"
    )
