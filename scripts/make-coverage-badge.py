#!/usr/bin/env python3
"""Generate a shields-style coverage badge SVG from the local coverage data.

Runs `coverage json -o -` (the coverage.py CLI, already a dependency via
pytest-cov - no new package added) and reads `totals.percent_covered` from
its stdout, so it always reflects whatever coverage data is on disk (i.e.
`pytest tests/` must have run first, since that is what writes `.coverage`).

The badge is a small, self-contained flat SVG in the style popularised by
shields.io: a "coverage" label on the left, the rounded percentage on the
right, coloured green only at exactly 100% and progressively cooler colours
below that. It has no runtime dependency of its own (just the standard
library), so it can run in CI with nothing beyond what pytest already
installed.

Usage:
    python3 scripts/make-coverage-badge.py [output_path]

`output_path` defaults to `.github/badges/coverage.svg` (relative to the
repository root, which is taken as this script's grandparent directory).
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / ".github" / "badges" / "coverage.svg"

LABEL_TEXT = "coverage"

# Thresholds are the standard shields.io five-step ramp: brightgreen is
# reserved for a genuinely complete number (this repo's gate enforces
# fail_under = 100, so "green" should mean the gate is actually passing,
# not merely "pretty good"), then green/yellow/orange/red step down as the
# number falls further from that target.
_COLOR_STEPS: list[tuple[float, str]] = [
    (100.0, "#4c1"),  # brightgreen - the gate is satisfied
    (90.0, "#97ca00"),  # green
    (75.0, "#dfb317"),  # yellow
    (50.0, "#fe7d37"),  # orange
]
_COLOR_BELOW_LOWEST = "#e05d44"  # red

# Approximate advance width (px) of Verdana 11px, the font shields.io
# badges use, for the fixed alphanumeric/punctuation set a coverage badge
# can ever contain. This is not byte-identical to shields.io's own output
# (which draws from font metrics directly) but is close enough to lay text
# out without overlap, which is all a locally generated badge needs.
_CHAR_WIDTH: dict[str, int] = {
    **dict.fromkeys("0123456789", 7),
    "%": 8,
    ".": 4,
    " ": 4,
    **dict.fromkeys("acegkstuvxyz", 6),
    **dict.fromkeys("bdfhmnopqrw", 7),
    "i": 3,
    "j": 3,
    "l": 3,
}


def get_coverage_percent() -> float:
    """Return the total coverage percentage from the local `.coverage` data.

    Shells out to `coverage json -o -` (writes the report to stdout instead
    of a file) rather than importing the `coverage` package directly, so
    this stays a thin, easily-mocked subprocess boundary in tests.
    """
    result = subprocess.run(
        ["coverage", "json", "-o", "-"],
        capture_output=True,
        check=True,
        text=True,
    )
    data = json.loads(result.stdout)
    percent: float = data["totals"]["percent_covered"]
    return percent


def badge_color(percent: float) -> str:
    """Map a coverage percentage to a shields.io-style hex colour.

    100.0 is brightgreen; anything below steps down through green, yellow
    and orange to red as it falls further from complete.
    """
    for threshold, color in _COLOR_STEPS:
        if percent >= threshold:
            return color
    return _COLOR_BELOW_LOWEST


def format_percent(percent: float) -> str:
    """Round a coverage percentage to the nearest whole number for display."""
    return f"{round(percent)}%"


def _text_width(text: str) -> int:
    """Estimate the rendered pixel width of `text` from `_CHAR_WIDTH`."""
    return sum(_CHAR_WIDTH.get(char, 7) for char in text)


def render_svg(percent: float) -> str:
    """Render the full badge SVG for a given coverage percentage."""
    value_text = format_percent(percent)
    color = badge_color(percent)

    pad = 10
    label_width = _text_width(LABEL_TEXT) + pad
    value_width = _text_width(value_text) + pad
    total_width = label_width + value_width

    label_x = label_width / 2
    value_x = label_width + value_width / 2

    aria_label = f"{LABEL_TEXT}: {value_text}"
    font = "Verdana,Geneva,DejaVu Sans,sans-serif"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="20"
     role="img" aria-label="{aria_label}">
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{total_width}" height="20" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{label_width}" height="20" fill="#555"/>
    <rect x="{label_width}" width="{value_width}" height="20" fill="{color}"/>
    <rect width="{total_width}" height="20" fill="url(#s)"/>
  </g>
  <g fill="#fff" text-anchor="middle" font-family="{font}" font-size="11">
    <text x="{label_x}" y="14">{LABEL_TEXT}</text>
    <text x="{value_x}" y="14">{value_text}</text>
  </g>
</svg>
"""


def write_badge(percent: float, output_path: Path) -> None:
    """Render the badge for `percent` and write it to `output_path`."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_svg(percent))


def main(argv: list[str]) -> int:
    output_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    percent = get_coverage_percent()
    write_badge(percent, output_path)
    print(f"Wrote {output_path} at {format_percent(percent)} coverage.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
