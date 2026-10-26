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
  `dashboard_html_assets.py`'s `STYLE` constant,
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

## backfill-token-dedup.py

Recomputes the token fields of historical `sessions.jsonl`/`subagents.jsonl` rows that a pre-dedup hook wrote inflated, by replaying each row's own transcript as of its own `timestamp_end`. `--fields tokens` (the default) is the only mode that touches the four token fields the dashboards read, and it is idempotent: a row whose stored value already matches the reconstructed peak is left alone, so a second run against already-corrected data changes nothing.

Two other fields have their own `--fields` mode, run only when named and never implied by a token run: `--fields tool_calls` rebuilds `tool_call_count` from the transcript's distinct tool-use block ids as of each row's `timestamp_end` (every hook before 2026-09-23 undercounted it), and `--fields incomplete_usage` adds `incomplete_usage_messages` to rows written before the hooks recorded it. Both modes are just as idempotent as the token run: each only rewrites a row whose stored value still matches one of the old, wrong formulas, so re-running either against already-corrected data is a no-op.

All three modes share the same safety mechanics: they back up the target file first and verify the copy by sha256 before proceeding, write the corrected result to a new file and rename it into place only after validation (never editing in place), and carry over verbatim anything appended to the live, append-only metrics file while the run was in progress. Every mode is a dry run by default and writes nothing until `--apply` is passed.

## backfill-subagent-last-call.py

Since Claude Code 2.1.278 the last message of many subagent transcripts never gets its final usage, so the stored output of that agent is a floor. The parent session records each finished agent's result with a `usage` block that is exactly that last call, and this script raises `output_tokens` (and the matching `tokens_by_model` bucket) by the difference. It repairs only the last call: every earlier call without a final usage stays a floor and keeps counting in `incomplete_usage_messages`. A row is changed only when the parent block is provably one call, its other three fields equal the transcript's last message, and the stored row equals the transcript's own final total; every other row is left alone and the dry run says why, by reason. It reuses `backfill-token-dedup.py`'s verified backup and atomic rewrite, so `--apply` backs the file up first and is idempotent. Afterwards a repaired row no longer matches the transcript's total, so `backfill-token-dedup.py --kind subagents` leaves that unit untouched on purpose.

```
python3 scripts/backfill-subagent-last-call.py            # dry run
python3 scripts/backfill-subagent-last-call.py --apply
```

## backfill-calls.py

Seeds `calls.jsonl` (one row per API response) and `limit-hits.jsonl` (one row per rate-limit rejection) from the transcripts still on disk. The hooks fill both files from the moment they are installed; this covers what the transcripts that remain can still say, and the dry run states the earliest day it can reach (`earliest day reachable`). Claude Code deletes old transcripts, so run it once after installing the version that adds the hooks.

It only appends to those two files and never opens `sessions.jsonl`, `subagents.jsonl` or `memory-reads.jsonl`, so it takes no backup and has no `--cutoff`: a row is written only when its message id is absent from the store, which makes a second run write nothing. A message replayed into a second transcript by a resumed session is written once, at its per-field peak. After `--apply` it records each transcript's end offset in `calls_cursor.json`, so the hooks continue from there. A dry run (the default) writes nothing and prints the exact command to apply; `--projects-dir` and `--metrics-dir` point it at copies.

## official-usage.py

Runs `claude -p "/usage"` and prints its `Current session` and `Current week` percentages, then the raw report, so you can compare the plan's own figure with the weekly meter. Run it by hand: it makes a network request through the Claude Code binary (the plugin sends nothing), the report is approximate, covers this machine's sessions, and its text may change between versions (the script says so when it finds no meter lines). Nothing calls it and it writes nothing.

```
python3 scripts/official-usage.py
```

## growth-report.py

Per ISO week: API calls, cache creation, commits and the two ratios (calls and cache creation per commit), to tell "more work" from "heavier process". The calls come from `calls.jsonl`; the commits come from the repositories you pass with `--repo` (repeatable; `git log --all`, by author date). Without `--repo` the commit columns are empty and it says so. Read-only.

