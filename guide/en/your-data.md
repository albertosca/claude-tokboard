# Your data

Short version: it stays on your machine.

## Where it lives

Everything is in `~/.claude/metrics/`:

- the session log (`sessions.jsonl`);
- the subagent log (`subagents.jsonl`);
- the per-call log (`calls.jsonl`) and rejections (`limit-hits.jsonl`);
- the label caches;
- the rendered dashboard.

## What leaves your machine

Nothing.

There is one exception: **AI task labels**. If you turn them on, each labelled session is sent to Claude to be summarized. They are off by default.

## Uninstalling

Uninstalling stops new logging.

It keeps what is already there. Delete `~/.claude/metrics/` yourself if you want it gone.

## Repair scripts

Old logs can be repaired by scripts in `scripts/`.

They are careful:

- they are **dry runs** unless you pass `--apply`;
- they **back up** the file first;
- they **verify the copy** by sha256 before writing;
- the per-model one first checks its reconstruction against records the hook logged natively, and **refuses to run** if they disagree.

## Why the logs hold duplicates

Claude Code repeats each API response once per content block. A resumed session also replays old messages into a new file.

So the raw logs hold duplicates by design. tokboard reads them through one function that counts each message once.
