"""The non-token field passes of the history backfill: tool_call_count
recomputed by the current rule, and incomplete_usage_messages set on rows
written before the hooks recorded it.

Library half (lib/metrics_core/backfill.py) first, then the script half
(scripts/backfill-token-dedup.py --fields). Every file lives under tmp_path.
"""

import importlib.util
import json
import types
from pathlib import Path
from typing import Any

import metrics_core as mr
import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "backfill-token-dedup.py"


def entry(
    stamp: str,
    message_id: str | None,
    tools: tuple[str | None, ...] = (),
    stop: str | None = "end_turn",
    output: int = 1,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": "x"}]
    for tool_id in tools:
        block: dict[str, Any] = {"type": "tool_use", "name": "Read"}
        if tool_id is not None:
            block["id"] = tool_id
        content.append(block)
    message: dict[str, Any] = {
        "model": "model-alpha",
        "stop_reason": stop,
        "usage": {"input_tokens": 1, "output_tokens": output},
        "content": content,
    }
    if message_id is not None:
        message["id"] = message_id
    return {"type": "assistant", "timestamp": stamp, "message": message}


def write(path: Path, entries: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item) + "\n" for item in entries))
    return str(path)


# A streamed message m1: its tool_use blocks arrive in LATER entries of the
# id (the first entry has none), then the whole message is replayed once.
STREAMED = [
    entry("2026-08-01T10:00:00Z", "m1", (), stop=None),
    entry("2026-08-01T10:00:01Z", "m1", ("t1",), stop=None),
    entry("2026-08-01T10:00:02Z", "m1", ("t2",)),
    entry("2026-08-01T10:00:03Z", "m2", ("t3",)),
    # a replay of m1's tool-carrying entries, at their original timestamps
    entry("2026-08-01T10:00:01Z", "m1", ("t1",), stop=None),
    entry("2026-08-01T10:00:02Z", "m1", ("t2",)),
]


# --- tool_calls_as_of ------------------------------------------------------


def test_tool_calls_as_of_reconstructs_all_three_formulas(tmp_path):
    facts = mr.scan_entry_facts(write(tmp_path / "t.jsonl", STREAMED))

    # distinct ids t1 t2 t3; first entry of each id: m1's (none) + m2's (t3);
    # every block of every entry: 5.
    assert mr.tool_calls_as_of(facts, None) == (3, 1, 5)
    # As of m1's second entry: only t1 exists yet.
    assert mr.tool_calls_as_of(facts, "2026-08-01T10:00:01Z") == (1, 0, 2)


def test_tool_calls_as_of_counts_an_id_less_block_once_per_message(tmp_path):
    # The hook's rule: a block with no id cannot be matched, so it counts on
    # its message's first entry only - in every formula but raw.
    facts = mr.scan_entry_facts(
        write(
            tmp_path / "t.jsonl",
            [
                entry("2026-08-01T10:00:00Z", "m1", (None,)),
                entry("2026-08-01T10:00:00Z", "m1", (None,)),
            ],
        )
    )
    assert mr.tool_calls_as_of(facts, None) == (1, 1, 2)


def test_tool_calls_as_of_agrees_with_the_hook_over_the_whole_file(tmp_path):
    path = write(
        tmp_path / "t.jsonl", [*STREAMED, entry("2026-08-01T10:00:04Z", None, ("t9", None))]
    )
    distinct, _first, _raw = mr.tool_calls_as_of(mr.scan_entry_facts(path), None)
    assert distinct == mr.parse_transcript(path)["tool_call_count"]


