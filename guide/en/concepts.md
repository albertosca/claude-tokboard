# Token types and the quota line

Not all tokens cost the same. This page explains the four kinds you will see.

## The four token types

| Type | What it is |
|---|---|
| **input** | The text you send that is not served from cache. |
| **output** | The text Claude writes back. |
| **cache read** | Context Claude reused from its cache. Cheap. |
| **cache creation** | Context written into the cache. |

These keep their English names in both languages. They are the terms Anthropic's API uses.

## Why a raw total misleads

Cache reads are usually the biggest number by far.

They are also the cheapest. Adding them to the rest hides what matters.

## The quota line

A separate line estimates how much of your rate limit a period used.

It weighs the types like this:

- **input** and **cache creation** count on one side;
- **output** counts on the other;
- **cache reads** are left out.

This is the weighting Anthropic documents for its API.

## It is an approximation

Claude Code does not publish its own formula.

So the line is labelled **approximate**. Use it to compare days and projects, not as an exact meter.

## The 5-minute and 1-hour tiers

Cache creation comes in two tiers, a 5-minute one and a 1-hour one.

The [weekly-limit meter](weekly-meter.md) keeps them apart.

## The "last bar"

The newest bucket is still in progress.

The browser shows how much of it has elapsed, hatched. A half-finished week never looks like a drop.

The terminal app says the same in a line under the chart: for example, "last bar: 37% of the week so far".
