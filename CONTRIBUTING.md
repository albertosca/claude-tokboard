# Contributing to tokboard

Thanks for your interest. Pull requests are welcome; for anything beyond a small fix, please open an issue first so we can agree on the direction.

## Setup

```bash
git clone https://github.com/albertosca/claude-tokboard
cd claude-tokboard
pip install -r requirements-dev.txt -r requirements-tui.txt
npm install                     # jsdom, for the rendering tests
```

`npm install` is not optional: without Node and jsdom the rendering tests fail rather than skip, on purpose.

## The quality bar

The four gates CI runs. All four must pass before review:

```bash
pytest tests/                   # full suite; 100% statement and branch coverage is a hard gate
ruff check .
ruff format --check .
mypy --strict .
```

- Tests are written first, and a fix is proven by a test that fails when the fix is reverted.
- Coverage is gated at 100%. Use `# pragma: no cover` only for genuinely unreachable defensive code, with the justification inline - never to skip a real path.
- For a subset run, use `pytest tests/test_x.py --no-cov`: the coverage gate applies to whatever ran, so a subset reports a low percentage without anything being broken.
- `hooks/` and `lib/metrics_core/` must stay Python 3.9 compatible: the hooks run on whatever `python3` the system has, and an import failure there silently loses every session's metrics.

## Layout

- `lib/metrics_core/` - the shared data layer. Both dashboards read from it.
- `dashboard_html.py` (+ `dashboard_html_assets.py`) and `dashboard_tui.py` - two independent consumers of it. They duplicate some logic, so a change to one usually needs the same change, and its own tests, in the other.
- `hooks/` - what Claude Code runs on `Stop` and `SubagentStop`. `scripts/` - repair tools, run by hand. `commands/` - the slash commands.

`CLAUDE.md` lists the gotchas in more depth.

## Your own data

`~/.claude/metrics/*.jsonl` is real, irreplaceable data. Nothing in the tests or scripts may write to it; the test suite has a guard that fails if something tries.

## Test data

Use generic placeholders in tests and docs (`my-api`, `blog-site`, `feature/example`, `/Users/username/...`), never a real project, branch or path.

## Reporting security issues

Do not open a public issue. See [SECURITY.md](SECURITY.md).
