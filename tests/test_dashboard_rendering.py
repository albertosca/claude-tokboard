"""Executes the dashboard's own JavaScript and asks what is actually
visible. The two Criticals this suite exists for both shipped past checks
that asserted the markup CONTAINED an attribute - which is true whether
the filter works or does nothing at all."""

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import metrics_core as mr
from conftest import DashboardPage
from js_runtime import require_node_with_jsdom

import dashboard_html
import dashboard_html_assets

HERE = Path(__file__).resolve().parent

# A ratchet, not a guess: the harness currently makes exactly this many
# assertions, and the Python side has no other way to notice a section
# quietly shrinking to nothing. Raise it when you add assertions; lowering
# it means you removed coverage, which is a thing to say out loud in the
# commit message rather than to absorb silently.
#
# Read off the harness's own TOTAL line, never guessed from the diff:
# 173 -> 188 when the two charts gained a magnitude check each (2026-08-14);
# 188 -> 238 when the [trend-mode] section was added to catch a sub-tab
# click on one period blanking every other period's chart (2026-08-18).
# The four fixture records added the same day moved it by nothing at all -
# every section's count is structural, not per-record - which is exactly
# why this has to be measured rather than reasoned about.
MINIMUM_ASSERTIONS = 238


def _fixture_records(home: str) -> list[dict[str, Any]]:
    path = Path(home) / ".claude" / "metrics" / "sessions.jsonl"
    with path.open() as sessions_file:
        return [json.loads(line) for line in sessions_file if line.strip()]


def _trend_chip(project_path: str | None) -> str:
    # Mirrors the trend legend's own labelling rule: a worktree collapses
    # into its parent, and the chip reads "<basename>" unless the display
    # label disagrees with it, in which case it reads "<basename>:<label>".
    parent = mr.color_group_key(project_path)
    raw_name = Path((parent or "").rstrip("/")).name or "sem projeto"
    label = mr.project_display_label(parent, lang="pt")
    return raw_name if raw_name == label else f"{raw_name}:{label}"


def _expectations(dashboard_page: DashboardPage) -> dict[str, Any]:
    # Every expected value below comes from metrics_core, over the records
    # the fixture wrote, scoped by the session ids metrics_core itself
    # decided belong to the period. No part of the rendering layer gets a
    # say in what the right answer is.
    records = _fixture_records(dashboard_page.home)
    # The trend chart is the one surface fed per-DAY deltas rather than
    # whole sessions - a delta is judged by its own date, so its scoping
    # cannot be derived from the session ids and needs the real window.
    # This is a second reading of the clock. The fixture records (today-a
    # and today-b) sit at fractions of today - not hours from edges, but
    # seconds after midnight. The exposure is a race condition between this
    # read and the fixture's timestamps across a midnight boundary. On
    # Mondays, the worst case is a roughly 90-second window at the very
    # start of the day where the trend chart could be empty; this is
    # documented in build_fixture.py's _today_at docstring. A third clock
    # read is unavoidable (the dashboard subprocess reads its own), so the
    # second one here is acceptable as long as that window is known.
    now = datetime.now().astimezone()
    deltas = mr.expand_records_to_daily_deltas(records)
    periods: dict[str, dict[str, Any]] = {}
    for period, data in dashboard_page.expected.items():
        scoped = [record for record in records if record["session_id"] in data["session_ids"]]
        totals = mr.compute_token_type_totals(scoped)
        scoped_deltas = mr.filter_deltas_by_window(deltas, mr.resolve_period(period, now))
        # A set of project names catches a chart fed the UNFILTERED records,
        # and misses a chart fed the WRONG WINDOW whenever the two windows
        # happen to hold the same projects. The two figures below are what
        # give the charts the teeth the composition legend and the group
        # headers already had: a magnitude, not just a name.
        panorama_totals: dict[str, int] = {}
        for record in scoped:
            label = mr.project_display_label(record.get("project_path"), lang="pt")
            panorama_totals[label] = panorama_totals.get(label, 0) + mr.record_token_total(record)
        periods[period] = {
            "session_ids": sorted(data["session_ids"]),
            "token_total": data["token_total"],
            "legend": {series: mr.format_tokens(value) for series, value in totals.items()},
            "trend_projects": sorted(
                {_trend_chip(delta.get("project_path")) for delta in scoped_deltas}
            ),
            # Every bar segment the trend chart draws carries its own value
            # in a <title>; they add up to exactly the tokens in the window.
            "trend_tokens": sum(mr.record_token_total(delta) for delta in scoped_deltas),
            # Panorama is fed whole sessions, so the period's own session
            # ids are all it takes. Projects with no tokens at all are
            # dropped by the chart; the fixture has none.
            "panorama_projects": sorted(
                {
                    mr.project_display_label(record.get("project_path"), lang="pt")
                    for record in scoped
                }
            ),
            "panorama_totals": panorama_totals,
        }
    return {
        "periods": periods,
        "default_period": mr.DEFAULT_PERIOD_ID,
        "session_tokens": {
            record["session_id"]: mr.record_token_total(record) for record in records
        },
    }


