# claude-metrics-dashboard

Session metrics logger and HTML dashboard for Claude Code. Logs
per-session token usage, duration, tmux window, and an auto-generated
task label to `~/.claude/metrics/sessions.jsonl`, and renders an
interactive HTML dashboard from it.

## What's here

- `hooks/metrics-log.py` — wired to Claude Code's `Stop` hook (see
  `link.sh` below). Appends one JSON record per session pause to
  `~/.claude/metrics/sessions.jsonl`.
- `dashboard_html.py` — reads `sessions.jsonl` and either prints a
  terminal table (default) or generates an HTML dashboard (`--html`) at
  `~/.claude/metrics/dashboard.html`, including a lazy, cached
  auto-generated task label per session (via a headless `claude -p`
  call) and a token composition/trend visualization.
- `commands/metrics.md` — a Claude Code personal slash command
  (`/metrics`) that regenerates the dashboard and opens it.

## Setup

This project isn't installed as a package — it's symlinked into place,
the same way Alberto's dotfiles work:

```bash
./link.sh
```

This symlinks `hooks/metrics-log.py` and `commands/` into `~/.claude/hooks/` and `~/.claude/commands/`
respectively. You'll also need `metrics-log.py` wired to the `Stop`
event in `~/.claude/settings.json` — see `albertosca/dotfiles`'s
`config/claude/settings.json` for the exact hook configuration this
project expects.

## Terminal dashboard (TUI)

A navigable terminal-native dashboard, feature-equivalent to the HTML
one, for when a browser isn't convenient. Uses `textual` +
`textual-plotext` (this project's only non-stdlib dependencies) —
isolated to their own virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-tui.txt
python3 dashboard_tui.py
```

Same filtering flags as the HTML/table modes (`--tag`, `--project`,
`-n`, `--days`, `--all`).

## Design docs

Full design history in `docs/superpowers/specs/` and
`docs/superpowers/plans/`, plus `docs/superpowers/PROJECT-LOG.md` for a
chronological summary.
