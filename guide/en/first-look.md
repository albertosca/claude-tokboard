# First look

A short walk through your first dashboard.

## Before you start

tokboard only knows about turns that happened **after** you installed it.

So use Claude Code for a bit first. A few turns are enough to see something.

## Step 1 - open the dashboard

In Claude Code, run:

```
/tokboard
```

Your browser opens the page.

## Step 2 - read the trend chart

The big chart at the top shows one bar per day.

Each bar is split into colors. By default, the colors are your **projects**.

## Step 3 - change the slicing

The tabs above the chart re-slice the same day:

1. by **project**
2. by **token type**
3. by **model**
4. by **git branch**

A day stays the same height in every tab. Only the colors change.

## Step 4 - spotlight one thing

Click a legend entry or a bar.

That project, token type or model is highlighted across the whole chart.

## Step 5 - scroll down

Below the chart you find:

- the **weekly-limit meter**;
- where your **cache creation** comes from;
- **tokens per branch**;
- a **session table**, grouped by project.

Each of these has its own page:

- [The weekly-limit meter](weekly-meter.md)
- [The HTML dashboard](dashboard.md)

## Want the terminal instead?

Run `/tokboard-tui`. Same data, keyboard-driven. See [The terminal app](tui.md).