def test_scan_entry_facts_skips_what_the_hook_skips(tmp_path):
    # Blank and malformed lines, non-assistant entries, and a content that is
    # not a list are all skipped by parse_transcript too; an entry without an
    # id is kept (it counts), and never takes part in finalisation.
    path = tmp_path / "t.jsonl"
    no_id = entry("2026-08-01T10:00:02Z", None, ("t2",), stop=None)
    odd_content = {
        "type": "assistant",
        "timestamp": "2026-08-01T10:00:03Z",
        "message": {"id": "m9", "content": "text", "stop_reason": "end_turn"},
    }
    lines = [
        "",
        "{not json",
        json.dumps({"type": "user", "timestamp": "2026-08-01T10:00:00Z"}),
        json.dumps(entry("2026-08-01T10:00:01Z", "m1", ("t1",), stop=None)),
        json.dumps(no_id),
        json.dumps(odd_content),
    ]
    path.write_text("\n".join(lines) + "\n")

    facts = mr.scan_entry_facts(str(path))

    assert [fact.message_id for fact in facts] == ["m1", None, "m9"]
    assert facts[2].tool_ids == ()
    assert mr.tool_calls_as_of(facts, None) == (2, 2, 2)
    # m1 was never finalised and m9 came after it; the id-less entry is not
    # a message that could be.
    assert mr.incomplete_as_of(facts, None) == 1
    assert (
        mr.incomplete_as_of(facts, None)
        == mr.parse_transcript(str(path))["incomplete_usage_messages"]
    )


# --- incomplete_as_of ------------------------------------------------------


def test_incomplete_as_of_agrees_with_the_hook_and_ignores_the_newest_message(tmp_path):
    entries = [
        entry("2026-08-01T10:00:00Z", "m1", stop=None),
        entry("2026-08-01T10:00:01Z", "m2", stop=None),
        entry("2026-08-01T10:00:02Z", "m3"),
        entry("2026-08-01T10:00:03Z", "m4", stop=None),
    ]
    path = write(tmp_path / "t.jsonl", entries)
    facts = mr.scan_entry_facts(path)

    assert (
        mr.incomplete_as_of(facts, None) == mr.parse_transcript(path)["incomplete_usage_messages"]
    )
    assert mr.incomplete_as_of(facts, None) == 2
    # As of m2's entry, m2 is the newest message and may still be streaming.
    assert mr.incomplete_as_of(facts, "2026-08-01T10:00:01Z") == 1


# --- plan_tool_call_corrections --------------------------------------------


def session_row(session_id: str, end: str | None, tools: int, **extra: Any) -> dict[str, Any]:
    row = {
        "session_id": session_id,
        "timestamp_end": end,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "tool_call_count": tools,
    }
    row.update(extra)
    return row


def test_tool_call_plan_raises_first_sums_lowers_raw_sums_and_keeps_correct_rows(tmp_path):
    transcripts = {
        "first": write(tmp_path / "first.jsonl", STREAMED),
        "raw": write(tmp_path / "raw.jsonl", STREAMED),
        "ok": write(tmp_path / "ok.jsonl", STREAMED),
    }
    records = [
        session_row("first", "2026-08-01T10:00:01Z", 0),  # first-sum as of then
        session_row("first", "2026-08-01T10:00:09Z", 1),
        session_row("raw", "2026-08-01T10:00:09Z", 5),
        session_row("ok", "2026-08-01T10:00:09Z", 3),
    ]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01")

    assert plan.values == {0: 1, 1: 3, 2: 3}
    assert plan.formulas == {0: mr.FIRST, 1: mr.FIRST, 2: mr.RAW}
    assert plan.stats["untouched: already correct"] == 1
    assert plan.stats["corrected: stored the first-entry count"] == 2
    assert plan.stats["corrected: stored the every-entry count"] == 1


def test_tool_call_plan_refuses_a_unit_with_a_row_matching_no_formula(tmp_path):
    transcripts = {"s": write(tmp_path / "s.jsonl", STREAMED)}
    records = [
        session_row("s", "2026-08-01T10:00:09Z", 1),
        session_row("s", "2026-08-01T10:00:10Z", 4),  # 3, 1 and 5 are the candidates
    ]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats["untouched: session has a row matching no formula"] == 2