def _drive(dashboard_page: DashboardPage, node: str) -> subprocess.CompletedProcess[str]:
    payload = Path(dashboard_page.home) / "expected.json"
    with payload.open("w") as payload_file:
        json.dump(_expectations(dashboard_page), payload_file)
    return subprocess.run(
        [node, str(HERE / "js" / "drive_dashboard.js"), str(payload)],
        env=dict(os.environ, DASHBOARD=dashboard_page.html_path),
        cwd=str((HERE / "..").resolve()),
        capture_output=True,
        text=True,
        timeout=120,
    )


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
    assert int(total.group(1)) >= MINIMUM_ASSERTIONS, (
        f"the harness made {total.group(1)} assertions, down from {MINIMUM_ASSERTIONS} - "
        f"coverage was removed, not added\n{result.stdout}"
    )


def _model_bucket(**fields: int) -> dict[str, int]:
    bucket = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    bucket.update(fields)
    return bucket


def test_a_model_past_the_colour_palette_highlights_independently_of_its_donor(
    tmp_path: Path,
) -> None:
    # M1's markup-level tests (test_dashboard_html_charts.py) prove the
    # overflow band and its donor carry DIFFERENT class names - which is
    # necessary but not sufficient. This is the same lesson this whole
    # module exists for (see its docstring): a CSS rule can still light
    # both classes together despite the markup being perfectly correct,
    # and that is exactly the bug this test caught on first implementation
    # - a selector list long enough to enumerate every model-overflow-N
    # class silently stopped matching in jsdom's CSS engine, dimming
    # nothing at all. Only asking getComputedStyle for the real, cascaded
    # opacity - not reading the stylesheet source - can catch that.
    node = require_node_with_jsdom()
    by_model = {f"claude-match{index}": _model_bucket(input_tokens=10) for index in range(31)}
    records = [
        {
            "session_id": "day1",
            "project_path": "/proj/a",
            "timestamp_start": "2026-01-01T10:00:00Z",
            "input_tokens": 310,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "tokens_by_model": by_model,
        },
        {
            "session_id": "day2",
            "project_path": "/proj/a",
            "timestamp_start": "2026-01-02T10:00:00Z",
            "input_tokens": 100,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        },
    ]
    fragment = dashboard_html.render_trend_chart(
        records, {"/proj/a": "group-color-1"}, "p1", lang="pt"
    )
    page = (
        "<!doctype html><html><head>"
        + dashboard_html_assets.STYLE
        + "</head><body>"
        + fragment
        + dashboard_html_assets.SCRIPT
        + "</body></html>"
    )
    fragment_path = tmp_path / "model_overflow.html"
    fragment_path.write_text(page)
    result = subprocess.run(
        [node, str(HERE / "js" / "drive_model_overflow.js"), str(fragment_path)],
        # A minimal explicit env, not the inherited process one: this
        # script reads nothing from the environment (the fragment path
        # travels as an argv, not an env var, unlike drive_dashboard.js's
        # DASHBOARD) - PATH is only here in case node itself needs it to
        # resolve a shared library.
        env={"PATH": os.environ.get("PATH", "")},
        cwd=str((HERE / "..").resolve()),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"the overflow model's band/chip did not highlight independently of its "
        f"donor's\n{result.stdout}\n{result.stderr}"
    )
