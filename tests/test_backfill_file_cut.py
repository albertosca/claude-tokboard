"""The file-prefix cut: what a hook really read when it wrote a row.

The hook parses the transcript FILE top to bottom and records, as
timestamp_end, the timestamp of the last timestamped line it read - any
line type, not only assistant entries. Claude Code does not write lines in
timestamp order, so "every assistant entry stamped at or before
timestamp_end" (the time cut) can include an entry the hook had not read
yet, or leave out one it had. Measured 2026-09-29 on real history: 24
session rows in 9 units matched no formula under the time cut and all 24
matched exactly at the file prefix, which refused 745 rows that were
already correct.
"""

import importlib.util
import json
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import metrics_core as mr
import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill-token-dedup.py"


def assistant(
    stamp: str | None,
    message_id: str | None,
    input_tokens: int,
    output_tokens: int = 0,
    tools: tuple[str, ...] = (),
    model: str = "model-alpha",
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "tool_use", "id": tool} for tool in tools]
    message: dict[str, Any] = {
        "model": model,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "content": content,
    }
    if message_id is not None:
        message["id"] = message_id
    return {"type": "assistant", "timestamp": stamp, "message": message}


def other(stamp: str | None, kind: str = "attachment") -> dict[str, Any]:
    line: dict[str, Any] = {"type": kind}
    if stamp is not None:
        line["timestamp"] = stamp
    return line


def write(path: Path, lines: Sequence[dict[str, Any] | str]) -> str:
    path.write_text(
        "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines)
    )
    return str(path)


def row(
    session_id: str, end: str, input_tokens: int, output_tokens: int = 0, **extra: Any
) -> dict[str, Any]:
    result = {
        "session_id": session_id,
        "timestamp_end": end,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    result.update(extra)
    return result


# An assistant entry stamped BEFORE the last line the hook read, but
# written to the file AFTER it (the real shape: a queue-operation line at
# 13:18:04.743 followed by an assistant entry stamped 13:18:01.209).
BACKWARD = [
    assistant("2026-08-01T10:00:00Z", "m1", 100),
    other("2026-08-01T10:00:05Z", "queue-operation"),
    assistant("2026-08-01T10:00:03Z", "m2", 50),
]

# The last line read carries an EARLIER timestamp than an assistant entry
# above it (the real shape: an attachment at 22:23:09.788 after an entry
# stamped 22:23:10.788).
FORWARD = [
    assistant("2026-08-01T10:00:00Z", "m1", 100),
    assistant("2026-08-01T10:00:07Z", "m2", 50),
    other("2026-08-01T10:00:05Z"),
]


# --- scan_cut_points ---------------------------------------------------------


def test_scan_cut_points_counts_assistant_entries_read_when_each_timestamp_was_last(tmp_path):
    path = write(
        tmp_path / "t.jsonl",
        [
            other(None, "mode"),  # nothing read carries a timestamp yet
            assistant("2026-08-01T10:00:00Z", "m1", 1),
            other("2026-08-01T10:00:05Z"),
            other(None, "permission-mode"),  # untimestamped: the cut runs on through it
            assistant(None, "m9", 1),  # ...and through an untimestamped entry
            assistant("2026-08-01T10:00:03Z", "m2", 1),
            "",
            "{not json",
            other("2026-08-01T10:00:05Z"),  # the same stamp again, later in the file
        ],
    )

    cuts = mr.scan_cut_points(path)

    assert cuts == {
        "2026-08-01T10:00:00Z": (1,),
        "2026-08-01T10:00:05Z": (1, 2, 3),
        "2026-08-01T10:00:03Z": (3,),
    }


def test_scan_cut_points_count_indexes_the_same_entries_scan_transcript_returns(tmp_path):
    path = write(tmp_path / "t.jsonl", FORWARD)
    timeline = mr.scan_transcript(path)
    assert max(max(counts) for counts in mr.scan_cut_points(path).values()) == len(timeline)


def test_build_file_order_sums_totals_the_first_n_entries_in_file_order(tmp_path):
    path = write(
        tmp_path / "t.jsonl",
        [
            assistant("2026-08-01T10:00:09Z", "m1", 100),
            assistant("2026-08-01T10:00:00Z", "m1", 100),  # replay, earlier stamp
            assistant("2026-08-01T10:00:05Z", "m2", 7),
        ],
    )
    sums = mr.build_file_order_sums(mr.scan_transcript(path))

    assert mr.at_cut(sums, 0) == mr.AsOf([0] * 4, [0] * 4, [0] * 4)
    assert mr.at_cut(sums, 2) == mr.AsOf([100, 0, 0, 0], [100, 0, 0, 0], [200, 0, 0, 0])
    assert mr.at_cut(sums, 3).peak == [107, 0, 0, 0]


# --- the token pass ----------------------------------------------------------


def test_a_row_the_hook_wrote_before_a_late_written_entry_is_already_correct(tmp_path):
    transcripts = {"s": write(tmp_path / "s.jsonl", BACKWARD)}
    records = [row("s", "2026-08-01T10:00:05Z", 100)]  # the time cut says 150

    plan = mr.plan_corrections_by_formula(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats == {"untouched: already correct": 1}


def test_a_row_that_read_an_entry_stamped_after_its_end_is_already_correct(tmp_path):
    transcripts = {"s": write(tmp_path / "s.jsonl", FORWARD)}
    records = [row("s", "2026-08-01T10:00:05Z", 150)]  # the time cut says 100

    plan = mr.plan_corrections_by_formula(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats == {"untouched: already correct": 1}


def test_a_raw_sum_at_the_file_cut_is_corrected_to_the_peak_at_that_cut(tmp_path):
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                assistant("2026-08-01T10:00:00Z", "m1", 100, model="model-alpha"),
                assistant("2026-08-01T10:00:00Z", "m1", 100, model="model-alpha"),  # replay
                other("2026-08-01T10:00:05Z"),
                assistant("2026-08-01T10:00:03Z", "m2", 50, model="model-beta"),  # read later
            ],
        )
    }
    records = [row("s", "2026-08-01T10:00:05Z", 200, tokens_by_model={})]

    plan = mr.plan_corrections_by_formula(records, transcripts, "2026-09-01")

    assert plan.values == {0: [100, 0, 0, 0]}
    assert plan.formulas == {0: mr.RAW}
    # the map is rebuilt from the same cut: m2 was never read
    assert plan.model_maps[0] == {
        "model-alpha": {
            "input_tokens": 100,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
        }
    }


