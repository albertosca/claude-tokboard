# FAQ

## The dashboard is empty

tokboard only knows turns that happened after you installed it. Use Claude Code for a few turns, then run `/tokboard` again.

## `/tokboard-tui` fails on first run with an `ensurepip` error

This happens on Debian and Ubuntu.

The auto-install needs the `python3-venv` package, which is not always preinstalled.

Run:

```bash
sudo apt install python3-venv
```

Or the version-specific `python3.X-venv` the error names. Then try again.

`/tokboard` is unaffected either way.

## The weekly-limit meter says "No calls stored yet"

The per-call log starts empty after an upgrade. Seed it once. See [the meter](weekly-meter.md#seeing-no-data).

## Why does a total start with "≥"?

Some messages never got a final usage entry. The real total may be higher. See [the dashboard](dashboard.md#a-in-front-of-a-total).

## Why is the quota line "approximate"?

Claude Code does not publish its own formula. See [Token types and the quota line](concepts.md#it-is-an-approximation).

## Why does a project show up under the wrong name?

A session is logged under the directory it ran in. Set `project_roots`. See [Settings](settings.md#naming-your-projects).

## Does anything leave my machine?

No. The only exception is AI task labels, which are off by default. See [Your data](your-data.md).

## Can I use it outside macOS?

Yes. Only the browser auto-open is macOS-specific. Elsewhere, open `~/.claude/metrics/dashboard.html`.

## How do I report a bug or contribute?

Open an issue on [GitHub](https://github.com/albertosca/claude-tokboard). For a vulnerability, follow [SECURITY.md](https://github.com/albertosca/claude-tokboard/blob/main/SECURITY.md).
