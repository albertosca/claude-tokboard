"""Guards the whole CLASS of defect behind the 2026-09-23 incident: a test
that calls into metrics_core/dashboard_tui/dashboard_html without isolating
HOME (or the module-level file constants directly) reads and, worse, WRITES
the author's real ~/.claude/metrics - real, irreplaceable session data and
label caches other live Claude sessions are appending to right now.

Concretely, two tests in tests/test_tui_unbound.py
(test_m_toggles_project_ranking_expanded_state,
test_second_g_switches_back_to_sessions_and_refocuses_the_tree) called
dashboard_tui.load_data() with no isolation at all. That was pre-existing
and harmless while save_label_cache did a plain overwrite; Task 5's
migration-plus-atomic-save made it damaging: the real task_labels.json/
agent_labels.json were rewritten in the new {id: {lang: label}} shape, with
a one-time task_labels.json.pre-i18n/agent_labels.json.pre-i18n backup, and
thousands of freshly regenerated "en": "__no_label__" entries. No labels
were lost and no model call happened (--label-budget defaults to 0), but
the files were touched and had to be restored from the pre-i18n backups by
hand.

This file is the guard for that whole class - not a fix for the two
specific tests (see tests/test_tui_unbound.py for that) and not the fix
itself (see tests/conftest.py's autouse _isolate_from_real_metrics_by_default
fixture) - it is what proves the fix actually applies to every test by
default, present and future.

Reading these six module attributes is the ENTIRE test - it never touches
the filesystem, so running this file alone is always safe, red or green.
"""

from pathlib import Path

import metrics_core as mr
import metrics_core.backfill as backfill_module
import metrics_core.labels as labels_module
import metrics_core.paths as paths_module

# Captured once, at collection time - before any fixture in this session
# (this file's own included) gets a chance to monkeypatch HOME or any of
# the six constants below. Path.home() reads $HOME at CALL time, so this
# has to run before anything rebinds it; module-level code in a test file
# runs at collection, which is as early as pytest offers.
_REAL_METRICS_DIR = (Path.home() / ".claude" / "metrics").resolve()


def _watched_constants() -> dict[str, str]:
    """The exact six file-path constants the 2026-09-23 incident named:
    read from the module OBJECTS the production code actually calls at
    runtime (mr.METRICS_FILE etc.), not copied/re-imported values - a test
    that patched an unrelated name would not fool this."""
    return {
        "metrics_core.METRICS_FILE": mr.METRICS_FILE,
        "metrics_core.SUBAGENTS_FILE": mr.SUBAGENTS_FILE,
        "metrics_core.labels.TASK_LABELS_FILE": labels_module.TASK_LABELS_FILE,
        "metrics_core.labels.AGENT_LABELS_FILE": labels_module.AGENT_LABELS_FILE,
        "metrics_core.backfill.BACKFILL_STATE_FILE": backfill_module.BACKFILL_STATE_FILE,
        "metrics_core.paths.CONFIG_FILE": paths_module.CONFIG_FILE,
    }


def test_no_metrics_file_constant_resolves_under_the_real_metrics_directory() -> None:
    offenders = {
        name: value
        for name, value in _watched_constants().items()
        if Path(value).resolve().is_relative_to(_REAL_METRICS_DIR)
    }
    assert not offenders, (
        "these constants still resolve under the real, irreplaceable "
        f"{_REAL_METRICS_DIR} from inside a test: {offenders} - every test "
        "must be isolated from ~/.claude/metrics by default (see "
        "tests/conftest.py's autouse isolation fixture)"
    )
