"""Parsing a transcript region into per-call rows (backlog 2026-10-07 items 1, 3)."""

import json
from pathlib import Path

from calls_fixtures import assistant_entry, limit_entry, write_transcript
from metrics_core import calls_store


def test_usage_fields_splits_the_cache_tiers() -> None:
    usage = assistant_entry("m1", cache_1h=70, cache_5m=30)["message"]["usage"]

    fields = calls_store.usage_fields(usage)

    assert fields == {"input": 1, "output": 10, "cache_read": 50, "cache_5m": 30, "cache_1h": 70}


def test_usage_fields_counts_an_unsplit_creation_as_the_five_minute_tier() -> None:
    usage = {"cache_creation_input_tokens": 80}

    assert calls_store.usage_fields(usage)["cache_5m"] == 80
    assert calls_store.usage_fields(usage)["cache_1h"] == 0


def test_usage_fields_turns_malformed_counts_into_zero() -> None:
    usage = {
        "input_tokens": "9",
        "output_tokens": -4,
        "cache_read_input_tokens": True,
        "cache_creation": None,
    }

    assert calls_store.usage_fields(usage) == dict.fromkeys(calls_store.USAGE_FIELDS, 0)


def test_call_row_carries_identity_and_usage() -> None:
    row = calls_store.call_row(assistant_entry("m1", sidechain=True), "agent-7", 0)

    assert row == {
        "session_id": "session-1",
        "conversation": "agent-7",
        "message_id": "m1",
        "timestamp": "2026-10-01T10:00:00.000Z",
        "model": "claude-sonnet-5-5",
        "sidechain": 1,
        "claude_code_version": "2.1.300",
        "input": 1,
        "output": 10,
        "cache_read": 50,
        "cache_5m": 0,
        "cache_1h": 100,
    }


def test_call_row_omits_keys_it_does_not_know() -> None:
    entry = assistant_entry("m1")
    del entry["version"], entry["sessionId"]

    row = calls_store.call_row(entry, "session-1", 0)

    assert row is not None
    assert "claude_code_version" not in row
    assert "session_id" not in row


def test_call_row_skips_what_is_not_a_billed_call() -> None:
    zero = assistant_entry("m1", input_tokens=0, output_tokens=0, cache_read=0, cache_1h=0)
    no_usage = assistant_entry("m2")
    del no_usage["message"]["usage"]
    user = {"type": "user", "message": {"usage": {"input_tokens": 5}}}
    bad_message = {"type": "assistant", "message": "text"}

    for entry in (zero, no_usage, user, bad_message):
        assert calls_store.call_row(entry, "session-1", 0) is None


def test_call_row_message_id_falls_back_without_ever_colliding() -> None:
    entry = assistant_entry("m1")
    del entry["message"]["id"]
    first = calls_store.call_row(entry, "c", 0)
    assert first is not None and first["message_id"] == "request-m1"
    del entry["requestId"]
    second = calls_store.call_row(entry, "c", 0)
    assert second is not None and second["message_id"] == "uuid-m1"
    del entry["uuid"]
    third = calls_store.call_row(entry, "c", 42)
    assert third is not None and third["message_id"] == "@c:42"


def test_limit_hit_row_maps_the_quota_record() -> None:
    assert calls_store.limit_hit_row(limit_entry()) == {
        "timestamp": "2026-10-07T12:09:17.059Z",
        "rate_limit_type": "seven_day",
        "resets_at": 1791781200,
        "status": "rejected",
        "overage_status": "rejected",
        "overage_disabled_reason": "org_level_disabled_until",
    }


def test_limit_hit_row_keeps_a_hit_without_resets_at_and_ignores_other_entries() -> None:
    row = calls_store.limit_hit_row(limit_entry(resets_at=None))
    assert row is not None
    assert "resets_at" not in row
    assert calls_store.limit_hit_row(assistant_entry("m1")) is None
    assert calls_store.limit_hit_row({"quotaLimits": "rejected"}) is None


def test_read_region_returns_rows_hits_and_the_end_offset(tmp_path: Path) -> None:
    transcript = tmp_path / "session-1.jsonl"
    write_transcript(transcript, [assistant_entry("m1"), limit_entry(), assistant_entry("m2")])

    rows, hits, offset = calls_store.read_region(str(transcript), 0)

    assert [row["message_id"] for row in rows] == ["m1", "m2"]
    assert rows[0]["conversation"] == "session-1"
    assert len(hits) == 1
    assert offset == transcript.stat().st_size


def test_read_region_collapses_content_block_repeats_to_the_peak(tmp_path: Path) -> None:
    transcript = tmp_path / "session-1.jsonl"
    write_transcript(
        transcript,
        [
            assistant_entry("m1", output_tokens=3, cache_1h=100),
            assistant_entry("m1", output_tokens=40, cache_1h=100),
            assistant_entry("m1", output_tokens=12, cache_1h=100),
        ],
    )

    rows, _, _ = calls_store.read_region(str(transcript), 0)

    assert len(rows) == 1
    assert rows[0]["output"] == 40


def test_read_region_leaves_a_partial_last_line_for_the_next_run(tmp_path: Path) -> None:
    transcript = tmp_path / "session-1.jsonl"
    write_transcript(transcript, [assistant_entry("m1")])
    complete_size = transcript.stat().st_size
    with transcript.open("a") as handle:
        handle.write(json.dumps(assistant_entry("m2"))[:40])

    rows, _, offset = calls_store.read_region(str(transcript), 0)

    assert [row["message_id"] for row in rows] == ["m1"]
    assert offset == complete_size


def test_read_region_resumes_from_an_offset(tmp_path: Path) -> None:
    transcript = tmp_path / "session-1.jsonl"
    write_transcript(transcript, [assistant_entry("m1")])
    _, _, first_offset = calls_store.read_region(str(transcript), 0)
    with transcript.open("a") as handle:
        handle.write(json.dumps(assistant_entry("m2")) + "\n")

    rows, _, offset = calls_store.read_region(str(transcript), first_offset)

    assert [row["message_id"] for row in rows] == ["m2"]
    assert offset == transcript.stat().st_size


def test_read_region_restarts_when_the_file_is_smaller_than_the_offset(tmp_path: Path) -> None:
    transcript = tmp_path / "session-1.jsonl"
    write_transcript(transcript, [assistant_entry("m1")])

    rows, _, _ = calls_store.read_region(str(transcript), 10**9)

    assert [row["message_id"] for row in rows] == ["m1"]


def test_read_region_skips_blank_malformed_and_non_object_lines(tmp_path: Path) -> None:
    transcript = tmp_path / "session-1.jsonl"
    transcript.write_text(
        "\n"
        '{"usage": broken\n'
        '["usage"]\n'
        + json.dumps(assistant_entry("m1"))
        + "\n"
        + json.dumps({"type": "user", "text": "no markers here"})
        + "\n"
    )

    rows, hits, _ = calls_store.read_region(str(transcript), 0)

    assert [row["message_id"] for row in rows] == ["m1"]
    assert hits == []
