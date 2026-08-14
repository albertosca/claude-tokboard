"""Executes the dashboard's own JavaScript and asks what is actually
visible. The two Criticals this suite exists for both shipped past checks
that asserted the markup CONTAINED an attribute - which is true whether
the filter works or does nothing at all."""
import json
import os
import subprocess

import metrics_core as mr
from js_runtime import require_node_with_jsdom

HERE = os.path.dirname(__file__)


def _fixture_records(home):
    path = os.path.join(home, ".claude", "metrics", "sessions.jsonl")
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _expectations(dashboard_page):
    # The legend's four chips are derived the same way the session ids are:
    # from metrics_core, over the records the fixture wrote, scoped by the
    # session ids metrics_core itself decided belong to the period. No part
    # of the rendering layer gets a say in what the right answer is.
    records = _fixture_records(dashboard_page.home)
    periods = {}
    for period, data in dashboard_page.expected.items():
        scoped = [r for r in records if r["session_id"] in data["session_ids"]]
        totals = mr.compute_token_type_totals(scoped)
        periods[period] = {
            "session_ids": sorted(data["session_ids"]),
            "token_total": data["token_total"],
            "legend": {series: mr.format_tokens(value) for series, value in totals.items()},
        }
    return {"periods": periods, "default_period": mr.DEFAULT_PERIOD_ID}


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