def test_a_row_no_file_cut_reproduces_falls_back_to_the_time_cut(tmp_path):
    transcripts = {"s": write(tmp_path / "s.jsonl", BACKWARD)}
    records = [
        row("s", "2026-08-01T10:00:05Z", 150),  # the time cut's peak, no prefix's
        row("s", "2026-08-01T10:00:04Z", 150),  # no line carries this stamp at all
    ]

    plan = mr.plan_corrections_by_formula(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats == {"untouched: already correct": 2}


def test_file_cuts_that_reproduce_the_tokens_but_disagree_refuse_the_unit(tmp_path):
    # 200 is the raw sum of the first cut (m1 and its replay, peak 100) and
    # the peak of the second (m1 plus m2): which one the hook read decides
    # whether the row is broken, and nothing in the row says which.
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                assistant("2026-08-01T10:00:00Z", "m1", 100),
                assistant("2026-08-01T10:00:00Z", "m1", 100),
                other("2026-08-01T10:00:05Z"),
                assistant("2026-08-01T10:00:01Z", "m2", 100),
                other("2026-08-01T10:00:05Z"),
            ],
        )
    }
    records = [
        row("s", "2026-08-01T10:00:00Z", 200),  # correctable on its own: raw
        row("s", "2026-08-01T10:00:05Z", 200),
    ]

    plan = mr.plan_corrections_by_formula(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats == {"untouched: session has a row whose file cut is ambiguous": 2}


def test_file_cuts_that_agree_are_not_ambiguous(tmp_path):
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                assistant("2026-08-01T10:00:00Z", "m1", 100),
                other("2026-08-01T10:00:05Z"),
                other("2026-08-01T10:00:05Z"),
            ],
        )
    }
    records = [row("s", "2026-08-01T10:00:05Z", 100)]

    plan = mr.plan_corrections_by_formula(records, transcripts, "2026-09-01")

    assert plan.stats == {"untouched: already correct": 1}


