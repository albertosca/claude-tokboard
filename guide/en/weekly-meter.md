# The weekly-limit meter

Ran out of weekly limit and can't tell why? This is the panel for that.

## What it shows

The meter shows the **cache creation** piled up in the current weekly window.

It keeps the 5-minute and 1-hour tiers apart.

It compares them against a **range**: the amounts seen when past weeks were first rejected.

## It is a hypothesis

Anthropic does not publish how the weekly limit is counted.

So the meter says so:

- it is labelled as a hypothesis;
- it shows how many **data points** stand behind the range.

More past rejections means a more trustworthy range.

## Where the data comes from

Both hooks log, since version 2.5.0:

- one row per **API response**, in `calls.jsonl`;
- one row per **rate-limit rejection**, in `limit-hits.jsonl`.

The meter reads those two files.

## Seeing no data?

The files start empty after an upgrade.

Seed them from the transcripts Claude Code still keeps:

```bash
python3 scripts/backfill-calls.py            # dry run
python3 scripts/backfill-calls.py --apply    # writes
```

The dry run prints the earliest day it can reach.

Claude Code deletes old transcripts, so run it **once, right after upgrading**.

## Where cache creation comes from

Each weekly window splits cache creation into three causes:

| Cause | Meaning |
|---|---|
| **Cold start** | A session started from nothing. |
| **Rewrite after idle** | The cache expired during a pause, so it was written again. |
| **Incremental growth** | The conversation simply got longer. |

The panel also shows which sessions rewrote the most after being idle.

## What to do with it

- Many rewrites after idle? Long pauses cost you. Finish or `/clear` instead of leaving a big session open.
- Many cold starts? Many fresh sessions, each paying to build its context.
- Mostly incremental growth? Long sessions. Compact earlier.
