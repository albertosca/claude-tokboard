"""Writing, cursor and deduplicating readers of the per-call store."""

import json
import multiprocessing
import stat
from pathlib import Path
from typing import Any

from calls_fixtures import assistant_entry, limit_entry, write_transcript
from metrics_core import calls_store


def _row(message_id: str, **overrides: Any) -> dict[str, Any]:
    row = calls_store.call_row(assistant_entry(message_id), "session-1", 0)
    assert row is not None
    row.update(overrides)
    return row


def test_append_rows_creates_a_private_file_and_appends(tmp_path: Path) -> None:
    path = tmp_path / "metrics" / "calls.jsonl"

    calls_store.append_rows(path, [_row("m1")])
    calls_store.append_rows(path, [_row("m2")])
    calls_store.append_rows(path, [])

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [json.loads(line)["message_id"] for line in path.read_text().splitlines()] == [
        "m1",
        "m2",
    ]


def test_append_rows_keeps_every_chunk_on_a_row_boundary(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    rows = [_row(f"m{index}") for index in range(300)]

    calls_store.append_rows(path, rows)

    assert path.stat().st_size > calls_store.APPEND_CHUNK_BYTES * 3
    assert len(path.read_text().splitlines()) == 300


def _write_batch(path: str, prefix: str) -> None:
    rows = [_row(f"{prefix}-{index}") for index in range(400)]
    calls_store.append_rows(Path(path), rows)


def test_concurrent_writers_never_tear_or_lose_a_row(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    workers = [
        multiprocessing.Process(target=_write_batch, args=(str(path), prefix)) for prefix in "abc"
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    lines = path.read_text().splitlines()
    assert len(lines) == 1200
    assert len({json.loads(line)["message_id"] for line in lines}) == 1200


def test_cursor_round_trips_and_forgets_transcripts_that_are_gone(tmp_path: Path) -> None:
    alive = tmp_path / "alive.jsonl"
    alive.write_text("{}\n")

    calls_store.save_cursor(tmp_path, {str(alive): 7, str(tmp_path / "gone.jsonl"): 9})

    assert calls_store.load_cursor(tmp_path) == {str(alive): 7}
    assert stat.S_IMODE((tmp_path / calls_store.CURSOR_FILE_NAME).stat().st_mode) == 0o600


def test_load_cursor_is_lenient(tmp_path: Path) -> None:
    cursor_file = tmp_path / calls_store.CURSOR_FILE_NAME
    assert calls_store.load_cursor(tmp_path) == {}
    cursor_file.write_text("not json")
    assert calls_store.load_cursor(tmp_path) == {}
    cursor_file.write_text("[1, 2]")
    assert calls_store.load_cursor(tmp_path) == {}
    cursor_file.write_text(json.dumps({"a": 3, "b": "x", "c": -1, "d": True, "e": 4.5}))
    assert calls_store.load_cursor(tmp_path) == {"a": 3}


def test_readers_return_empty_for_a_missing_store(tmp_path: Path) -> None:
    assert calls_store.read_calls(tmp_path / "calls.jsonl") == []
    assert calls_store.read_limit_hits(tmp_path / "limit-hits.jsonl") == []


def test_read_calls_counts_a_replayed_message_once(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    original = _row("m1", conversation="session-1", output=10)
    replay = _row("m1", conversation="session-2", output=10)
    calls_store.append_rows(path, [original, replay, _row("m2")])

    rows = calls_store.read_calls(path)

    raw_total = sum(json.loads(line)["cache_1h"] for line in path.read_text().splitlines())
    assert raw_total == 300, "the fixture must really contain a duplicate"
    assert sum(row["cache_1h"] for row in rows) == 200
    assert len(rows) == 2


def test_read_calls_keeps_the_peak_across_regions_and_sorts_by_time(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    calls_store.append_rows(
        path,
        [
            _row("late", timestamp="2026-10-02T00:00:00.000Z"),
            _row("early", timestamp="2026-10-01T00:00:00.000Z", output=5),
            _row("early", timestamp="2026-10-01T00:00:00.000Z", output=40),
        ],
    )

    rows = calls_store.read_calls(path)

    assert [row["message_id"] for row in rows] == ["early", "late"]
    assert rows[0]["output"] == 40


def test_read_calls_skips_rows_it_cannot_place(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    path.write_text(
        "garbage\n"
        + json.dumps({"timestamp": "2026-10-01T00:00:00.000Z"})
        + "\n"
        + json.dumps({"message_id": "m1"})
        + "\n"
        + json.dumps(_row("m2", cache_1h="12"))
        + "\n"
        + "[1]\n"
    )

    rows = calls_store.read_calls(path)

    assert [row["message_id"] for row in rows] == ["m2"]
    assert rows[0]["cache_1h"] == 0


def test_read_limit_hits_dedups_and_sorts(tmp_path: Path) -> None:
    path = tmp_path / "limit-hits.jsonl"
    first = calls_store.limit_hit_row(limit_entry(timestamp="2026-10-07T12:00:00.000Z"))
    later = calls_store.limit_hit_row(limit_entry(timestamp="2026-10-06T12:00:00.000Z"))
    assert first is not None and later is not None
    undated = {"rate_limit_type": "five_hour"}
    calls_store.append_rows(path, [first, first, later, undated])

    hits = calls_store.read_limit_hits(path)

    assert [hit["timestamp"] for hit in hits] == [
        "2026-10-06T12:00:00.000Z",
        "2026-10-07T12:00:00.000Z",
    ]


def test_log_transcript_writes_once_and_then_only_what_is_new(tmp_path: Path) -> None:
    metrics = tmp_path / "metrics"
    transcript = tmp_path / "session-1.jsonl"
    write_transcript(transcript, [assistant_entry("m1"), limit_entry()])

    calls_store.log_transcript(str(transcript), metrics)
    calls_store.log_transcript(str(transcript), metrics)
    assert len((metrics / "calls.jsonl").read_text().splitlines()) == 1
    assert len((metrics / "limit-hits.jsonl").read_text().splitlines()) == 1

    with transcript.open("a") as handle:
        handle.write(json.dumps(assistant_entry("m2")) + "\n")
    calls_store.log_transcript(str(transcript), metrics)

    assert [row["message_id"] for row in calls_store.read_calls(metrics / "calls.jsonl")] == [
        "m1",
        "m2",
    ]
