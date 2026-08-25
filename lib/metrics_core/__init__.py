"""Shared, dependency-free (pure stdlib) data layer for both the HTML
and TUI Claude Code metrics dashboards. Presentation code (dashboard_html.py,
dashboard_tui.py) imports from this package instead of each other."""

from .backfill import (
    BACKFILL_STATE_FILE,
    DEFAULT_CUTOFF,
    TOKEN_FIELDS,
    apply_plan,
    as_of,
    build_prefix_sums,
    daily_totals,
    describe_backfill_state,
    find_nonmonotonic_sessions,
    index_transcripts,
    load_backfill_state,
    plan_corrections,
    scan_transcript,
    sha256_bytes,
    sha256_of,
    token_sum,
    verify_fields,
    write_backfill_state,
)
from .format import format_date, format_duration, format_tokens
from .granularity import GRANULARITY_CONFIG, aggregate_by_day, choose_granularity
from .labels import (
    AGENT_LABELS_FILE,
    TASK_LABELS_FILE,
    LabelBudget,
    cold_summary_source,
    compress_to_label,
    find_away_summary,
    get_agent_label,
    get_task_label,
    group_subagents_by_session,
    load_agent_label_cache,
    load_label_cache,
    save_agent_label_cache,
    save_label_cache,
)
from .periods import DEFAULT_PERIOD_ID, PERIODS, Period, resolve_period
from .projects import (
    assign_group_colors,
    build_project_groups,
    color_group_key,
    compute_project_first_seen,
    compute_project_last_seen,
    compute_project_totals,
    get_open_projects,
    project_days_running,
    project_display_label,
)
from .records import (
    METRICS_FILE,
    SUBAGENTS_FILE,
    compute_token_type_totals,
    dedupe_by_key,
    dedupe_sessions,
    dedupe_subagents,
    expand_records_to_daily_deltas,
    filter_by_days,
    filter_deltas_by_window,
    filter_records,
    filter_sessions_by_window,
    fold_subagent_tokens_into_sessions,
    is_ephemeral_path,
    load_records,
    normalize_project_paths,
    record_token_total,
)
