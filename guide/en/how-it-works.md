# How it works

Three parts: hooks, a data layer, two interfaces.

## The picture

```
Claude Code
  |-- Stop hook (end of each turn) -----> sessions.jsonl
  |-- SubagentStop hook ----------------> subagents.jsonl
  |
  v
metrics_core  (one shared data layer)
  |-- /tokboard      HTML dashboard
  '-- /tokboard-tui  terminal app
```

## 1. The hooks

Two hooks append a line to a local log:

- after every **turn**;
- after every **subagent**.

They also log one row per API response and one per rate-limit rejection. These feed the [weekly-limit meter](weekly-meter.md).

## 2. The data layer

`lib/metrics_core/` is pure standard-library Python.

Both interfaces read through it. So a number means the same thing in either one.

## 3. The two interfaces

- `dashboard_html.py` writes a self-contained HTML page.
- `dashboard_tui.py` is a Textual terminal app.

They are independent consumers of the same layer.

## How it is tested

- **100% statement and branch coverage**, enforced. One uncovered line fails CI.
- **The page is tested by using it.** The rendering tests drive the real generated dashboard in jsdom: clicking, filtering, highlighting.
- **Strict static checks.** `ruff` and `mypy --strict`.
