# Install

Two commands, then you are done.

## Step 1 - install the plugin

From a shell:

```bash
claude plugin marketplace add albertosca/claude-tokboard
claude plugin install tokboard@tokboard
```

Prefer to click? Inside Claude Code, `/plugin` does the same thing interactively.

## Step 2 - restart Claude Code

Logging starts on its own. Nothing to configure.

## Step 3 - look at your data

Whenever you want to look, use one of two commands:

| Command | What it does |
|---|---|
| `/tokboard` | Builds the HTML dashboard and opens it in your browser. |
| `/tokboard-tui` | Opens the terminal app. |

## Requirements

- **`/tokboard`**: Python standard library only. Nothing to install.
- **`/tokboard-tui`**: `python3` 3.10 or newer, with the `venv` module.

## About the first `/tokboard-tui` run

The first run creates a virtual environment in the plugin's own folder (`.venv`).

It installs `textual` and `textual-plotext` into it from PyPI.

Every run then prints the command to paste into your own terminal. It looks like this, with the plugin's real folder filled in:

```bash
<plugin folder>/.venv/bin/python3 <plugin folder>/dashboard_tui.py
```

The terminal app is full-screen, so it has to run in your own terminal, not inside Claude Code.

## The browser

On macOS the browser opens by itself.

Elsewhere, open this file:

```
~/.claude/metrics/dashboard.html
```

## Problems?

On Debian or Ubuntu, `/tokboard-tui` may fail on the first run with an `ensurepip` error. See the [FAQ](faq.md#tokboard-tui-fails-on-first-run-with-an-ensurepip-error).
