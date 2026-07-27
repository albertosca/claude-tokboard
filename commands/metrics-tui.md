---
description: Open the interactive terminal (TUI) dashboard for Claude Code session metrics
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-tui-venv.sh:*), Bash(${CLAUDE_PLUGIN_ROOT}/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/dashboard_tui.py:*)
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/setup-tui-venv.sh` first (it's a no-op if the plugin's `.venv` already exists - only prints something the very first time, when it's creating the venv and installing textual/textual-plotext). Then run `${CLAUDE_PLUGIN_ROOT}/.venv/bin/python3 ${CLAUDE_PLUGIN_ROOT}/dashboard_tui.py $ARGUMENTS` in the user's terminal (this is an interactive full-screen TUI app - it must run directly in their terminal, not be captured/piped) and tell them it's opening.
