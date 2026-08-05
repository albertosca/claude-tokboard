---
description: Open the interactive terminal (TUI) dashboard for Claude Code session metrics
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-tui-venv.sh:*)
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/setup-tui-venv.sh` first (it's a no-op if the plugin's `.venv` already exists - only prints something the very first time, when it's creating the venv and installing textual/textual-plotext).

Do NOT run `dashboard_tui.py` yourself via Bash - it's a full-screen interactive app, and a tool call only captures output back to you, it never hands the user a live terminal to type into (confirmed: running it this way just hangs/backgrounds with no one able to interact with it). Instead, print this exact command and ask the user to copy it into their own terminal and run it directly:

```
${CLAUDE_PLUGIN_ROOT}/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/dashboard_tui.py $ARGUMENTS
```
