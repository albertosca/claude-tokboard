# claude-tokboard

Claude Code plugin: logs session and token metrics through hooks, and renders them as an HTML dashboard and a Textual TUI.

- `hooks/` - `hooks.json` runs `metrics-log.py` on `Stop` and `subagent-metrics-log.py` on `SubagentStop`; they append to `~/.claude/metrics/*.jsonl`.
- `lib/metrics_core/` - the shared data layer (records, labels, periods, backfill, i18n). Not installed as a package: the entry points `sys.path.insert` `lib/`, and `mypy_path = "lib"` mirrors that.
- `dashboard_html.py` (+ `dashboard_html_assets.py`, its CSS/JS as string constants) and `dashboard_tui.py` - two independent consumers of `metrics_core`.
- `commands/` - the `/tokboard` and `/tokboard-tui` slash commands. `scripts/` - repair and maintenance tools run by hand (`scripts/README.md`). `tools/` - dev-only (demo assets).

## This repo is public

Every change - code, comments, commit messages, docs, test data - is written assuming a stranger reads it today. Concretely:

- No absolute paths, other project names, or identifying details beyond Alberto's own already-public identity (his real name and GitHub handle are in `LICENSE`, `plugin.json`, and every commit's author field by design - that's attribution, not a leak).
- No secrets, tokens, or credentials, ever - not even in a throwaway script or a `.bak` file that "won't get committed."
- Example data in docs/tests uses generic placeholders (`my-api`, `blog-site`, `/Users/username/...`), never a real project name.

See `docs/PUBLISHING.md` for how the repo goes public and what was done to its history first.

## Setup

```bash
pip install -r requirements-dev.txt   # test deps (includes textual)
npm install                           # jsdom, for the rendering tests
```

## Commands

The same four gates CI runs:

```bash
pytest tests/                    # full suite + the 100% coverage gate
ruff check .
ruff format --check .            # a separate CI step - `ruff check` does not cover it
mypy --strict .
```

For a subset: `pytest tests/test_x.py --no-cov` (see below for why `--no-cov`).

To see a change: `python3 dashboard_html.py --html` (writes `~/.claude/metrics/dashboard.html` and prints its path), or `scripts/setup-tui-venv.sh && .venv/bin/python3 dashboard_tui.py` in a real terminal - the TUI is full-screen and cannot run inside a tool call.

## Gotchas

