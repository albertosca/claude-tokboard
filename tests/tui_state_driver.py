"""Drives dashboard_tui.py headlessly and reports UI state after "p".

Not a test module (no test_ prefix, so pytest does not collect it): the
payload test_tui_state.py runs as a SUBPROCESS, for the same reason
tui_latency_driver.py is one - pytest lives on the system interpreter,
textual lives only in the repo's .venv, and neither can import the other.

Regression covered: pressing "p" used to (1) blank the Footer until Tab was
pressed and (2) forget which TabPane was active, always reopening on
"Sessões" even when the user was on "Panorama" (both found by hand,
2026-08-18 - see action_cycle_period's comments in dashboard_tui.py for the
root causes). Two cases are driven because the fix has a branch per tab:

  sessions_case   presses "p" from the app's default tab (Sessões) and
                   checks the Tree stays the focused, in-pane widget.
  panorama_case   toggles to Panorama with "g" first, THEN presses "p" -
                   this is the sequence the user actually hit, and the one
                   defect 2 depended on (it did not reproduce starting from
                   Sessões, since that is also where a fresh, un-recomposed
                   TabbedContent already defaults to).

Each case reports:
  active_before / active_after   TabbedContent.active id, before and after "p"
  focused_type_after             type name of app.focused, or None
  focused_inside_active_pane     is app.focused a descendant of the TabPane
                                  named by active_after
  footer_keys_after               FooterKey children the Footer actually
                                  rendered after "p" - the direct, visible
                                  proof the footer did not stay blank

Prints one JSON object on stdout. Never launch the app interactively.
"""
import asyncio
import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, REPO)

import metrics_core as mr  # noqa: E402
import dashboard_tui as tui  # noqa: E402
from textual.widgets import Footer, TabbedContent  # noqa: E402


def _state(app):
    active = app.query_one(TabbedContent).active
    pane = app.query_one(f"#{active}")
    focused = app.focused
    footer = app.query_one(Footer)
    return {
        "active": active,
        "focused_type": type(focused).__name__ if focused is not None else None,
        "focused_inside_active_pane": (focused in pane.walk_children()) if focused is not None else False,
        "footer_keys": len(footer.query("FooterKey")),
    }


async def main():
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])

    results = {}
    async with tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args).run_test(size=(140, 45)) as pilot:
        app = pilot.app
        await pilot.pause()
        before = _state(app)
        await pilot.press("p")
        await pilot.pause()
        after = _state(app)
        results["sessions_case"] = {"active_before": before["active"], **after}

    async with tui.MetricsTUIApp(mr, tui.load_data(args, mr=mr), args=args).run_test(size=(140, 45)) as pilot:
        app = pilot.app
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        before = _state(app)
        await pilot.press("p")
        await pilot.pause()
        after = _state(app)
        results["panorama_case"] = {"active_before": before["active"], **after}

    print(json.dumps(results))


asyncio.run(main())
