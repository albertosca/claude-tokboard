---
description: Generate and open the Claude Code session metrics dashboard
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/dashboard_html.py:*), Bash(open:*)
---

Run `python3 ${CLAUDE_PLUGIN_ROOT}/dashboard_html.py --html` to (re)generate the metrics dashboard, then open the printed path with `open` (macOS default browser). Report the path back in one short line — no further analysis unless asked.