def test_a_raw_sum_no_file_cut_reproduces_is_corrected_at_the_time_cut(tmp_path):
    # No line carries 10:00:09, so no file cut exists: the time cut decides,
    # and the map is rebuilt from the same entries as the totals.
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                assistant("2026-08-01T10:00:00Z", "m1", 100),
                assistant("2026-08-01T10:00:01Z", "m1", 100),  # replay
                assistant("2026-08-01T10:00:20Z", "m2", 50, model="model-beta"),
            ],
        )
    }
    records = [row("s", "2026-08-01T10:00:09Z", 200, tokens_by_model={})]

    plan = mr.plan_corrections_by_formula(records, transcripts, "2026-09-01")

    assert plan.values == {0: [100, 0, 0, 0]}
    assert list(plan.model_maps[0]) == ["model-alpha"]


# --- the tool_calls pass -----------------------------------------------------


# m1 is streamed: its tool_use block arrives in its SECOND entry. m2 is
# stamped before the queue-operation line but written after it, so the hook
# that stopped at the queue-operation never saw m2's tool call.
STREAMED_TOOLS = [
    assistant("2026-08-01T10:00:00Z", "m1", 100),
    assistant("2026-08-01T10:00:00Z", "m1", 100, tools=("t1",)),
    other("2026-08-01T10:00:05Z", "queue-operation"),
    assistant("2026-08-01T10:00:03Z", "m2", 50, tools=("t2",)),
]


def test_a_tool_count_is_corrected_to_the_count_at_the_cut_its_tokens_reproduce(tmp_path):
    transcripts = {"s": write(tmp_path / "s.jsonl", STREAMED_TOOLS)}
    # tokens: the peak of m1 alone - the hook read up to the queue-operation.
    # 0 is m1's first-entry count there; at the time cut the first-entry
    # count is 1 (m2's) and the distinct count 2, so the row used to match
    # no formula and would have been raised to 2 had it matched one.
    records = [row("s", "2026-08-01T10:00:05Z", 100, tool_call_count=0)]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01")

    assert plan.values == {0: 1}
    assert plan.formulas == {0: mr.FIRST}


def test_a_tool_count_no_file_cut_explains_uses_the_time_cut(tmp_path):
    transcripts = {"s": write(tmp_path / "s.jsonl", STREAMED_TOOLS)}
    # tokens 150: m1 and m2, the time cut; no prefix reproduces them
    records = [row("s", "2026-08-01T10:00:05Z", 150, tool_call_count=1)]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01")

    assert plan.values == {0: 2}
    assert plan.formulas == {0: mr.FIRST}


def test_file_cuts_that_reproduce_the_tokens_but_disagree_on_the_tool_count_refuse(tmp_path):
    # The second entry of m1 carries no new usage, only a tool_use block:
    # both cuts reproduce the tokens, and they disagree on the count.
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                assistant("2026-08-01T10:00:00Z", "m1", 100),
                other("2026-08-01T10:00:05Z"),
                assistant("2026-08-01T10:00:00Z", "m1", 100, tools=("t1",)),
                other("2026-08-01T10:00:05Z"),
            ],
        )
    }
    records = [row("s", "2026-08-01T10:00:05Z", 100, tool_call_count=0)]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats == {"untouched: session has a row whose file cut is ambiguous": 1}


def test_a_tool_count_an_earlier_time_cut_backfill_wrote_is_moved_to_the_file_cut(tmp_path):
    # The 2026-09-28 apply rebuilt counts at the time cut: this row's hook
    # value (0, m1's first entry) became 2, which counts m2's tool call the
    # hook never read. 2 is no hook formula at the file cut the tokens
    # reproduce; it is exactly the time cut's distinct count.
    transcripts = {"s": write(tmp_path / "s.jsonl", STREAMED_TOOLS)}
    records = [
        row("s", "2026-08-01T10:00:05Z", 100, tool_call_count=2),
        row("s", "2026-08-01T10:00:06Z", 150, tool_call_count=2),  # time cut, correct
    ]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01")

    assert plan.values == {0: 1}
    assert plan.formulas == {0: mr.TIME_CUT}
    assert plan.stats == {
        "untouched: already correct": 1,
        "corrected: stored an earlier backfill's time-cut count": 1,
    }


def test_a_hook_formula_wins_over_the_time_cut_count_when_both_match(tmp_path):
    # 2 is both the every-entry count at the file cut (m1 and its replay)
    # and the distinct count at the time cut (t1, t2): a hook wrote it, so
    # it is read as the hook's formula, whose direction is known.
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                assistant("2026-08-01T10:00:00Z", "m1", 100, tools=("t1",)),
                assistant("2026-08-01T10:00:00Z", "m1", 100, tools=("t1",)),
                other("2026-08-01T10:00:05Z", "queue-operation"),
                assistant("2026-08-01T10:00:03Z", "m2", 50, tools=("t2",)),
            ],
        )
    }
    records = [row("s", "2026-08-01T10:00:05Z", 100, tool_call_count=2)]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01")

    assert plan.values == {0: 1}
    assert plan.formulas == {0: mr.RAW}


