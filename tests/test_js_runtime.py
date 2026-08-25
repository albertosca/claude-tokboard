"""The guard around the JS tests is itself a place a false green can hide:
a silent skip when node is missing reports success for a suite that never
ran. This pins the guard's behaviour in both directions."""

import pytest
from js_runtime import NODE_SKIP_ENV, require_node_with_jsdom


def test_opting_out_explicitly_skips(monkeypatch):
    monkeypatch.setenv(NODE_SKIP_ENV, "1")
    with pytest.raises(BaseException) as excinfo:
        require_node_with_jsdom()
    assert (
        "Skipped" in type(excinfo.value).__name__ or excinfo.value.__class__.__name__ == "Skipped"
    )


def test_a_missing_runtime_fails_loudly_rather_than_skipping(monkeypatch):
    # PATH emptied so node cannot be found. Absence must FAIL, because a
    # skip here would be indistinguishable from a passing suite.
    monkeypatch.delenv(NODE_SKIP_ENV, raising=False)
    monkeypatch.setenv("PATH", "")
    with pytest.raises(BaseException) as excinfo:
        require_node_with_jsdom()
    assert excinfo.value.__class__.__name__ == "Failed"
    assert "npm install" in str(excinfo.value)


def test_a_present_runtime_returns_its_path(monkeypatch):
    monkeypatch.delenv(NODE_SKIP_ENV, raising=False)
    node = require_node_with_jsdom()
    assert node and node.endswith("node")
