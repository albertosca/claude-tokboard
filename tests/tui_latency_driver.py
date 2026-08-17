"""Drives dashboard_tui.py headlessly and reports what cycling a period costs.

Not a test module (no test_ prefix, so pytest does not collect it): it is
the payload test_tui_responsiveness.py runs as a SUBPROCESS. That indirection
is not optional. pytest lives on the system interpreter, textual lives only
in the repo's .venv, and neither can import the other - so the timing test
has to hand this file to .venv/bin/python3 and read a result back.

Everything is measured inside one process, on one machine, under one load,
so the numbers are comparable to each other even when the machine is busy:

  full_loads   complete load_base_data calls - the period-INDEPENDENT work
               that used to run on every keypress. Taken while the app is
               already up, so they are warm numbers, like a reload would be.
               Several samples, because the caller compares medians: one
               unlucky sample either side must not decide the verdict.
  actions      wall time inside action_cycle_period per "p" press - the
               work the keypress is actually allowed to do.
  presses      wall time of the whole key round-trip, recompose included -
               what the human waits for.
  load_records the number of times the data layer re-read the JSONL files
               during the presses. Zero is the only correct answer.

Prints one JSON object on stdout. Never launch the app interactively.
"""
import asyncio
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "lib"))
sys.path.insert(0, REPO)

import metrics_core as mr  # noqa: E402
import dashboard_tui as tui  # noqa: E402


async def main():
    args = tui.build_arg_parser().parse_args(["--label-budget", "0"])
    data = tui.load_data(args, mr=mr)
    app = tui.MetricsTUIApp(mr, data, args=args)

    actions = []
    original_action = app.action_cycle_period

    def timed_action():
        started = time.perf_counter()
        original_action()
        actions.append(time.perf_counter() - started)

    app.action_cycle_period = timed_action

    presses = []
    periods = []
    records_seen = []
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()

        full_loads = []
        for _ in range(3):
            started = time.perf_counter()
            tui.load_base_data(args, mr=mr)
            full_loads.append(time.perf_counter() - started)

        # Counted only from here on, so the startup load above is not
        # mistaken for a reload triggered by a keypress.
        real_load_records = mr.load_records
        reload_count = {"n": 0}

        def counting_load_records(path):
            reload_count["n"] += 1
            return real_load_records(path)

        mr.load_records = counting_load_records
        try:
            for _ in range(len(mr.PERIODS)):
                started = time.perf_counter()
                await pilot.press("p")
                await pilot.pause()
                presses.append(time.perf_counter() - started)
                periods.append(app.data["period_id"])
                records_seen.append(len(app.data["records"]))
        finally:
            mr.load_records = real_load_records

    print(json.dumps({
        "full_loads": full_loads,
        "actions": actions,
        "presses": presses,
        "periods": periods,
        "load_records_calls_during_presses": reload_count["n"],
        "records_per_period": dict(zip(periods, records_seen)),
    }))


asyncio.run(main())