- **`npm install` is not optional.** Without Node and jsdom the rendering tests FAIL rather than skip - deliberately, see `tests/js_runtime.py`.
- **Coverage is gated at 100%** (`[tool.coverage.report] fail_under`), and pytest-cov applies the gate to whatever ran - so a subset run reports a single-digit percentage and fails with nothing broken. Use `--no-cov` for subset runs.
- **Put `-B` before `-m pytest`, never after.** After it, pytest exits 4 with a usage error that reads exactly like a killed suite.
- **`# pragma: no cover` on an `if` line excludes the body too**, dropping live lines out of the denominator. Use `# pragma: no branch` for a dead arc. Every pragma in the repo carries its justification inline.
- **`~/.claude/metrics/*.jsonl` is real, irreplaceable user data, and nothing here may write to it.** Verify by hashing the first N bytes before and after, never by mtime: other live Claude sessions append to those files continuously, so mtime cannot tell their writes from yours.
- **`metrics_core` resolves `METRICS_FILE`, `SUBAGENTS_FILE` and the label caches from `HOME` at IMPORT time.** Setting `HOME` afterwards changes nothing - the module is already aimed at the real `~/.claude/metrics`. Tests patch the constants directly (`tests/test_tui_state.py`); scripts run a subprocess with `HOME` already in its environment (`tools/make-demo-assets.py`). A demo recording once came out full of real projects this way.
- **Under an overridden `HOME`, invoke Python as `sys.executable`, never a bare `python3`.** A version-manager shim on `PATH` can resolve through `$HOME` and exit 126 before your code runs.
- **`scripts/verify-detectors.py` rewrites real repo source files** and shells out to a full pytest run. Never call its `main()` from a test without replacing the module-level `DEFECTS` list.
- **`requirements-tui.txt` and `requirements-dev.txt` overlap on purpose.** The first is what `scripts/setup-tui-venv.sh` provisions into `.venv` for END USERS; the second is what the tests need. Collapsing them is what once hid `dashboard_tui.py` from coverage entirely.
- **The two dashboards duplicate logic.** A fix in one does not reach the other - every chart mode, for instance, is implemented twice. Change both, and test both.
- **Code that runs only inside a `sys.addaudithook` callback is invisible to coverage.py** (measured: real calls, 0% reported, under both the CTracer and sys.monitoring cores). `tests/conftest.py`'s `_audit_real_claude_dir_guard` dispatcher, and every helper only reachable through it, is this shape. Don't pragma it: call the same private functions directly, with the argument shapes `test_real_metrics_isolation.py` proved CPython produces (`tests/test_conftest_guard_direct_dispatch.py`). Keep both kinds of test - the behavioral ones prove the real audit event shapes, the direct-call ones make conftest's 100% mean anything.
- **The rendering tests run in jsdom, which performs no layout.** A green suite says nothing about sticky positioning, z-index or overlap. After touching those in `dashboard_html_assets.py`'s `STYLE`, run `scripts/layout-probe.js` (see `scripts/README.md`).
- **The full suite takes ~30 s on an idle machine and 5+ minutes under load** (1940 tests, 315 s at load average 22). Give it a timeout well above the 120 s default, and check `uptime` before reading a timeout as a hang.
- **A green 100% gate on macOS does not mean a green CI: CI runs on Linux.** Any `sys.platform` branch in measured code (including `tests/conftest.py`) needs a test that forces each platform with `monkeypatch.setattr(sys, "platform", ...)`, or the gate fails on the other one only. Reproduce locally in a Linux container as a NON-root user (`docker run --user 1000:1000 ...`): as root, the chmod-000 "unreadable state" tests fail because root can read anything. Mount a `git archive` copy under the gitignored `.superpowers/`, never under `/private/tmp`: Docker Desktop's VM does not share it, and the mount comes up empty with no error.
- **`hooks/` and `lib/metrics_core/` must stay Python 3.9-compatible.** hooks.json runs each hook as `python3 ... 2>/dev/null || true`, and a stock macOS still ships 3.9.6 - an import failure there loses every session's metrics with no visible error. ruff's `py310` target and its `B905` rule will suggest `zip(..., strict=True)`; don't take that suggestion on this path (see `transcript_ingest.py`'s `usage_increment` for the one place this was already a live concern). `.github/workflows/ci.yml`'s `hooks-py39` job and `scripts/check-hooks-py39-compat.py` guard this in CI - the ordinary suite can't, because `tests/conftest.py` itself uses real 3.10+ runtime syntax and can't even be collected under 3.9.

## Repair scripts (`scripts/backfill-*.py`)

- **`--cutoff` takes a day (`YYYY-MM-DD`), never a timestamp.** Rows at or after it are left alone; to spare rows from later the same day, cut at the next day and check in the dry run that only old-hook rows would change.
- **"Has every session restarted on the new hook?" is answered by the data, not by asking.** A row without `claude_code_version` was written by a pre-2.3.0 hook (the hook resolves per call, so even old Claude Code binaries write the field once restarted).
- **An apply refuses a `backfill_state.json` that cannot be read or is malformed** (a container or a leaf of the wrong type), before writing anything; the dry run only warns, the dashboards stay lenient. A null `corrected_rows` / `unidentified_earlier_rows` is valid ("unknown for good" - the writer emits it). Fix or move the file aside, never delete entries to get past it.
- **Every command a script prints goes through `metrics_core.script_invocation()`**, shell-quoted - pasteable from any directory. Tests run the printed command as a process.
- **Subagent output since Claude Code 2.1.278 is a permanent floor.** The only other source of subagent usage, the parent transcript's `queued_command` attachment, holds a single `totalTokens` with no input/output/cache split - already searched; the "≥" marker is the answer.

## Where things are written down

`docs/superpowers/` holds the project log, backlog, specs and plans. It is a symlink into a private repository and is gitignored here: it exists in the author's checkout and in no clone of this one. Paths under it that the code mentions resolve for him only - deliberately.
