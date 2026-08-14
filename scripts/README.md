# scripts/

Tools you run by hand. Nothing in here is wired into `pytest`, and that is
deliberate for at least `layout-probe.js` -- see below.

## layout-probe.js

Drives real headless Chrome against a generated dashboard HTML file and
measures, with actual `getBoundingClientRect()` pixels, whether the sticky
period-filter bar ever visually covers the trend chart's mode tabs while
scrolling.

**pytest does NOT run this. A green test suite says nothing about
layout.** The rendering tests in `tests/test_dashboard_rendering.py` run
the dashboard's JavaScript inside jsdom, and jsdom performs no layout at
all -- it does not resolve `var()`, has no concept of `position: sticky`
actually pinning anything on screen, and every element's
`getBoundingClientRect()` is zeroed out. During the period-filters phase
a sticky-positioning fix shipped that jsdom happily waved through as
correct; headless Chrome then measured the "fixed" build as completely
inert -- byte-identical in behaviour to the broken build it replaced, 29px
of a 29px-tall element still covered at scrollY=44. A claim about visual
overlap needs a real rendering engine to back it up, and a real engine
(spawns a subprocess, needs an installed Chrome, takes real wall-clock
seconds, behaves slightly differently machine to machine) is too heavy
and too environment-dependent to gate the test suite on. So it lives
here instead, as a tool a person runs.

### When to run it

Any time you touch:
- sticky positioning (`position: sticky`, `top:`, `z-index:`) anywhere in
  `dashboard_html.py`'s `<style>` block,
- the period filter bar's height (`--period-filter-height`) or anything
  that could change it,
- the trend chart's DOM structure near `.trend-mode-tabs` or
  `.trend-container`.

### How to run it

The probe takes one argument: the path to a generated `dashboard.html`.
Against your real dashboard:

```
python3 dashboard_html.py --html
node scripts/layout-probe.js ~/.claude/metrics/dashboard.html
```

Against a fast, hermetic, throwaway dashboard built from the test fixture
instead of your real metrics history (seconds instead of minutes, and it
does not touch `~/.claude/metrics`) -- the same approach
`tests/conftest.py`'s `dashboard_page` fixture uses, reused here by hand
since this script does not run under pytest:

```
PYBIN=$(python3 -c 'import sys; print(sys.executable)')
FAKE_HOME=$(mktemp -d)
HOME="$FAKE_HOME" PYTHONPATH=lib:tests "$PYBIN" - <<'FIXTURE'
import os, sys
from datetime import datetime
sys.path.insert(0, "tests")
from fixtures.build_fixture import build_fixture
build_fixture(os.environ["HOME"], datetime.now().astimezone())
FIXTURE
HOME="$FAKE_HOME" PYTHONPATH=lib "$PYBIN" dashboard_html.py --html --label-budget 0
node scripts/layout-probe.js "$FAKE_HOME/.claude/metrics/dashboard.html"
```

`PYBIN` matters, not just style: plain `python3` on PATH is an asdf shim
that exits 126 the moment `HOME` is overridden (it resolves through
`$HOME/.asdf`), so the two `HOME="$FAKE_HOME" python3 ...` steps above
would silently fail with an unhelpful exit code if you "simplified" them
back to a bare `python3`. Resolve the real interpreter *before*
overriding `HOME`, then use that absolute path for every step that runs
under the fake one. Same lever `tests/conftest.py`'s `dashboard_page`
fixture uses (via `sys.executable`) to build a hermetic dashboard in
~0.07s instead of waiting minutes on real `~/.claude/metrics` data.

Either way, the probe exits:

- **0** -- every sampled scroll offset kept the mode tabs on screen and
  uncovered.
- **1** -- it ran to completion and found a real problem: the mode tabs
  were covered, or were not verifiably on screen, at one or more sampled
  offsets. This is the "layout is actually broken" exit code.
- **2** -- the probe itself could not run: no dashboard path given, no
  Chrome found, Chrome failed to start, navigation timed out, or the
  page it was pointed at was too short to run the sweep at all (see "the
  false green" below). This is a tooling/setup failure, distinct from
  "the layout is broken".

It needs a real Chrome or Chromium binary on this machine (checks the
usual macOS/Linux install locations; override with `CHROME_PATH` if
yours is somewhere else) and drives it over the Chrome DevTools Protocol
directly -- no `puppeteer`/`playwright` dependency, since the surface
this script needs is small enough that a raw CDP connection over Node's
built-in `WebSocket`/`fetch` is less to maintain than adding a new
`devDependency` for a script that runs occasionally, by hand.

### The false green this probe is built to refuse

Every session group on the dashboard is born collapsed. With every group
collapsed the document is only a few hundred pixels tall and the trend
chart's mode tabs never leave their starting position -- the page never
scrolls far enough for the sticky behaviour to engage at all. A naive
probe that just diffed two `getBoundingClientRect()` rectangles would
report "0px overlap" in that state, which is true and means nothing: the
tabs were never actually exercised, not proven safe. This is exactly
what fooled a reviewer on this page before.

To refuse that trap, the probe:

1. Expands every collapsed session group first, so the document is
   genuinely taller than the viewport.
2. Confirms the page can actually scroll far enough to cover every
   sampled offset -- if `window.scrollTo` would silently clamp short of
   an offset, that is reported as a loud setup failure (exit 2), not
   folded into a passing result.
3. At each sampled offset, confirms the mode tabs are actually on screen
   before treating a 0px-overlap reading as meaningful. "Not on screen"
   and "on screen with 0px overlap" are different findings and the
   probe never conflates them -- an element that isn't on screen fails
   loudly instead of silently reporting zero.
