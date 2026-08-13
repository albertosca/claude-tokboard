"""Shared, dependency-free (pure stdlib) data layer for both the HTML
and TUI Claude Code metrics dashboards. Presentation code (dashboard_html.py,
dashboard_tui.py) imports from this package instead of each other."""
from .format import format_tokens, format_duration, format_date
from .periods import PERIODS, DEFAULT_PERIOD_ID, Period, resolve_period
from .records import (
    METRICS_FILE,
    SUBAGENTS_FILE,
    load_records,
    dedupe_by_key,
    normalize_project_paths,
    is_ephemeral_path,
    dedupe_sessions,
    dedupe_subagents,
    filter_records,
    filter_by_days,
    filter_sessions_by_window,
    filter_deltas_by_window,
    record_token_total,
    compute_token_type_totals,
    expand_records_to_daily_deltas,
    fold_subagent_tokens_into_sessions,
)
from .projects import (
    compute_project_first_seen,
    compute_project_last_seen,
    project_display_label,
    color_group_key,
    compute_project_totals,
    project_days_running,
    get_open_projects,
    assign_group_colors,
    build_project_groups,
)
from .granularity import GRANULARITY_CONFIG, choose_granularity, aggregate_by_day
from .labels import (
    TASK_LABELS_FILE,
    AGENT_LABELS_FILE,
    LabelBudget,
    load_label_cache,
    save_label_cache,
    load_agent_label_cache,
    save_agent_label_cache,
    get_task_label,
    get_agent_label,
    compress_to_label,
    find_away_summary,
    cold_summary_source,
    group_subagents_by_session,
)