def test_tool_call_plan_leaves_units_without_a_transcript_or_past_the_cutoff(tmp_path):
    transcripts = {"late": write(tmp_path / "late.jsonl", STREAMED)}
    records = [
        session_row("gone", "2026-08-01T10:00:09Z", 1),
        session_row("late", "2026-08-01T10:00:09Z", 1),
        session_row("late", "2026-09-02T10:00:00Z", 1),
        session_row("late", None, 1),
    ]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats["untouched: no transcript on disk"] == 1
    assert plan.stats["untouched: session spans the cutoff"] == 3


def test_tool_call_plan_names_subagent_units_by_agent(tmp_path):
    transcripts = {"a1": write(tmp_path / "a1.jsonl", STREAMED)}
    records = [dict(session_row("parent", "2026-08-01T10:00:09Z", 4), agent_id="a1")]

    plan = mr.plan_tool_call_corrections(records, transcripts, "2026-09-01", "agent_id")

    assert plan.stats["untouched: agent has a row matching no formula"] == 1


# --- plan_incomplete_usage ---------------------------------------------------


UNFINISHED = [
    entry("2026-08-01T10:00:00Z", "m1", stop=None, output=2),
    entry("2026-08-01T10:00:01Z", "m2", stop=None, output=3),
    entry("2026-08-01T10:00:02Z", "m3", output=5),
]


def tokens_row(session_id: str, end: str, output: int, **extra: Any) -> dict[str, Any]:
    # input 1 per message; output at each message's peak
    count = {2: 1, 5: 2, 10: 3}[output]
    return session_row(session_id, end, 0, input_tokens=count, output_tokens=output, **extra)


def test_incomplete_plan_adds_the_key_only_where_the_count_is_positive(tmp_path):
    transcripts = {"s": write(tmp_path / "s.jsonl", UNFINISHED)}
    records = [
        tokens_row("s", "2026-08-01T10:00:00Z", 2),  # m1 newest: 0
        tokens_row("s", "2026-08-01T10:00:01Z", 5),  # m1 over: 1
        tokens_row("s", "2026-08-01T10:00:02Z", 10),  # m1, m2 over: 2
    ]

    plan = mr.plan_incomplete_usage(records, transcripts, "2026-09-01")

    assert plan.values == {1: 1, 2: 2}
    assert plan.stats["flagged: messages without a final usage entry"] == 2
    assert plan.stats["untouched: already correct"] == 1


