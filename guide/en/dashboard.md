# The HTML dashboard

Open it with `/tokboard`.

![The HTML dashboard](images/dashboard.png)

## What is on the page

From top to bottom:

1. **The quota line**: how much of your rate limit a period used.
2. **The trend chart**: one bar per day, with four slicings.
3. **The weekly-limit meter**: see [the meter](weekly-meter.md).
4. **Cache-creation causes**: cold start, rewrite after idle, growth.
5. **Tokens per branch**: for projects that used 2+ branches.
6. **The session table**: every session, grouped by project.

## The trend chart

Switch the slicing with the tabs:

- project
- token type
- model
- git branch

Click a legend entry or a bar to spotlight it.

## Sessions

Sessions are grouped by project.

Each row shows:

- its subagents;
- the duration;
- the token breakdown.

You can filter by period.

## Branches

If a project used more than one git branch, a panel shows what each branch cost.

The session list can narrow to a single branch.

## A "≥" in front of a total

Some messages never got a final usage entry.

A total prefixed with "≥" means the real value may be **higher**. It is a floor, not an estimate.

## tmux

Inside tmux, the hook records the window's name with each session.

The dashboard shows it. Nothing to configure.

## Filters

Run the script directly to narrow what it shows:

```bash
python3 dashboard_html.py --html --days 14
```

| Flag | Effect |
|---|---|
| `--project` | Only sessions of this project path. |
| `--tag` | Only sessions with this tag. |
| `--days N` | Only the last N days. |
| `--all` | Accepted for compatibility. The full history is the default unless `--days` is given. |
