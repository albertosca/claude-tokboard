# Upgrading

## Step 1 - update

```bash
claude plugin marketplace update tokboard
claude plugin update tokboard@tokboard
```

## Step 2 - restart Claude Code

The new version loads on restart.

## What changed, version by version

### 2.6.0

Nothing is required. The weekly-limit meter and the charts gain a few details, and the hooks log the same files as before.

Optional: you can raise a subagent row's output by the one final call that the parent session still records:

```bash
python3 scripts/backfill-subagent-last-call.py --apply
```

It is a dry run until given `--apply`. Earlier calls without a final usage stay a floor.

### 2.5.0

Both hooks now log two new files:

- `calls.jsonl`: one row per API response;
- `limit-hits.jsonl`: one row per rate-limit rejection.

They feed the [weekly-limit meter](weekly-meter.md). They start empty.

Seed them once:

```bash
python3 scripts/backfill-calls.py --apply
```

Run the dry run first, without `--apply`. Claude Code deletes old transcripts, so do this right after upgrading.

### 2.3.0

A total prefixed with "≥" is a **floor**: some messages never got a final usage entry.

To add that marker to older rows:

```bash
python3 scripts/backfill-token-dedup.py --fields incomplete_usage
```

A row whose transcript is gone never gets it, for good.

### 2.2.0

The tools count is per distinct tool call. It is about **4x higher** than before on the same work. Older rows keep the counts they were logged with.

Subagent output recorded on Claude Code 2.1.278 or later is a lower bound.

### 2.1.0

AI task labels are cached per language.

The first save converts the file and keeps the original next to it, as `task_labels.json.pre-i18n` or `agent_labels.json.pre-i18n`.

To go back to a version before 2.1.0, copy those files back over the new ones.