def test_incomplete_plan_requires_the_transcript_to_reproduce_the_rows_tokens(tmp_path):
    # The as-of reconstruction is only trusted where it reproduces what the
    # row stored: a row whose tokens it cannot reproduce may have been
    # parsed from a different state of the file.
    transcripts = {"s": write(tmp_path / "s.jsonl", UNFINISHED)}
    records = [
        tokens_row("s", "2026-08-01T10:00:01Z", 5),
        dict(tokens_row("s", "2026-08-01T10:00:02Z", 10), output_tokens=11),
    ]

    plan = mr.plan_incomplete_usage(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert (
        plan.stats["untouched: session has a row whose tokens the transcript does not reproduce"]
        == 2
    )


def test_incomplete_plan_trusts_a_recorded_count_only_when_it_agrees(tmp_path):
    transcripts = {
        "agrees": write(tmp_path / "a.jsonl", UNFINISHED),
        "differs": write(tmp_path / "d.jsonl", UNFINISHED),
    }
    records = [
        tokens_row("agrees", "2026-08-01T10:00:02Z", 10, incomplete_usage_messages=2),
        tokens_row("differs", "2026-08-01T10:00:01Z", 5),
        tokens_row("differs", "2026-08-01T10:00:02Z", 10, incomplete_usage_messages=1),
    ]

    plan = mr.plan_incomplete_usage(records, transcripts, "2026-09-01")

    assert plan.values == {}
    # No zero-count "flagged" line in the report of a unit with nothing to flag.
    assert "flagged: messages without a final usage entry" not in plan.stats
    assert plan.stats["untouched: already correct"] == 1
    assert (
        plan.stats["untouched: session has a recorded count the transcript does not reproduce"] == 2
    )


def test_incomplete_plan_leaves_units_without_a_transcript_or_past_the_cutoff(tmp_path):
    transcripts = {"late": write(tmp_path / "late.jsonl", UNFINISHED)}
    records = [
        tokens_row("gone", "2026-08-01T10:00:02Z", 10),
        tokens_row("late", "2026-09-02T10:00:02Z", 10),
    ]

    plan = mr.plan_incomplete_usage(records, transcripts, "2026-09-01")

    assert plan.values == {}
    assert plan.stats["untouched: no transcript on disk"] == 1
    assert plan.stats["untouched: session spans the cutoff"] == 1


# --- apply_field_plan / write_field_state ----------------------------------


def test_apply_field_plan_replaces_in_place_or_appends_and_copies_only_planned_rows():
    records = [session_row("a", "x", 1), session_row("b", "x", 2)]

    out = mr.apply_field_plan(records, "tool_call_count", {0: 7})
    assert out[0]["tool_call_count"] == 7
    assert list(out[0]) == list(records[0])  # same key position
    assert out[1] is records[1]
    assert records[0]["tool_call_count"] == 1  # input untouched

    flagged = mr.apply_field_plan(records, "incomplete_usage_messages", {1: 3})
    assert list(flagged[1])[-1] == "incomplete_usage_messages"
    assert flagged[1]["incomplete_usage_messages"] == 3


def identified_rows(*ends: str) -> list[dict[str, Any]]:
    return [{"session_id": "s", "timestamp_end": end} for end in ends]


def test_row_identities_name_a_row_by_unit_moment_and_ordinal_not_by_its_values():
    records = [
        {"session_id": "s", "timestamp_end": "e1", "tool_call_count": 1},
        {"session_id": "s", "timestamp_end": "e1", "tool_call_count": 1},  # same moment
        {"session_id": "other", "timestamp_end": "e1", "tool_call_count": 1},
    ]

    identities = mr.row_identities(records, [0, 1, 2], "session_id")

    assert len(set(identities)) == 3
    # A corrected value, or rows appended later, leave a row's identity alone.
    corrected = [dict(records[0], tool_call_count=9), *records[1:], *identified_rows("e0")]
    assert mr.row_identities(corrected, [0, 1, 2], "session_id") == identities
    # Subagent rows are named by their own agent_id, not the parent session.
    agents = [
        {"session_id": "s", "agent_id": "a1", "timestamp_end": "e1"},
        {"session_id": "s", "agent_id": "a2", "timestamp_end": "e1"},
    ]
    assert len(set(mr.row_identities(agents, [0, 1], "agent_id"))) == 2


def test_write_field_state_counts_a_row_two_runs_corrected_once(tmp_path):
    path = str(tmp_path / "state.json")
    mr.write_backfill_state(
        "2026-09-23", "2026-09-23T12:00:00", ["t1", "t2", "t3", "t4", "t5"], path=path
    )
    records = identified_rows("e1", "e2", "e3")
    first_run = mr.row_identities(records, [0, 1], "session_id")
    second_run = mr.row_identities(records, [1, 2], "session_id")

    mr.write_field_state(
        "sessions", "tool_calls", "2026-09-28", "2026-09-28T10:00:00", first_run, path=path
    )
    mr.write_field_state(
        "sessions", "tool_calls", "2026-09-29", "2026-09-29T10:00:00", second_run, path=path
    )
    mr.write_field_state(
        "subagents", "tool_calls", "2026-09-28", "2026-09-28T11:00:00", first_run, path=path
    )

    state = json.loads(Path(path).read_text())
    assert state["corrected_rows"] == 5  # the token entry is untouched
    assert state["fields"]["sessions.tool_calls"] == {
        "cutoff": "2026-09-29",
        "applied_at": "2026-09-29T10:00:00",
        "corrected_rows": 3,  # every run's, row e2 once though both corrected it
        "rows_changed_this_run": 2,
        "corrected_row_ids": sorted(mr.row_identities(records, [0, 1, 2], "session_id")),
    }
    assert state["fields"]["subagents.tool_calls"]["corrected_rows"] == 2
    # The dashboards' note still reads the token entry only.
    assert mr.describe_backfill_state(state, lang="en") == mr.describe_backfill_state(
        mr.load_backfill_state(path), lang="en"
    )


def test_write_field_state_over_an_old_state_keeps_adding_to_its_count(tmp_path):
    # The shape every state written before row identities has: one count
    # that added each run's rows, so a row corrected twice counted twice.
    path = tmp_path / "state.json"
    old = {
        "cutoff": "2026-09-23",
        "applied_at": "2026-09-23T21:38:39",
        "corrected_rows": 2541,
        "fields": {
            "sessions.tool_calls": {
                "cutoff": "2026-09-28",
                "applied_at": "2026-09-29T10:58:52",
                "corrected_rows": 2000,
            }
        },
    }
    path.write_text(json.dumps(old))
    note = mr.describe_backfill_state(mr.load_backfill_state(str(path)), lang="en")
    records = identified_rows("e1", "e2")

    for index, applied_at in ((0, "t1"), (1, "t2")):
        state = mr.write_field_state(
            "sessions",
            "tool_calls",
            "c",
            applied_at,
            mr.row_identities(records, [index], "session_id"),
            path=str(path),
        )

    entry = state["fields"]["sessions.tool_calls"]
    # The earlier rows have no identities, so whether a later run touched
    # one of them again cannot be told: the count adds, as it always did,
    # and says how much of it is unidentified.
    assert entry["corrected_rows"] == 2002
    assert entry["rows_changed_this_run"] == 1
    assert entry["unidentified_earlier_rows"] == 2000
    assert entry["corrected_row_ids"] == sorted(mr.row_identities(records, [0, 1], "session_id"))
    assert mr.describe_backfill_state(state, lang="en") == note


@pytest.mark.parametrize("earlier", [{"corrected_rows": 0}, {}], ids=["zero", "missing"])
def test_write_field_state_over_an_old_entry_that_corrected_nothing_knows_the_total(
    tmp_path, earlier
):
    path = tmp_path / "state.json"
    old_entry = {"cutoff": "c", "applied_at": "t0", **earlier}
    path.write_text(json.dumps({"fields": {"sessions.tool_calls": old_entry}}))
    identities = mr.row_identities(identified_rows("e1"), [0], "session_id")

    state = mr.write_field_state("sessions", "tool_calls", "c", "t", identities, path=str(path))

    entry = state["fields"]["sessions.tool_calls"]
    assert "unidentified_earlier_rows" not in entry
    assert entry["corrected_rows"] == 1


def test_write_field_state_replaces_a_fields_entry_that_is_not_a_dict(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"fields": "junk"}))
    identities = mr.row_identities(identified_rows("e1"), [0], "session_id")

    state = mr.write_field_state("sessions", "tool_calls", "c", "t", identities, path=str(path))

    assert state["fields"] == {
        "sessions.tool_calls": {
            "cutoff": "c",
            "applied_at": "t",
            "corrected_rows": 1,
            "rows_changed_this_run": 1,
            "corrected_row_ids": identities,
        }
    }


