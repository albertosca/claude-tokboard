🇺🇸 [English](README.md) · 🇧🇷 [Português](README.pt.md)

[![CI](https://github.com/albertosca/claude-metrics-dashboard/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/albertosca/claude-metrics-dashboard/actions/workflows/ci.yml)
[![Lint](https://github.com/albertosca/claude-metrics-dashboard/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/albertosca/claude-metrics-dashboard/actions/workflows/lint.yml)
[![Coverage](https://raw.githubusercontent.com/albertosca/claude-metrics-dashboard/main/.github/badges/coverage.svg)](https://github.com/albertosca/claude-metrics-dashboard/blob/main/.github/badges/coverage.svg)

# metrics-dashboard

A Claude Code plugin that tracks your session history and token usage,
and shows it back to you two ways: an HTML dashboard, and a full
terminal (TUI) app. See what you've been working on and how much it's
costing you, project by project, day by day.

## What you get

- **Cost and token visibility** - input/output/cache tokens broken
  down by day, by project, and by token type, so you can see what's
  actually driving your usage.
- **Session history** - every session logged with an auto-generated
  task label (opt-in - see below), searchable/filterable by project or
  tag, with subagent breakdowns per session.
- **Two ways to look at it**: `/metrics` opens an interactive HTML
  dashboard in your browser; `/metrics-tui` opens a full terminal app
  (built on [Textual](https://github.com/Textualize/textual)) for when
  a browser isn't convenient.

## Install

Install this plugin through Claude Code's plugin system (see the
[Claude Code plugin docs](https://docs.claude.com/en/docs/claude-code)
for how your setup discovers and installs plugins). Once installed,
`/metrics` works immediately (zero external dependencies) - `/metrics-tui`
auto-installs its own dependencies (`textual`, `textual-plotext`) into
a dedicated virtual environment the first time you run it.

On Debian/Ubuntu, that auto-install step needs the `python3-venv` package,
which isn't always preinstalled. If `/metrics-tui` fails on first run with
an error about `ensurepip`, run `sudo apt install python3-venv` (or the
version-specific `python3.X-venv` the error message names) and try again -
`/metrics` itself is unaffected either way, since it has no dependencies
to install.

## AI-generated task labels (opt-in)

Both dashboards can auto-generate a short label per session
(e.g. "fixing auth redirect bug") by summarizing it with a lightweight
`claude -p` call. **This is off by default** (`--label-budget 0`) -
it's a real API call per uncached session, so it spends your own
quota. Pass `--label-budget N` to either dashboard's underlying script
to turn it on (`N` new labels per run; already-cached labels are
always free).

## tmux integration (optional, auto-detected)

If you run Claude Code inside tmux, session rows show which tmux
window they happened in. If you don't use tmux at all, that column
simply doesn't appear - nothing to configure.

## Uninstall / data location

All logged data lives in `~/.claude/metrics/` (`sessions.jsonl`,
`subagents.jsonl`, label caches, the rendered `dashboard.html`) -
independent of the plugin itself. Uninstalling the plugin stops new
data from being logged but doesn't delete what's already there.