# --- the incomplete_usage pass -----------------------------------------------


def unfinished(stamp: str, message_id: str, input_tokens: int) -> dict[str, Any]:
    line = assistant(stamp, message_id, input_tokens)
    line["message"]["stop_reason"] = None
    return line


def test_incomplete_usage_is_counted_at_the_cut_the_rows_tokens_reproduce(tmp_path):
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                unfinished("2026-08-01T10:00:00Z", "m1", 100),
                assistant("2026-08-01T10:00:01Z", "m2", 10),
                other("2026-08-01T10:00:05Z", "queue-operation"),
                assistant("2026-08-01T10:00:03Z", "m3", 50),  # written after the read
            ],
        )
    }
    # 110 is m1 and m2: the file cut. The time cut (160) does not reproduce
    # the row, which used to refuse the unit.
    records = [row("s", "2026-08-01T10:00:05Z", 110)]

    plan = mr.plan_incomplete_usage(records, transcripts, "2026-09-01")

    assert plan.values == {0: 1}


def test_incomplete_usage_refuses_file_cuts_that_disagree_on_the_count(tmp_path):
    # m2 adds no tokens, so both cuts reproduce the row; it also ends m1,
    # which only the second cut can therefore count as incomplete.
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                unfinished("2026-08-01T10:00:00Z", "m1", 100),
                other("2026-08-01T10:00:05Z"),
                assistant("2026-08-01T10:00:00Z", "m2", 0),
                other("2026-08-01T10:00:05Z"),
            ],
        )
    }
    records = [row("s", "2026-08-01T10:00:05Z", 100)]

    plan = mr.plan_incomplete_usage(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats == {"untouched: session has a row whose file cut is ambiguous": 1}


def test_incomplete_usage_still_refuses_a_row_reproduced_only_as_a_broken_formula(tmp_path):
    # At the file cut 200 is the raw sum, not the peak: the tokens are not
    # what the current hook writes, so the count is not trusted either.
    transcripts = {
        "s": write(
            tmp_path / "s.jsonl",
            [
                unfinished("2026-08-01T10:00:00Z", "m1", 100),
                unfinished("2026-08-01T10:00:00Z", "m1", 100),
                other("2026-08-01T10:00:05Z"),
            ],
        )
    }
    records = [row("s", "2026-08-01T10:00:05Z", 200)]

    plan = mr.plan_incomplete_usage(records, transcripts, "2026-09-01")

    assert plan.stats == {
        "untouched: session has a row whose tokens the transcript does not reproduce": 1
    }


# --- the script ----------------------------------------------------------------


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("backfill_token_dedup_file_cut", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_moves_a_time_cut_count_to_the_file_cut_and_then_leaves_it(
    tmp_path, script, monkeypatch, capsys
):
    projects = tmp_path / "projects"
    (projects / "slug").mkdir(parents=True)
    transcript = write(projects / "slug" / "s.jsonl", STREAMED_TOOLS)
    metrics = tmp_path / "sessions.jsonl"
    rows = [
        row("s", "2026-08-01T10:00:05Z", 100, tool_call_count=2, transcript_path=transcript),
        row("s", "2026-08-01T10:00:06Z", 150, tool_call_count=2, transcript_path=transcript),
    ]
    metrics.write_text("".join(json.dumps(item) + "\n" for item in rows))
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill-token-dedup.py",
            "--metrics-file",
            str(metrics),
            "--projects-dir",
            str(projects),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--state-file",
            str(tmp_path / "state.json"),
            "--cutoff",
            "2026-09-01",
            "--fields",
            "tool_calls",
            "--apply",
        ],
    )

    assert script.main() == 0

    out = capsys.readouterr().out
    assert (
        "0 every-entry row(s) only fell, 0 first-entry row(s) only rose, "
        "1 earlier time-cut row(s) moved to their file cut"
    ) in out
    written = [json.loads(line) for line in metrics.read_text().splitlines()]
    assert [item["tool_call_count"] for item in written] == [1, 2]

    assert script.main() == 0
    assert "nothing to correct" in capsys.readouterr().out
