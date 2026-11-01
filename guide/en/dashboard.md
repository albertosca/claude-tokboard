# The HTML dashboard

Open it with `/tokboard`.

![The HTML dashboard](images/dashboard.png)

## What is on the page

Two tabs: **Sessions** and **Overview**.

**Sessions** holds, from top to bottom:

1. **The quota line** with the token-type chips, and under them **two summary lines**: what the weekly-limit meter and the cache-creation causes found, each in one sentence.
2. **A closed block of details** with the two full cards. Open it when a summary line makes you want the numbers.
3. **The session table**: every session, grouped by project. A project that used 2+ branches says so on its own line, as `main 73% · feat/x 16% · +2`: its two biggest branches by tokens, and how many more there are.

**Overview** holds:

1. **The quota line**: how much of your rate limit a period used.
2. **The trend chart**: one bar per day, with four slicings.

The block of details holds two cards, each keeping its tables closed until you open them:

1. **The weekly-limit meter**: see [the meter](weekly-meter.md).
2. **Cache-creation causes**: a bar per recent window splitting cold start, rewrite after idle and growth.

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

If a project used more than one git branch, its line in the session table shows what the two biggest branches cost, as a share of the project's tokens.

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