# --- a hand-edited state never breaks a write ---------------------------------
# write_field_state runs AFTER the metrics file was replaced, so a state it
# cannot read must not raise: that turned a successful apply into a
# traceback. Values it cannot use are recorded as unknown (null) and the
# rest is kept.


@pytest.mark.parametrize("count", ["2000", 3.5, True, -1, [1]], ids=repr)
def test_write_field_state_records_an_unusable_old_count_as_unknown(tmp_path, count):
    path = tmp_path / "state.json"
    other = {"cutoff": "c", "applied_at": "t0", "corrected_rows": 9}
    path.write_text(
        json.dumps(
            {
                "fields": {
                    "sessions.tool_calls": {
                        "cutoff": "c",
                        "applied_at": "t0",
                        "corrected_rows": count,
                    },
                    "subagents.tool_calls": other,
                }
            }
        )
    )
    identities = mr.row_identities(identified_rows("e1"), [0], "session_id")

    state = mr.write_field_state("sessions", "tool_calls", "c", "t", identities, path=str(path))

    entry = state["fields"]["sessions.tool_calls"]
    assert entry["corrected_rows"] is None
    assert entry["unidentified_earlier_rows"] is None
    assert entry["rows_changed_this_run"] == 1
    assert entry["corrected_row_ids"] == identities
    assert state["fields"]["subagents.tool_calls"] == other


