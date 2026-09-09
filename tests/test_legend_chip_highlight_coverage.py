"""Every group-color-N swatch must have a matching legend-chip highlight rule.

Regression test for the group-color-8 gap found by manual review
(2026-08-25, python-quality-standard-phase1-toolchain): the click-to-highlight
selector list for `.legend-chip:has(.legend-swatch.group-color-N)` jumped
from group-color-7 straight to group-color-9, so clicking that one legend
chip did nothing while the other 29 worked. Nothing in the suite asserted
the two lists stayed in lockstep, so it shipped unnoticed - this closes that
gap by deriving both lists from the stylesheet itself rather than hardcoding
an expected count that could drift the same way the CSS did.
"""

from __future__ import annotations

import re

from dashboard_html_assets import STYLE

SWATCH_RE = re.compile(r"\.session-row\.(group-color-\d+)\s*\{")
CHIP_RE = re.compile(r"legend-chip:has\(\.legend-swatch\.(group-color-\d+)\)")


def test_every_group_color_swatch_has_a_legend_chip_highlight_rule() -> None:
    swatch_groups = set(SWATCH_RE.findall(STYLE))
    chip_groups = set(CHIP_RE.findall(STYLE))

    assert swatch_groups, "no group-color-N swatch rules found - regex or stylesheet drifted"
    missing = swatch_groups - chip_groups
    assert not missing, (
        f"group(s) {sorted(missing)} have a chart-bar swatch color but no "
        "legend-chip:has(.legend-swatch.group-color-N) highlight rule - "
        "clicking their legend chip would do nothing"
    )
