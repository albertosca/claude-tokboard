# Security Policy

## Supported versions

Only the latest release receives security fixes. The plugin version is in `.claude-plugin/plugin.json`; upgrade with the commands in the README's "Upgrading" section.

## Reporting a vulnerability

Please report vulnerabilities privately, through GitHub Security Advisories: open this repository's **Security** tab and choose **Report a vulnerability**. That opens a private channel visible only to the maintainer.

**Do not open a public issue for a security report.** Public issues are visible to everyone, including before a fix exists.

## Trust model

tokboard runs on your own machine, on your own Claude Code data. What it reads, writes and sends:

**Reads**
- The hook payloads Claude Code passes to `hooks/metrics-log.py` and `hooks/subagent-metrics-log.py`.
- The session transcript files those payloads point to (Claude Code keeps them under `~/.claude/projects/`), to count tokens and tool calls.

**Writes**
- Only under `~/.claude/metrics/`: the session and subagent logs, the label caches, the call log and the rendered `dashboard.html`. The repair scripts in `scripts/` are dry runs unless given `--apply`, and they back the file up and verify the copy by sha256 before writing.

**Sends**
- Nothing, by default. The one exception is the opt-in AI task labels (`--label-budget N`): each new label is a `claude -p` call that sends that session's text to Claude, on your own quota.

**Untrusted input, treated as data**
- Project paths, branch names, session titles and any other text that came from your sessions are HTML-escaped before they reach the generated page, and the page runs no remote code: the HTML dashboard is one self-contained file.

A vulnerability here would be, for example, a way for crafted session content to run script in the generated page, to make a repair script write outside `~/.claude/metrics/`, or to make the hooks fail in a way that exfiltrates or destroys data.

## Known accepted risk

Commits in this repository carry the maintainer's name and email as the git author identity, and `LICENSE` and `plugin.json` carry the name. This is attribution, a conscious choice rather than a leak.
