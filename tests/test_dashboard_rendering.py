"""Executes the dashboard's own JavaScript and asks what is actually
visible. The two Criticals this suite exists for both shipped past checks
that asserted the markup CONTAINED an attribute - which is true whether
the filter works or does nothing at all."""
import json
import os
import re
import subprocess
from datetime import datetime

import metrics_core as mr
from js_runtime import require_node_with_jsdom

HERE = os.path.dirname(__file__)

# A ratchet, not a guess: the harness currently makes exactly this many
# assertions, and the Python side has no other way to notice a section
# quietly shrinking to nothing. Raise it when you add assertions; lowering
# it means you removed coverage, which is a thing to say out loud in the
# commit message rather than to absorb silently.
#
# Read off the harness's own TOTAL line, never guessed from the diff:
# 173 -> 188 when the two charts gained a magnitude check each (2026-08-14).
# The four fixture records added the same day moved it by nothing at all -
# every section's count is structural, not per-record - which is exactly
# why this has to be measured rather than reasoned about.
MIN_ASSERTIONS = 188


def _fixture_records(home):
    path = os.path.join(home, ".claude", "metrics", "sessions.jsonl")
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _trend_chip(project_path):
    # Mirrors the trend legend's own labelling rule: a worktree collapses
    # into its parent, and the chip reads "<basename>" unless the display
    # label disagrees with it, in which case it reads "<basename>:<label>".
    parent = mr.color_group_key(project_path)
    raw_name = os.path.basename((parent or "").rstrip("/")) or "sem projeto"
    label = mr.project_display_label(parent)
    return raw_name if raw_name == label else f"{raw_name}:{label}"


def _expectations(dashboard_page):
    # Every expected value below comes from metrics_core, over the records
    # the fixture wrote, scoped by the session ids metrics_core itself
    # decided belong to the period. No part of the rendering layer gets a
    # say in what the right answer is.
    records = _fixture_records(dashboard_page.home)
    # The trend chart is the one surface fed per-DAY deltas rather than
    # whole sessions - a delta is judged by its own date, so its scoping
    # cannot be derived from the session ids and needs the real window.
    # This is a second reading of the clock; the fixture deliberately sits
    # hours away from every window edge so that is safe.
    now = datetime.now().astimezone()
    deltas = mr.expand_records_to_daily_deltas(records)
    periods = {}
    for period, data in dashboard_page.expected.items():
        scoped = [r for r in records if r["session_id"] in data["session_ids"]]
        totals = mr.compute_token_type_totals(scoped)
        scoped_deltas = mr.filter_deltas_by_window(deltas, mr.resolve_period(period, now))
        # A set of project names catches a chart fed the UNFILTERED records,
        # and misses a chart fed the WRONG WINDOW whenever the two windows
        # happen to hold the same projects. The two figures below are what
        # give the charts the teeth the composition legend and the group
        # headers already had: a magnitude, not just a name.
        panorama_totals = {}
        for record in scoped:
            label = mr.project_display_label(record.get("project_path"))
            panorama_totals[label] = panorama_totals.get(label, 0) + mr.record_token_total(record)
        periods[period] = {
            "session_ids": sorted(data["session_ids"]),
            "token_total": data["token_total"],
            "legend": {series: mr.format_tokens(value) for series, value in totals.items()},
            "trend_projects": sorted({_trend_chip(d.get("project_path")) for d in scoped_deltas}),
            # Every bar segment the trend chart draws carries its own value
            # in a <title>; they add up to exactly the tokens in the window.
            "trend_tokens": sum(mr.record_token_total(d) for d in scoped_deltas),
            # Panorama is fed whole sessions, so the period's own session
            # ids are all it takes. Projects with no tokens at all are
            # dropped by the chart; the fixture has none.
            "panorama_projects": sorted(
                {mr.project_display_label(r.get("project_path")) for r in scoped}
            ),
            "panorama_totals": panorama_totals,
        }
    return {
        "periods": periods,
        "default_period": mr.DEFAULT_PERIOD_ID,
        "session_tokens": {r["session_id"]: mr.record_token_total(r) for r in records},
    }


def _drive(dashboard_page, node):
    payload = os.path.join(dashboard_page.home, "expected.json")
    with open(payload, "w") as f:
        json.dump(_expectations(dashboard_page), f)
    result = subprocess.run(
        [node, os.path.join(HERE, "js", "drive_dashboard.js"), payload],
        env=dict(os.environ, DASHBOARD=dashboard_page.html_path),
        cwd=os.path.abspath(os.path.join(HERE, "..")),
        capture_output=True, text=True, timeout=120,
    )
    return result


def test_every_period_shows_exactly_the_sessions_that_belong(dashboard_page):
    node = require_node_with_jsdom()
    result = _drive(dashboard_page, node)
    assert result.returncode == 0, (
        "the rendered page disagreed with metrics_core about what belongs "
        f"in a period\n{result.stdout}\n{result.stderr}"
    )


def test_the_harness_reports_which_periods_it_checked(dashboard_page):
    # Guards against the harness passing by checking nothing - an empty
    # loop exits 0 just as happily as a satisfied one.
    node = require_node_with_jsdom()
    result = _drive(dashboard_page, node)
    for period in ("ontem", "desde-segunda", "15-dias", "mes", "tudo"):
        assert period in result.stdout, f"harness never reported on {period}: {result.stdout}"
    assert "assertions" in result.stdout.lower()
    # Naming the periods is not enough on its own: several sections derive
    # how much they check from the fixture and guard it with an `if`, so a
    # fixture change could delete assertions without deleting any output.
    total = re.search(r"^TOTAL: (\d+) assertions", result.stdout, re.MULTILINE)
    assert total, f"harness printed no TOTAL line: {result.stdout}"
    assert int(total.group(1)) >= MIN_ASSERTIONS, (
        f"the harness made {total.group(1)} assertions, down from {MIN_ASSERTIONS} - "
        f"coverage was removed, not added\n{result.stdout}"
    )
