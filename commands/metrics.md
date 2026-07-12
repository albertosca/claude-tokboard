---
description: Generate and open the Claude Code session metrics dashboard
allowed-tools: Bash(python3 ~/.claude/hooks/metrics-report.py:*), Bash(open:*)
---

Run `python3 ~/.claude/hooks/metrics-report.py --html` to (re)generate the metrics dashboard, then open the printed path with `open` (macOS default browser). Report the path back in one short line — no further analysis unless asked.
