"""Proves the label caches survive a concurrent save while still being written.

dashboard_html.py's main() shares one cache dict between the main thread
(which calls save_*_cache periodically - see its SAVE_EVERY comment - while
labeling is still in flight) and a single ThreadPoolExecutor worker thread
(which writes cache[key] = ... as each get_*_label call resolves). Without
a lock around both the writes and the json.dump, the main thread's dict
iteration can observe the worker thread inserting a new key mid-iteration:

    RuntimeError: dictionary changed size during iteration

Reproduced for real (2026-09-08) running dashboard_html.py --html against a
cache with genuinely new, uncached agent ids to write. This is a stress
test, not a deterministic one - the race depends on thread scheduling - so
it forces many more context switches than default (a tiny
sys.setswitchinterval) and saves continuously in a tight loop for as long
as the writer thread runs, on a cache big enough that json.dump's own
iteration takes measurably long. Confirmed (2026-09-08) this fails
reliably, every run, against the unlocked code, and never fails against
the locked version.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import metrics_core as mr
import metrics_core.labels as labels_module
import pytest

RECORD_COUNT = 20000


def test_saving_the_agent_cache_while_a_worker_thread_still_writes_it_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(labels_module, "AGENT_LABELS_FILE", str(tmp_path / "agent_labels.json"))
    monkeypatch.setattr(labels_module, "compress_to_label", lambda source: "a label")
    monkeypatch.setattr(labels_module, "warm_summary_source", lambda path, max_turns=4: "some text")

    cache: dict[str, str] = {}
    budget = mr.LabelBudget(None)
    records = [
        {"agent_id": f"agent-{i}", "agent_transcript_path": "/some/path.jsonl"}
        for i in range(RECORD_COUNT)
    ]
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer() -> None:
        for record in records:
            try:
                mr.get_agent_label(record, cache, budget)
            except BaseException as exc:
                errors.append(exc)
        stop.set()

    def saver() -> None:
        while not stop.is_set():
            try:
                mr.save_agent_label_cache(cache)
            except BaseException as exc:
                errors.append(exc)
                return

    original_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.0001)
    try:
        writer_thread = threading.Thread(target=writer)
        saver_thread = threading.Thread(target=saver)
        writer_thread.start()
        saver_thread.start()
        writer_thread.join()
        saver_thread.join()
    finally:
        sys.setswitchinterval(original_interval)

    assert not errors, errors
    assert len(cache) == RECORD_COUNT


def test_saving_the_task_cache_while_a_worker_thread_still_writes_it_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(labels_module, "TASK_LABELS_FILE", str(tmp_path / "task_labels.json"))
    monkeypatch.setattr(labels_module, "compress_to_label", lambda source: "a label")
    monkeypatch.setattr(labels_module, "cold_summary_source", lambda path, max_turns=6: "some text")

    cache: dict[str, str] = {}
    budget = mr.LabelBudget(None)
    records = [
        {"session_id": f"session-{i}", "transcript_path": "/some/path.jsonl"}
        for i in range(RECORD_COUNT)
    ]
    errors: list[BaseException] = []
    stop = threading.Event()

    def writer() -> None:
        for record in records:
            try:
                mr.get_task_label(record, cache, budget)
            except BaseException as exc:
                errors.append(exc)
        stop.set()

    def saver() -> None:
        while not stop.is_set():
            try:
                mr.save_label_cache(cache)
            except BaseException as exc:
                errors.append(exc)
                return

    original_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.0001)
    try:
        writer_thread = threading.Thread(target=writer)
        saver_thread = threading.Thread(target=saver)
        writer_thread.start()
        saver_thread.start()
        writer_thread.join()
        saver_thread.join()
    finally:
        sys.setswitchinterval(original_interval)

    assert not errors, errors
    assert len(cache) == RECORD_COUNT
