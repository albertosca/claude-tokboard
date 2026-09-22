# claude-tokboard

Claude Code plugin: logs session and token metrics through hooks, and renders them as an HTML dashboard and a Textual TUI. `lib/metrics_core/` is the shared data layer; `dashboard_html.py` and `dashboard_tui.py` are two independent consumers of it.

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

## Where things are written down

`docs/superpowers/` holds the project log, backlog, specs and plans. It is a symlink into a private repository and is gitignored here: it exists in the author's checkout and in no clone of this one. Paths under it that the code mentions resolve for him only - deliberately.
