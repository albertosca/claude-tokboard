"""Proves hooks/metrics-log.py and hooks/subagent-metrics-log.py (and
everything they import from lib/metrics_core) actually import and run
under Python 3.9 - not just under whatever CI's default interpreter is.

Why this exists rather than just running the two hooks' own pytest files
under 3.9: hooks.json invokes each hook as
`python3 ... 2>/dev/null || true`, so an import failure on a user's
system Python (macOS still ships 3.9.6) loses every session's metrics
silently - CI needs a direct check that doesn't swallow that the way
production deliberately does. But tests/conftest.py, which pytest loads
for every test under tests/ (including
tests/test_metrics_log_hook.py and tests/test_subagent_metrics_log_hook.py
themselves), uses actual 3.10+ runtime syntax of its own
(`isinstance(x, str | bytes | os.PathLike)`, evaluated when the function
runs, not just an annotation) - so the ordinary suite cannot be collected
under 3.9 at all, hook-specific tests included. This script is the
separate, minimal path that only needs the hooks and their real
dependency (lib/metrics_core) to be 3.9-safe, which is the actual
gotcha in play; see .github/workflows/ci.yml's hooks-py39 job for how it
runs in CI, and CLAUDE.md's Gotchas for the compatibility rule this
guards.

Runs each hook exactly as hooks.json does - as a subprocess, so the
import happens in a fresh interpreter process, under a throwaway HOME
(never the real ~/.claude/metrics) - and feeds it a tiny synthetic
transcript on stdin via the same hook_input JSON shape Claude Code
itself writes. `HOOKS` is a module-level list precisely so a test can
swap in a fake, deliberately-broken "hook" to exercise every failure
branch below without needing a real Python 3.9 to be broken against.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# (hook script, the metrics filename it should append to under
# ~/.claude/metrics/, the hook_input key naming the transcript path it reads).
HookSpec = tuple[Path, str, str]

HOOKS: list[HookSpec] = [
    (REPO_ROOT / "hooks" / "metrics-log.py", "sessions.jsonl", "transcript_path"),
    (
        REPO_ROOT / "hooks" / "subagent-metrics-log.py",
        "subagents.jsonl",
        "agent_transcript_path",
    ),
]

# One assistant message with real usage - enough for parse_transcript to
# produce a non-empty record, which is what proves the hook ran its real
# logic rather than merely failing to import cleanly and returning early.
# Neither message carries a stop_reason, so neither is ever "finalised"
# (transcript_ingest.count_incomplete); the second one coming after the
# first is what makes the first "over" and so counted - a lone unfinalised
# message is always excluded as possibly still in progress. This is what
# exercises the incomplete_usage_messages key end to end under 3.9, not
# just the four token fields. Both entries also carry the same "version"
# (backlog 2026-09-28 #5), so the check below exercises claude_code_version
# too - the entry-level field parse_transcript now reads regardless of
# entry type, same as it already does for gitBranch.
SYNTHETIC_TRANSCRIPT_VERSION = "2.1.284"
SYNTHETIC_TRANSCRIPT_ENTRY = {
    "type": "assistant",
    "timestamp": "2026-01-01T00:00:00.000Z",
    "version": SYNTHETIC_TRANSCRIPT_VERSION,
    "message": {
        "id": "msg_check_1",
        "model": "claude-check",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    },
}
SYNTHETIC_TRANSCRIPT_ENTRY_2 = {
    "type": "assistant",
    "timestamp": "2026-01-01T00:00:01.000Z",
    "version": SYNTHETIC_TRANSCRIPT_VERSION,
    "message": {
        "id": "msg_check_2",
        "model": "claude-check",
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    },
}


def check_hook(
    hook_path: Path, metrics_filename: str, transcript_key: str, python_executable: str
) -> str | None:
    """Runs one hook end to end under `python_executable`. Returns None on
    success, or a human-readable description of what went wrong."""
    with tempfile.TemporaryDirectory() as home:
        home_path = Path(home)
        transcript_path = home_path / "transcript.jsonl"
        transcript_path.write_text(
            json.dumps(SYNTHETIC_TRANSCRIPT_ENTRY)
            + "\n"
            + json.dumps(SYNTHETIC_TRANSCRIPT_ENTRY_2)
            + "\n"
        )
        hook_input = {
            "session_id": "hooks-py39-check",
            "cwd": str(home_path),
            transcript_key: str(transcript_path),
        }
        environment = dict(os.environ)
        environment["HOME"] = home
        result = subprocess.run(
            [python_executable, str(hook_path)],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode != 0:
            return (
                f"{hook_path.name} exited {result.returncode} under "
                f"{python_executable}\nstderr:\n{result.stderr}"
            )
        metrics_file = home_path / ".claude" / "metrics" / metrics_filename
        if not metrics_file.exists():
            return (
                f"{hook_path.name} did not write {metrics_file} "
                f"(stdout={result.stdout!r} stderr={result.stderr!r})"
            )
        lines = metrics_file.read_text().strip().splitlines()
        if len(lines) != 1:
            return (
                f"{hook_path.name} expected exactly one record in {metrics_file}, "
                f"found {len(lines)}"
            )
        record = json.loads(lines[0])
        if record.get("input_tokens") != 10 or record.get("output_tokens") != 5:
            return f"{hook_path.name} wrote an unexpected record: {record!r}"
        if record.get("incomplete_usage_messages") != 1:
            return (
                f"{hook_path.name} did not write incomplete_usage_messages=1 for a "
                f"transcript with one over, unfinalised message: {record!r}"
            )
        if record.get("claude_code_version") != SYNTHETIC_TRANSCRIPT_VERSION:
            return (
                f"{hook_path.name} did not write claude_code_version="
                f"{SYNTHETIC_TRANSCRIPT_VERSION!r}: {record!r}"
            )
        return None


def main(
    hooks: list[HookSpec] | None = None,
    python_executable: str | None = None,
) -> int:
    hooks = HOOKS if hooks is None else hooks
    python_executable = python_executable or sys.executable
    failures = []
    for hook_path, metrics_filename, transcript_key in hooks:
        failure = check_hook(hook_path, metrics_filename, transcript_key, python_executable)
        if failure is not None:
            failures.append(failure)
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(f"Both hooks imported and ran cleanly under {python_executable}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