```
python3 scripts/growth-report.py --repo /path/to/my-api --repo /path/to/blog-site
```

## instruction-size-history.py

The size, month by month, of an instruction file tracked in git (for example a `CLAUDE.md`), with a flag on any month that grew by more than `--alert-percent` (default 25) over the previous one. Every request carries those files, so growth there is paid in every conversation. Sizes are bytes, about 4 per token. Read-only.

```
python3 scripts/instruction-size-history.py --file /path/to/CLAUDE.md
```

## fixed-context-probe.py

Decomposes the fixed context of a request by sending nine minimal `claude -p` requests with parts switched off (skills, MCP servers, user and project settings, tools, system prompt) and printing the token difference between variants. **It makes nine real API calls with your account**, so it only lists the variants unless you pass `--run`. Run it from the project you want measured, right after changing a `CLAUDE.md`, plugin or hook. Writes nothing.

```
python3 scripts/fixed-context-probe.py --run
```

## tool-results-report.py

Prints how much tool output entered your conversations: the share by tool, the share of Bash output by kind of command (search, read, claude, git, tests, other), the part that came from subagents, and what a cap on a single result would have trimmed (`--bash-cap`, `--read-cap`, in characters; defaults 8,000 and 20,000). Sizes are characters, and the token figure is characters divided by 3, an estimate.

It reads the transcripts under `--projects-dir` (default `~/.claude/projects`), counts each tool call once even when a resumed session replays it, and writes nothing: no file, and nothing in `~/.claude/metrics`. It is a report and not a dashboard panel because only the transcripts hold tool output.

```
python3 scripts/tool-results-report.py
```

## verify-detectors.py

Re-proves that `tests/` actually catches the two Criticals it was built for, instead of leaving that proof sitting in a session transcript that eventually rots. Both defects shipped past five reviews because every check that existed asserted the markup *contained* an attribute, which is true whether the thing the attribute controls works or not:

1. **The inert row filter.** `.session-row.period-hidden { display: none }` had no `!important`, and `toggleGroup` used to write an inline `style.display` on expand -- an inline declaration beats any non-`!important` class rule regardless of specificity, so no period ever actually hid an expanded row. Under `ontem` the chart showed a handful of sessions while the list below it showed every session in the history.
2. **The unscoped totals.** `render_composition_legend` used to be fed the full, unfiltered `records` instead of the period-scoped window, so it reported the same all-time token figure under every period -- several times what `ontem` actually held.

For each defect the script backs up each defect's own target file, applies an anchored patch that reproduces that exact shape, runs the full suite, records whether it went red and which test caught it, then restores the file and verifies the restore two independent ways (a sha256 comparison against the pre-patch backup, and `git diff --exit-code`). It never uses `git checkout --` to restore -- on a file that is modified but not yet committed, that command is destructive, not a restore, and it has already destroyed real work in this project twice.

Like `layout-probe.js`, this is not wired into `pytest` -- it shells out to a full `pytest tests/` run of its own, twice, and mutates its target files on disk (temporarily) to do it, which is not something a test in the suite itself should ever do.

### When to run it

Any time you touch how the period filter hides things: the `.period-hidden` CSS rule, `toggleGroup`, `selectPeriod`, or which `records` a `render_*` call is scoped from. A change here can silently turn one of these two Criticals back into a defect the suite would no longer catch, and nothing else checks that.

### How to run it

```
PYTHONPATH=lib:tests python3 scripts/verify-detectors.py
```

Exits 0 only if both defects are reported `DETECTED`, naming the test(s) that caught them, and every patched file came back byte-identical. Exits non-zero if either defect goes undetected, if a patch's anchor doesn't match its target's current text (refused loudly rather than silently applying to nothing, which would misreport a defect as undetectable when it was never actually injected), or if the restore doesn't verify clean -- any of those is a hole in the suite's teeth, or in this script, and neither is something to shrug off.