def test_write_field_state_keeps_the_usable_names_of_a_mixed_list(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "fields": {
                    "sessions.tool_calls": {
                        "corrected_rows": 3,
                        "corrected_row_ids": [1, "abc", None],
                    }
                }
            }
        )
    )

    state = mr.write_field_state("sessions", "tool_calls", "c", "t", ["def"], path=str(path))

    entry = state["fields"]["sessions.tool_calls"]
    # The two entries that are not names are rows nobody can name: they
    # are carried as unidentified, like an old-shape count.
    assert entry["corrected_row_ids"] == ["abc", "def"]
    assert entry["unidentified_earlier_rows"] == 2
    assert entry["corrected_rows"] == 4


@pytest.mark.parametrize("carried", [None, "x", -3], ids=repr)
def test_an_unknown_count_stays_unknown_on_later_runs(tmp_path, carried):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "fields": {
                    "sessions.tool_calls": {
                        "corrected_rows": None,
                        "corrected_row_ids": ["abc"],
                        "unidentified_earlier_rows": carried,
                    }
                }
            }
        )
    )

    state = mr.write_field_state("sessions", "tool_calls", "c", "t", ["def"], path=str(path))

    entry = state["fields"]["sessions.tool_calls"]
    assert entry["corrected_rows"] is None
    assert entry["unidentified_earlier_rows"] is None
    assert entry["corrected_row_ids"] == ["abc", "def"]


def test_the_token_entry_tolerates_an_unusable_old_count_too(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"cutoff": "c", "applied_at": "t0", "corrected_rows": "many"}))
    state = mr.write_backfill_state("c", "2026-09-29T10:00", ["e1"], path=str(path))
    assert state["corrected_rows"] is None
    assert state["rows_changed_this_run"] == 1


# --- the script: --fields ----------------------------------------------------


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("backfill_token_dedup_fields", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path: Path) -> dict[str, Path]:
    projects = tmp_path / "projects"
    write(projects / "slug" / "s1.jsonl", STREAMED)
    write(projects / "slug" / "s2.jsonl", UNFINISHED)
    metrics = tmp_path / "sessions.jsonl"
    rows = [
        # s1: tokens are already correct (3 messages... see STREAMED: m1, m2)
        session_row(
            "s1",
            "2026-08-01T10:00:09Z",
            1,
            input_tokens=2,
            output_tokens=2,
            transcript_path=str(projects / "slug" / "s1.jsonl"),
        ),
        tokens_row(
            "s2", "2026-08-01T10:00:02Z", 10, transcript_path=str(projects / "slug" / "s2.jsonl")
        ),
    ]
    metrics.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return {
        "projects": projects,
        "metrics": metrics,
        "state": tmp_path / "state.json",
        "backups": tmp_path / "backups",
    }


def argv(paths: dict[str, Path], *extra: str) -> list[str]:
    return [
        "backfill-token-dedup.py",
        "--metrics-file",
        str(paths["metrics"]),
        "--projects-dir",
        str(paths["projects"]),
        "--backup-dir",
        str(paths["backups"]),
        "--state-file",
        str(paths["state"]),
        "--cutoff",
        "2026-09-01",
        *extra,
    ]


def test_tool_calls_dry_run_reports_and_writes_nothing(tmp_path, script, monkeypatch, capsys):
    paths = fixture(tmp_path)
    before = paths["metrics"].read_bytes()
    monkeypatch.setattr("sys.argv", argv(paths, "--fields", "tool_calls"))

    assert script.main() == 0

    out = capsys.readouterr().out
    assert "Lines: 2 read, 1 would change" in out
    assert "tool_call_count total: before 1  after 3" in out
    assert (
        "only tool_call_count changed; 0 every-entry row(s) only fell, "
        "1 first-entry row(s) only rose"
    ) in out
    assert "DRY RUN" in out
    assert paths["metrics"].read_bytes() == before
    assert not paths["backups"].exists()
    assert not paths["state"].exists()


def test_tool_calls_apply_backs_up_rewrites_one_field_records_state_and_is_idempotent(
    tmp_path, script, monkeypatch, capsys
):
    paths = fixture(tmp_path)
    before = paths["metrics"].read_bytes()
    monkeypatch.setattr("sys.argv", argv(paths, "--fields", "tool_calls", "--apply"))

    assert script.main() == 0

    (backup,) = paths["backups"].glob("sessions.jsonl.backfill-*.bak")
    assert backup.read_bytes() == before
    old = [json.loads(line) for line in before.decode().splitlines()]
    new = [json.loads(line) for line in paths["metrics"].read_text().splitlines()]
    assert new[0] == dict(old[0], tool_call_count=3)
    assert new[1] == old[1]
    state = json.loads(paths["state"].read_text())
    assert state["fields"]["sessions.tool_calls"]["corrected_rows"] == 1
    assert state["fields"]["sessions.tool_calls"]["corrected_row_ids"] == mr.row_identities(
        old, [0], "session_id"
    )
    assert "corrected_rows" not in state  # the token entry was never written

    capsys.readouterr()
    after_first = paths["metrics"].read_bytes()
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "nothing to correct" in out
    assert paths["metrics"].read_bytes() == after_first
    assert len(list(paths["backups"].glob("*.bak"))) == 1


def test_incomplete_usage_apply_adds_only_the_new_key(tmp_path, script, monkeypatch, capsys):
    paths = fixture(tmp_path)
    before = paths["metrics"].read_bytes()
    monkeypatch.setattr("sys.argv", argv(paths, "--fields", "incomplete_usage", "--apply"))

    assert script.main() == 0

    out = capsys.readouterr().out
    assert "only incomplete_usage_messages was added" in out
    old = [json.loads(line) for line in before.decode().splitlines()]
    new = [json.loads(line) for line in paths["metrics"].read_text().splitlines()]
    assert new[0] == old[0]
    assert new[1] == dict(old[1], incomplete_usage_messages=2)
    state = json.loads(paths["state"].read_text())
    assert state["fields"]["sessions.incomplete_usage"]["corrected_rows"] == 1


def test_a_field_pass_refuses_a_plan_that_would_touch_anything_else(
    tmp_path, script, monkeypatch, capsys
):
    paths = fixture(tmp_path)
    before = paths["metrics"].read_bytes()
    real = script.apply_field_plan

    def also_tokens(records, field, values):
        out = real(records, field, values)
        for index in values:
            out[index]["output_tokens"] -= 1
            out[index]["tag"] = "x"
        return out

    monkeypatch.setattr(script, "apply_field_plan", also_tokens)
    monkeypatch.setattr("sys.argv", argv(paths, "--fields", "tool_calls", "--apply"))

    assert script.main() == 1

    error_output = capsys.readouterr().err
    assert "line 1: output_tokens changed 2 -> 1" in error_output
    assert "line 1: key set changed" in error_output or "line 1: tag" in error_output
    assert paths["metrics"].read_bytes() == before


def test_a_tool_call_pass_refuses_a_row_that_moved_against_its_formula(
    tmp_path, script, monkeypatch, capsys
):
    paths = fixture(tmp_path)
    real = script.plan_tool_call_corrections

    def backwards(*arguments, **keyword_arguments):
        plan = real(*arguments, **keyword_arguments)
        return plan._replace(values=dict.fromkeys(plan.values, 0))

    monkeypatch.setattr(script, "plan_tool_call_corrections", backwards)
    monkeypatch.setattr("sys.argv", argv(paths, "--fields", "tool_calls"))

    assert script.main() == 1
    assert "line 1: tool_call_count fell 1 -> 0 but was recognised as the first-entry count" in (
        capsys.readouterr().err
    )


def test_a_tool_call_pass_refuses_a_raw_row_that_rose(tmp_path, script, monkeypatch, capsys):
    paths = fixture(tmp_path)
    real = script.plan_tool_call_corrections

    def rising_raw(*arguments, **keyword_arguments):
        plan = real(*arguments, **keyword_arguments)
        return plan._replace(
            values=dict.fromkeys(plan.values, 99),
            formulas=dict.fromkeys(plan.values, mr.RAW),
        )

    monkeypatch.setattr(script, "plan_tool_call_corrections", rising_raw)
    monkeypatch.setattr("sys.argv", argv(paths, "--fields", "tool_calls"))

    assert script.main() == 1
    assert "line 1: tool_call_count rose 1 -> 99 but was recognised as the every-entry count" in (
        capsys.readouterr().err
    )


def test_a_token_run_never_touches_tool_call_count(tmp_path, script, monkeypatch, capsys):
    paths = fixture(tmp_path)
    before = paths["metrics"].read_bytes()
    monkeypatch.setattr("sys.argv", argv(paths, "--apply"))

    assert script.main() == 0
    assert "nothing to correct" in capsys.readouterr().out
    assert paths["metrics"].read_bytes() == before


def test_an_apply_over_a_hand_edited_state_records_it_and_succeeds(
    tmp_path, script, monkeypatch, capsys
):
    paths = fixture(tmp_path)
    paths["state"].write_text(
        json.dumps(
            {
                "fields": {
                    "sessions.tool_calls": {
                        "corrected_rows": "2000",
                        "corrected_row_ids": [7, "abc"],
                    }
                }
            }
        )
    )
    monkeypatch.setattr("sys.argv", argv(paths, "--fields", "tool_calls", "--apply"))

    assert script.main() == 0

    entry = json.loads(paths["state"].read_text())["fields"]["sessions.tool_calls"]
    assert entry["rows_changed_this_run"] == 1
    assert "abc" in entry["corrected_row_ids"]


def test_a_state_write_that_fails_after_the_rewrite_warns_instead_of_raising(
    tmp_path, script, monkeypatch, capsys
):
    # The file is already replaced when the state is written, and the
    # carry-over of lines a live hook appended in that instant runs after
    # it: a raise there would both hide a finished apply behind a traceback
    # and skip the carry-over.
    paths = fixture(tmp_path)
    before = paths["metrics"].read_bytes()

    def fail(*arguments: Any, **keywords: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(script, "write_field_state", fail)
    carried: list[str] = []
    real_carry = script.carry_late_appends

    def carry(*arguments: Any) -> int:
        carried.append("called")
        return int(real_carry(*arguments))

    monkeypatch.setattr(script, "carry_late_appends", carry)
    monkeypatch.setattr("sys.argv", argv(paths, "--fields", "tool_calls", "--apply"))

    assert script.main() == 0

    assert paths["metrics"].read_bytes() != before
    assert carried == ["called"]
    warning = capsys.readouterr().err
    assert "the backfill state could not be recorded" in warning
    assert "disk full" in warning
