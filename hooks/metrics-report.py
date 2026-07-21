#!/usr/bin/env python3
import argparse
import glob
import html
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

METRICS_FILE = os.path.expanduser("~/.claude/metrics/sessions.jsonl")
SUBAGENTS_FILE = os.path.expanduser("~/.claude/metrics/subagents.jsonl")
TASK_LABELS_FILE = os.path.expanduser("~/.claude/metrics/task_labels.json")
AGENT_LABELS_FILE = os.path.expanduser("~/.claude/metrics/agent_labels.json")


def load_records(path):
    records = []
    if not os.path.exists(path):
        return records
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def dedupe_by_key(records, key_field):
    # metrics-log.py / subagent-metrics-log.py append a fresh cumulative
    # snapshot on every Stop/SubagentStop event during a session or
    # subagent's life, not just once at the end - a long/busy one can log
    # many rows sharing one id, each with growing
    # duration_seconds/tokens/turn_count. Every downstream consumer here
    # (trend chart day totals, Panorama project totals, the composition
    # legend, the subagent detail table) just sums/lists records, so
    # without this dedup real usage gets counted once per snapshot logged
    # - inflating every total by however many times that id got logged
    # (found 2026-07-21: one session had 33 duplicate rows; 17 subagent
    # ids had up to 4). Keep the snapshot with the largest
    # duration_seconds per id, first-seen order otherwise.
    best_by_id = {}
    order = []
    no_id_records = []
    for r in records:
        key = r.get(key_field)
        if key is None:
            no_id_records.append(r)
            continue
        if key not in best_by_id:
            order.append(key)
            best_by_id[key] = r
        elif (r.get("duration_seconds") or 0) >= (best_by_id[key].get("duration_seconds") or 0):
            best_by_id[key] = r
    return [best_by_id[key] for key in order] + no_id_records


def dedupe_sessions(records):
    return dedupe_by_key(records, "session_id")


def dedupe_subagents(records):
    return dedupe_by_key(records, "agent_id")


def filter_records(records, tag=None, project=None):
    filtered = records
    if tag is not None:
        filtered = [r for r in filtered if r.get("tag") == tag]
    if project is not None:
        filtered = [r for r in filtered if r.get("project_path") == project]
    return filtered


def filter_by_days(records, days):
    if days is None:
        return records
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    filtered = []
    for r in records:
        timestamp_start = r.get("timestamp_start")
        if not timestamp_start:
            continue
        try:
            dt = datetime.fromisoformat(timestamp_start.replace("Z", "+00:00")).astimezone()
        except (ValueError, AttributeError, TypeError):
            continue
        if dt >= cutoff:
            filtered.append(r)
    return filtered


def compute_project_first_seen(records):
    first_seen = {}
    for r in records:
        project = r.get("project_path")
        timestamp_start = r.get("timestamp_start")
        if not project or not timestamp_start:
            continue
        if project not in first_seen or timestamp_start < first_seen[project]:
            first_seen[project] = timestamp_start
    return first_seen


def compute_project_last_seen(records):
    last_seen = {}
    for r in records:
        project = r.get("project_path")
        timestamp_start = r.get("timestamp_start")
        if not project or not timestamp_start:
            continue
        if project not in last_seen or timestamp_start > last_seen[project]:
            last_seen[project] = timestamp_start
    return last_seen


def project_display_label(project_path):
    if not project_path:
        return "sem projeto"
    normalized = project_path.rstrip("/")
    marker = "/.claude/worktrees/"
    if marker in normalized:
        parent_path, _, remainder = normalized.partition(marker)
        worktree_name = remainder.split("/")[0]
        if worktree_name:
            parent_label = os.path.basename(parent_path.rstrip("/")) or parent_path
            return f"{parent_label} / {worktree_name}"
    return os.path.basename(normalized) or normalized


def color_group_key(project_path):
    # A worktree's own color group is its PARENT project (2026-07-21) -
    # they're the same underlying project as far as "what color am I"
    # goes, so the trend chart's legend can show one merged entry for
    # both instead of a separate near-duplicate one per worktree.
    if not project_path:
        return project_path
    normalized = project_path.rstrip("/")
    marker = "/.claude/worktrees/"
    if marker in normalized:
        parent_path, _, _ = normalized.partition(marker)
        return parent_path
    return project_path


def compute_project_totals(records, project_last_seen):
    totals = {}
    for r in records:
        project = r.get("project_path")
        totals[project] = totals.get(project, 0) + (r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0)
    # Sorted by most-recent-activity, not by token volume — answers "what
    # have I touched lately" rather than "what costs the most," and pairs
    # naturally with the top-10-then-collapse cutoff callers apply (the most
    # recently active projects are the ones most likely to matter right now).
    ordered_projects = sorted(totals.keys(), key=lambda p: project_last_seen.get(p, ""), reverse=True)
    max_total = max(totals.values()) if totals else 0
    return ordered_projects, totals, max_total


def project_days_running(project_first_seen, project):
    first_seen = project_first_seen.get(project)
    if not first_seen:
        return ""
    try:
        first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00")).astimezone()
        return f" · há {(datetime.now().astimezone() - first_dt).days} dias"
    except (ValueError, AttributeError, TypeError):
        return ""


def get_open_projects(sessions_dir=None):
    if sessions_dir is None:
        sessions_dir = os.path.expanduser("~/.claude/sessions")
    open_projects = set()
    try:
        paths = glob.glob(os.path.join(sessions_dir, "*.json"))
    except Exception:
        return open_projects
    for path in paths:
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            continue
        cwd = data.get("cwd")
        if cwd:
            open_projects.add(cwd)
    return open_projects


def assign_group_colors(records):
    # Keyed by raw project_path (so every existing `group_colors.get(project)`
    # call site keeps working unchanged) but the SLOT is decided by
    # color_group_key, so a worktree gets the exact same color as its
    # parent project instead of its own slot (2026-07-21).
    assignment = {}
    key_to_color = {}
    next_slot = 0
    for record in records:
        project = record.get("project_path")
        if project in assignment:
            continue
        if project is None:
            assignment[project] = "group-color-none"
            continue
        key = color_group_key(project)
        if key not in key_to_color:
            slot = (next_slot % 30) + 1
            key_to_color[key] = f"group-color-{slot}"
            next_slot += 1
        assignment[project] = key_to_color[key]
    return assignment


def build_project_groups(records, group_colors):
    ordered_desc = list(reversed(records))
    groups = {}
    project_order = []
    for record in ordered_desc:
        project = record.get("project_path")
        if project not in groups:
            groups[project] = []
            project_order.append(project)
        groups[project].append(record)
    result = []
    for project in project_order:
        result.append(
            {
                "project": project,
                "class": group_colors.get(project, "group-color-none"),
                "records": groups[project],
            }
        )
    return result


def format_tokens(n):
    if n is None:
        return "-"
    n = int(n)
    if abs(n) >= 1_000_000_000_000:
        return f"{n / 1_000_000_000_000:.1f}T"
    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_duration(seconds):
    if seconds is None:
        return "-"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.0f}min"
    hours = int(minutes // 60)
    remaining_minutes = int(minutes % 60)
    return f"{hours}h{remaining_minutes:02d}min"


def format_date(timestamp_start):
    if not timestamp_start:
        return "-"
    try:
        dt = datetime.fromisoformat(timestamp_start.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError, TypeError):
        return "-"
    return dt.strftime("%d/%m %H:%M")


def find_away_summary(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    last_content = None
    try:
        with open(transcript_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") == "system" and entry.get("subtype") == "away_summary":
                    last_content = entry.get("content")
    except Exception:
        return None
    return last_content


def cold_summary_source(transcript_path, max_turns=6):
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    texts = []
    try:
        with open(transcript_path, "r") as f:
            for line in f:
                if len(texts) >= max_turns:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") not in ("user", "assistant"):
                    continue
                message = entry.get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            texts.append(block.get("text", ""))
                            break
    except Exception:
        return None
    joined = " ".join(t for t in texts if t).strip()
    return joined or None


def compress_to_label(source_text):
    if not source_text:
        return None
    if not isinstance(source_text, str):
        return None
    prompt = (
        "Resuma o texto abaixo em um rotulo curto de 3 a 5 palavras, "
        "em portugues, sem pontuacao final, responda so com o rotulo:\n\n"
        + source_text[:2000]
    )
    try:
        # `nice` so the labeling subprocess never competes for CPU with
        # interactive work (Vim/tmux/LSPs/the calling session itself) - each
        # call is a full Claude CLI (Node process) boot, not a cheap thread.
        result = subprocess.run(
            ["nice", "-n", "15", "claude", "-p", prompt, "--model", "haiku"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    label = result.stdout.strip().strip('"').strip("'")
    return label or None


def load_label_cache():
    if not os.path.exists(TASK_LABELS_FILE):
        return {}
    try:
        with open(TASK_LABELS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_label_cache(cache):
    os.makedirs(os.path.dirname(TASK_LABELS_FILE), exist_ok=True)
    with open(TASK_LABELS_FILE, "w") as f:
        json.dump(cache, f, indent=2)


_NO_LABEL = "__no_label__"


def get_task_label(record, cache):
    session_id = record.get("session_id")
    if not session_id:
        return None
    if session_id in cache:
        cached = cache[session_id]
        return None if cached == _NO_LABEL else cached

    internal_name = record.get("internal_name")
    if internal_name and len(internal_name.split()) <= 6:
        cache[session_id] = internal_name
        return internal_name

    source = internal_name or find_away_summary(record.get("transcript_path"))
    if not source:
        source = cold_summary_source(record.get("transcript_path"))
    if not source:
        cache[session_id] = _NO_LABEL
        return None

    label = compress_to_label(source)
    cache[session_id] = label or _NO_LABEL
    return label


def load_agent_label_cache():
    if not os.path.exists(AGENT_LABELS_FILE):
        return {}
    try:
        with open(AGENT_LABELS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_agent_label_cache(cache):
    os.makedirs(os.path.dirname(AGENT_LABELS_FILE), exist_ok=True)
    with open(AGENT_LABELS_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def get_agent_label(subagent_record, cache):
    agent_id = subagent_record.get("agent_id")
    if not agent_id:
        return None
    if agent_id in cache:
        cached = cache[agent_id]
        return None if cached == _NO_LABEL else cached

    source = cold_summary_source(subagent_record.get("agent_transcript_path"))
    if not source:
        cache[agent_id] = _NO_LABEL
        return None

    label = compress_to_label(source)
    cache[agent_id] = label or _NO_LABEL
    return label


def group_subagents_by_session(subagent_records):
    grouped = {}
    for record in subagent_records:
        session_id = record.get("session_id")
        if session_id is None:
            continue
        grouped.setdefault(session_id, []).append(record)
    return grouped


def format_table(records):
    header = (
        f"{'session_id':<12} {'tag':<10} {'branch':<12} {'dur(s)':>8} "
        f"{'in_tok':>8} {'out_tok':>8} {'turns':>6} {'tools':>6}"
    )
    lines = [header, "-" * len(header)]
    for r in records:
        session_id = (r.get("session_id") or "")[:12]
        tag = (r.get("tag") or "-")[:10]
        branch = (r.get("git_branch") or "-")[:12]
        duration = r.get("duration_seconds")
        duration_str = f"{duration:.0f}" if duration is not None else "-"
        lines.append(
            f"{session_id:<12} {tag:<10} {branch:<12} {duration_str:>8} "
            f"{r.get('input_tokens', 0):>8} {r.get('output_tokens', 0):>8} "
            f"{r.get('turn_count', 0):>6} {r.get('tool_call_count', 0):>6}"
        )
    return "\n".join(lines)


def format_summary(records):
    if not records:
        return "No sessions matched."
    n = len(records)
    total_input = sum(r.get("input_tokens", 0) for r in records)
    total_output = sum(r.get("output_tokens", 0) for r in records)
    total_turns = sum(r.get("turn_count", 0) for r in records)
    durations = [r["duration_seconds"] for r in records if r.get("duration_seconds") is not None]
    avg_duration = sum(durations) / len(durations) if durations else 0
    return (
        f"\n{n} session(s) - avg duration {avg_duration:.0f}s, "
        f"total input tokens {total_input}, total output tokens {total_output}, "
        f"avg turns {total_turns / n:.1f}"
    )


def compute_token_type_totals(records):
    return {
        "series-1": sum(r.get("input_tokens", 0) or 0 for r in records),
        "series-2": sum(r.get("output_tokens", 0) or 0 for r in records),
        "series-3": sum(r.get("cache_read_tokens", 0) or 0 for r in records),
        "series-4": sum(r.get("cache_creation_tokens", 0) or 0 for r in records),
    }


SERIES_NAMES = {
    "series-1": "input",
    "series-2": "output",
    "series-3": "cache read",
    "series-4": "cache creation",
}


def render_composition_legend(totals):
    chips = []
    for css_class, name in SERIES_NAMES.items():
        chips.append(
            f'<span class="legend-chip" data-series="{css_class}" '
            f'onmouseenter="previewHighlight(\'{css_class}\')" onmouseleave="endPreviewHighlight()" '
            f'onclick="highlightTokenType(\'{css_class}\', event)">'
            f'<span class="legend-swatch {css_class}"></span>{name} · '
            f'<span class="legend-total" data-series="{css_class}">{format_tokens(totals.get(css_class, 0))}</span></span>'
        )
    return '<div class="comp-legend">' + "".join(chips) + "</div>"


def render_composition_bar(record):
    segments = [
        ("series-1", record.get("input_tokens", 0) or 0),
        ("series-2", record.get("output_tokens", 0) or 0),
        ("series-3", record.get("cache_read_tokens", 0) or 0),
        ("series-4", record.get("cache_creation_tokens", 0) or 0),
    ]
    total = sum(v for _, v in segments)
    if total <= 0:
        return '<div class="comp-bar comp-bar-empty"></div>'
    parts = []
    for css_class, value in segments:
        if value <= 0:
            continue
        pct = value / total * 100
        name = SERIES_NAMES[css_class]
        parts.append(
            f'<span class="comp-seg {css_class}" style="width:{pct:.2f}%" '
            f'title="{name} · {format_tokens(value)} ({pct:.0f}%)" '
            f'onmouseenter="previewHighlight(\'{css_class}\')" onmouseleave="endPreviewHighlight()" '
            f'onclick="highlightTokenType(\'{css_class}\', event)"></span>'
        )
    return f'<div class="comp-bar">{"".join(parts)}</div>'


def render_session_row(record, is_active, group_colors, group_index, subagent_count=0):
    session_id = record.get("session_id") or ""
    tag = html.escape(record.get("tag") or "-")
    branch = html.escape(record.get("git_branch") or "-")
    tmux_window = html.escape(record.get("tmux_window") or "-")
    task_label = html.escape(record.get("task_label") or "-")
    date_str = format_date(record.get("timestamp_start"))
    duration_str = format_duration(record.get("duration_seconds"))
    total_tokens = format_tokens((record.get("input_tokens", 0) or 0) + (record.get("output_tokens", 0) or 0))
    active_class = " active" if is_active else ""
    group_class = group_colors.get(record.get("project_path"), "group-color-none")
    # Compact "has subagents" indicator (2026-07-21) - a count badge here
    # answers "does this session have subagents worth checking" without
    # opening every single row's detail panel to find out.
    subagent_badge = f"🤖{subagent_count}" if subagent_count else ""
    return (
        f'<div class="session-row {group_class}{active_class}" id="row-{session_id}" '
        f'data-group="{group_index}" style="display:none" '
        f"onclick=\"showSession('{session_id}')\">"
        f'<span class="sid">{session_id[:12]}</span>'
        f'<span class="subagent-col">{subagent_badge}</span>'
        f'<span class="date-col">{date_str}</span>'
        f'<span class="tmux-window">{tmux_window}</span>'
        f'<span class="label-col">{task_label}</span>'
        f'<span class="tag-col">{tag}</span><span class="branch-col">{branch}</span>'
        f'<span class="tokens-total">{total_tokens}</span>'
        f"{render_composition_bar(record)}"
        f'<span class="duration-col">{duration_str}</span>'
        f"</div>"
    )


def render_group_header(group_index, group):
    project_path = group["project"]
    label = project_display_label(project_path)
    count = len(group["records"])
    total_tokens = format_tokens(
        sum((r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0) for r in group["records"])
    )
    title_attr = f' title="{html.escape(project_path)}"' if project_path else ""
    return (
        f'<div class="group-header collapsed {group["class"]}" id="group-{group_index}"{title_attr} '
        f"onclick=\"toggleGroup({group_index})\">"
        '<span class="group-icon">▸</span>'
        f"<span>{html.escape(label)} ({count} sessões · {total_tokens} tokens)</span>"
        "</div>"
    )


def render_session_header():
    return (
        '<div class="session-row session-header">'
        '<span class="sid">id</span>'
        '<span class="subagent-col">🤖</span>'
        '<span class="date-col">data</span>'
        '<span class="tmux-window">aba</span>'
        '<span class="label-col">tarefa</span>'
        '<span class="tag-col">tag</span>'
        '<span class="branch-col">branch</span>'
        '<span class="tokens-total">tokens</span>'
        '<span class="comp-header">composição</span>'
        '<span class="duration-col">duração</span>'
        "</div>"
    )


def render_detail_panel(session_id, subagents, is_active):
    display = "block" if is_active else "none"
    title = ""
    subagents = [
        s for s in subagents
        if (s.get("input_tokens", 0) or 0) + (s.get("output_tokens", 0) or 0) > 0
    ]
    if not subagents:
        body = '<p class="empty">Nenhum subagente nesta sessão.</p>'
    else:
        ordered = sorted(subagents, key=lambda s: s.get("timestamp_start") or "", reverse=True)
        title = f'<h4 class="subagent-title">Subagentes despachados ({len(ordered)})</h4>'
        header = (
            '<div class="subagent-row subagent-header">'
            "<span>data</span><span>tarefa</span><span>tokens</span><span>tools</span><span>duração</span>"
            "</div>"
        )
        rows = [header]
        for i, subagent in enumerate(ordered):
            duration_str = format_duration(subagent.get("duration_seconds"))
            agent_type = html.escape(subagent.get("agent_type") or "-")
            agent_label = subagent.get("agent_label")
            if agent_label:
                task_text = f"{agent_type} — {html.escape(agent_label)}"
            else:
                task_text = f"{agent_type} (sem transcript salvo)"
            date_str = format_date(subagent.get("timestamp_start"))
            # A composition bar instead of the old "{in}/{out} tok" text
            # (2026-07-21) - the slash format was ambiguous about which
            # number was which, and this also gives subagent rows the
            # same click-to-filter behavior session rows already had via
            # highlightTokenType, sharing the exact legend/highlight
            # wiring instead of needing its own.
            comp_bar = render_composition_bar(subagent)
            zebra_class = " subagent-row-odd" if i % 2 == 1 else ""
            rows.append(
                f'<div class="subagent-row{zebra_class}">'
                f"<span>{date_str}</span>"
                f"<span>{task_text}</span>"
                f"<span>{comp_bar}</span>"
                f'<span>{subagent.get("tool_call_count", 0)} tools</span>'
                f"<span>{duration_str}</span>"
                "</div>"
            )
        body = "\n".join(rows)
    return (
        f'<div class="detail-panel" id="detail-{session_id}" style="display:{display}">'
        f'<button class="detail-close" onclick="closeDetail(\'{session_id}\')" aria-label="Fechar">×</button>'
        f"<h3>{session_id}</h3>{title}{body}</div>"
    )


# Trend chart granularity, chosen by the data's own span (2026-07-21) -
# a single day of activity read as a wall of near-empty day-bars; a
# multi-year history would read as an unreadable wall of day-bars the
# other way. key_fmt buckets timestamps into that granularity; parse_fmt
# reads a bucket key back into a datetime (must round-trip key_fmt
# exactly); tick_fmt is what's shown on the x-axis.
GRANULARITY_CONFIG = {
    "hour": {"key_fmt": "%Y-%m-%dT%H", "parse_fmt": "%Y-%m-%dT%H", "tick_fmt": "%d/%m %Hh"},
    "day": {"key_fmt": "%Y-%m-%d", "parse_fmt": "%Y-%m-%d", "tick_fmt": "%d/%m"},
    "month": {"key_fmt": "%Y-%m", "parse_fmt": "%Y-%m", "tick_fmt": "%m/%Y"},
    "year": {"key_fmt": "%Y", "parse_fmt": "%Y", "tick_fmt": "%Y"},
}


def choose_granularity(timestamps):
    if len(timestamps) < 2:
        return "day"
    span = max(timestamps) - min(timestamps)
    if span <= timedelta(hours=48):
        return "hour"
    if span <= timedelta(days=180):
        return "day"
    if span <= timedelta(days=365 * 3):
        return "month"
    return "year"


def aggregate_by_day(records):
    dated = []
    timestamps = []
    for r in records:
        timestamp_start = r.get("timestamp_start")
        if not timestamp_start:
            continue
        try:
            dt = datetime.fromisoformat(timestamp_start.replace("Z", "+00:00")).astimezone()
        except (ValueError, AttributeError, TypeError):
            continue
        dated.append((dt, r))
        timestamps.append(dt)

    granularity = choose_granularity(timestamps)
    key_fmt = GRANULARITY_CONFIG[granularity]["key_fmt"]
    buckets = {}
    bucket_order = []
    for dt, r in dated:
        key = dt.strftime(key_fmt)
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(r)
    bucket_order.sort()
    return granularity, [{"day": key, "records": buckets[key]} for key in bucket_order]


def render_trend_chart(records, group_colors):
    granularity, days = aggregate_by_day(records)
    parse_fmt = GRANULARITY_CONFIG[granularity]["parse_fmt"]
    tick_fmt = GRANULARITY_CONFIG[granularity]["tick_fmt"]
    n = len(days)
    if n < 2:
        return ""

    day_totals = [
        sum((r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0) for r in day["records"])
        for day in days
    ]
    day_token_totals = [
        sum(
            (r.get("input_tokens", 0) or 0)
            + (r.get("output_tokens", 0) or 0)
            + (r.get("cache_read_tokens", 0) or 0)
            + (r.get("cache_creation_tokens", 0) or 0)
            for r in day["records"]
        )
        for day in days
    ]
    # Each mode gets its OWN scale/axis — see the note on this in an
    # earlier round's fix: sharing one denominator between the two modes
    # let cache-heavy days starve the "por projeto" bars to near-zero
    # height. That's still true per-day, so the same split applies here.
    max_pane = max(day_totals) or 1
    max_tokens = max(day_token_totals) or 1

    height = 150
    padding_left = 45
    padding_right = 10
    padding_top = 10
    padding_bottom = 20
    min_bar_width = 8
    bar_gap = 2

    # Bars represent DAYS now, not sessions, so n is naturally much
    # smaller/bounded than the old per-session count ever was — but a
    # long --days/--all history can still have many distinct days. Try
    # the normal clamp-based width first; if that would squeeze bars
    # below the 8px floor (dataviz's minimum mark size), size the chart
    # to fit every bar at exactly the floor width instead and let it
    # scroll horizontally — this function has already shipped two real
    # geometry bugs from letting bar width shrink toward zero, so this
    # floor is a hard rule, not a suggestion.
    # Panorama is now this chart's own tab (it no longer shares the page
    # with the session table), so there's meaningfully more room. Bumps the
    # per-day step (12px -> 16px, ~33% wider bars in the common case) and
    # drops the old 1000px ceiling entirely, matching "sem teto fixo." The
    # 8px floor / scrollable-fallback logic below this point is UNCHANGED —
    # this only changes how generously wide the non-floor-hit case renders.
    # 3x wider by default (2026-07-21 feedback, after item 1 made width a
    # fixed pixel value instead of stretchy) - per-day step 16px -> 48px,
    # floor 400 -> 1200, so days read as more spread out; .trend-scroll
    # handles anything past the visible container via horizontal scroll.
    candidate_width = max(1200, 40 + n * 48)
    plot_width = candidate_width - padding_left - padding_right
    step = plot_width / n
    bar_width = step * 0.6
    if bar_width < min_bar_width:
        step = min_bar_width + bar_gap
        bar_width = min_bar_width
        plot_width = step * n
        width = plot_width + padding_left + padding_right
        scrollable = True
    else:
        width = candidate_width
        scrollable = False

    plot_height = height - padding_top - padding_bottom

    def y_for(value, max_value):
        return padding_top + plot_height - (value / max_value) * plot_height

    def build_y_axis(max_value):
        parts = []
        for frac in (0, 0.25, 0.5, 0.75, 1.0):
            value = max_value * frac
            y = y_for(value, max_value)
            parts.append(
                f'<line x1="{padding_left}" y1="{y:.1f}" x2="{width - padding_right}" y2="{y:.1f}" class="chart-gridline" />'
                f'<text x="4" y="{y + 3:.1f}" class="chart-axis-label">{format_tokens(value)}</text>'
            )
        return "".join(parts)

    x_axis = []
    tick_count = min(5, n)
    for t in range(tick_count):
        idx = round(t * (n - 1) / (tick_count - 1)) if tick_count > 1 else 0
        x = padding_left + idx * step + bar_width / 2
        bucket_dt = datetime.strptime(days[idx]["day"], parse_fmt)
        tick_date = bucket_dt.strftime(tick_fmt)
        x_axis.append(
            f'<text x="{x:.1f}" y="{height - 4}" class="chart-axis-label" text-anchor="middle">{tick_date}</text>'
        )
    x_axis_html = "".join(x_axis)

    # Highlight band for the last 7 buckets (2026-07-21: was "last 7
    # calendar days" specifically, but with hour/month/year granularity
    # now possible too, "last 7 of whatever unit is on screen" is the
    # only version of this that still makes sense across all of them).
    highlight_html = ""
    if n > 7:
        highlight_start_idx = n - 7
        hx = padding_left + highlight_start_idx * step
        hwidth = 7 * step
        highlight_html = (
            f'<rect x="{hx:.1f}" y="{padding_top}" width="{hwidth:.1f}" height="{plot_height}" '
            'class="chart-highlight-band" />'
        )

    def bars_for_pane():
        bars = []
        for i, day in enumerate(days):
            x = padding_left + i * step + (step - bar_width) / 2
            by_project = {}
            project_order = []
            for r in day["records"]:
                project = r.get("project_path")
                if project not in by_project:
                    by_project[project] = 0
                    project_order.append(project)
                by_project[project] += (r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0)
            y_cursor = padding_top + plot_height
            for project in project_order:
                value = by_project[project]
                if value <= 0:
                    continue
                seg_height = (value / max_pane) * plot_height if max_pane else 0
                y_cursor -= seg_height
                group_class = group_colors.get(project, "group-color-none")
                project_label = project_display_label(project)
                bars.append(
                    f'<rect x="{x:.1f}" y="{y_cursor:.1f}" width="{bar_width:.1f}" height="{seg_height:.1f}" '
                    f'class="chart-bar {group_class}" '
                    f'onmouseenter="previewHighlight(\'{group_class}\')" onmouseleave="endPreviewHighlight()" '
                    f'onclick="highlightProject(\'{group_class}\', event)">'
                    f"<title>{html.escape(project_label)} · {day['day']} - {format_tokens(value)} tokens</title></rect>"
                )
        return "".join(bars)

    def bars_for_tokens():
        bars = []
        for i, day in enumerate(days):
            x = padding_left + i * step + (step - bar_width) / 2
            segments = [
                ("series-1", sum(r.get("input_tokens", 0) or 0 for r in day["records"])),
                ("series-2", sum(r.get("output_tokens", 0) or 0 for r in day["records"])),
                ("series-3", sum(r.get("cache_read_tokens", 0) or 0 for r in day["records"])),
                ("series-4", sum(r.get("cache_creation_tokens", 0) or 0 for r in day["records"])),
            ]
            y_cursor = padding_top + plot_height
            for css_class, value in segments:
                if value <= 0:
                    continue
                seg_height = (value / max_tokens) * plot_height if max_tokens else 0
                y_cursor -= seg_height
                type_name = SERIES_NAMES[css_class]
                bars.append(
                    f'<rect x="{x:.1f}" y="{y_cursor:.1f}" width="{bar_width:.1f}" height="{seg_height:.1f}" '
                    f'class="chart-bar {css_class}" '
                    f'onmouseenter="previewHighlight(\'{css_class}\')" onmouseleave="endPreviewHighlight()" '
                    f'onclick="highlightTokenType(\'{css_class}\', event)">'
                    f"<title>{type_name} · {day['day']} - {format_tokens(value)} tokens</title></rect>"
                )
        return "".join(bars)

    # width is fixed in CSS pixels (not 100%/responsive): with
    # preserveAspectRatio="none" a stretchy width forces the browser to
    # scale the viewBox's x-axis to fill the container, which distorts
    # every bar and label horizontally (they render at their real size
    # only when 1 viewBox unit == 1 CSS pixel). .trend-scroll's
    # overflow-x:auto gives the "long axis" instead, by scrolling past
    # this fixed width rather than squishing/stretching it.
    pane_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" id="chart-pane" preserveAspectRatio="none" '
        f'style="width:{width}px">'
        f"{highlight_html}{build_y_axis(max_pane)}{x_axis_html}{bars_for_pane()}</svg>"
    )
    tokens_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" id="chart-tokens" preserveAspectRatio="none" '
        f'style="width:{width}px">'
        f"{highlight_html}{build_y_axis(max_tokens)}{x_axis_html}{bars_for_tokens()}</svg>"
    )

    # Always scrollable now, not just in the floor-hit (scrollable=True)
    # case: with the width cap removed (an earlier round), a long real
    # history can make the chart genuinely very wide — the outer
    # .trend-scroll container (CSS, always overflow-x:auto) keeps the page
    # itself from breaking, and the auto-scroll-to-end script (below, in
    # render_html) shows the most recent days by default rather than the
    # oldest. data-floor-hit is kept purely as a debugging/testing signal
    # for whether the 8px floor engaged.
    #
    # Round 5 removed the toggle because a hidden chart's meaning wasn't
    # discoverable without clicking first - but with both rendered at
    # full width unconditionally, each one only gets the fixed-width
    # treatment from item 1 at the cost of showing both, all the time,
    # taking up sticky-header space. Reintroduced here (2026-07-20) as
    # explicitly labeled tabs ("por projeto" / "por tipo de token"),
    # which fixes the discoverability complaint without giving up the
    # single-chart-at-a-time layout.
    # Legend for "por projeto"'s bar colors (2026-07-21) - those colors
    # were only ever explained by the session-list rows' matching left
    # border, which isn't visible from up here. Built from the projects
    # actually present in this chart's own data, in first-seen order,
    # deduped by color_group_key so a worktree collapses into its
    # parent's single entry instead of getting its own near-duplicate
    # (they already share a color, per assign_group_colors above).
    pane_projects = []
    seen_keys = set()
    for day in days:
        for r in day["records"]:
            project = r.get("project_path")
            key = color_group_key(project)
            if key not in seen_keys:
                seen_keys.add(key)
                pane_projects.append(project)
    pane_legend_chips = []
    for project in pane_projects:
        group_class = group_colors.get(project, "group-color-none")
        # Just the directory name (2026-07-21: "não precisa o diretório
        # absoluto"), not the full path.
        raw_name = html.escape(os.path.basename((project or "").rstrip("/")) or "sem projeto")
        label = html.escape(project_display_label(project))
        pane_legend_chips.append(
            f'<span class="legend-chip" '
            f'onmouseenter="previewHighlight(\'{group_class}\')" onmouseleave="endPreviewHighlight()" '
            f'onclick="highlightProject(\'{group_class}\', event)">'
            f'<span class="legend-swatch {group_class}"></span>{raw_name}:{label}</span>'
        )
    pane_legend = "".join(pane_legend_chips)
    pane_legend_html = f'<div class="project-chart-legend">{pane_legend}</div>'

    return (
        '<div class="trend-container">'
        '<div class="trend-mode-tabs">'
        '<span class="tab-btn active" id="trend-btn-pane" onclick="showTrendMode(\'pane\')">Por projeto</span>'
        '<span class="tab-btn" id="trend-btn-tokens" onclick="showTrendMode(\'tokens\')">Por tipo de token</span>'
        "</div>"
        '<div class="trend-pane active" id="trend-pane-pane">'
        f'<div class="trend-scroll" data-floor-hit="{str(scrollable).lower()}">{pane_svg}</div>'
        f"{pane_legend_html}"
        "</div>"
        '<div class="trend-pane" id="trend-pane-tokens">'
        f'<div class="trend-scroll" data-floor-hit="{str(scrollable).lower()}">{tokens_svg}</div>'
        "</div>"
        "</div>"
    )


def compute_project_series_totals(records):
    per_project = {}
    for r in records:
        project = r.get("project_path")
        bucket = per_project.setdefault(project, {"series-1": 0, "series-2": 0, "series-3": 0, "series-4": 0})
        bucket["series-1"] += r.get("input_tokens", 0) or 0
        bucket["series-2"] += r.get("output_tokens", 0) or 0
        bucket["series-3"] += r.get("cache_read_tokens", 0) or 0
        bucket["series-4"] += r.get("cache_creation_tokens", 0) or 0
    grand_totals = {project: sum(series.values()) for project, series in per_project.items()}
    return per_project, grand_totals


def render_project_totals_chart(records, project_first_seen, project_last_seen, open_projects):
    ordered_projects, _totals, _max_total = compute_project_totals(records, project_last_seen)
    # Segmented by token type (2026-07-20) instead of one flat bar, so the
    # composition legend can highlight/filter these bars the same way it
    # already does the trend chart's "por tipo de token" mode - same
    # series-N classes, same highlightTokenType handler, same CSS.
    series_by_project, totals = compute_project_series_totals(records)
    # Omit zero-token projects (2026-07-20) - a project section with
    # nothing to show is just dead weight in this list.
    ordered_projects = [p for p in ordered_projects if totals.get(p, 0) > 0]
    if not ordered_projects:
        return ""
    max_total = max(totals.values()) or 1

    # row_height must clear label_baseline(=row start) + bar(9..23) + the
    # NEXT row's label ascent, or that next label overlaps up into this
    # row's bar - 24 left only 1px between a bar's bottom (y+23) and the
    # next label's baseline (y+24), and a ~10px font's ascent (~7-8px)
    # ate straight into the bar above it. 34 leaves real clearance.
    row_height = 34
    bar_max_width = 300
    label_x = 4
    # 620, not 460 (2026-07-21) - the total text ("<total> · há N dias ·
    # <filtered value>") got clipped at the SVG's right edge once the
    # "grand total + filtered value together" feedback made that string
    # longer; 460-310=150 user units wasn't enough room for it.
    width = 620

    def render_rows(projects):
        rows = []
        y = 14
        for project in projects:
            total = totals[project]
            label = project_display_label(project)
            title_attr = f' title="{html.escape(project)}"' if project else ""
            is_open = project in open_projects
            # Bar color now carries token-type (to match the legend), so
            # "open now" moved from bar color to a small text marker next
            # to the label instead of being lost.
            open_marker = " ●" if is_open else ""
            days_running = project_days_running(project_first_seen, project)
            seg_rects = []
            x_cursor = label_x
            for css_class, value in series_by_project.get(project, {}).items():
                if value <= 0:
                    continue
                seg_width = (value / max_total) * bar_max_width if max_total else 0
                name = SERIES_NAMES[css_class]
                seg_rects.append(
                    f'<rect x="{x_cursor:.1f}" y="{y + 9}" width="{seg_width:.1f}" height="14" '
                    f'class="chart-bar {css_class}" '
                    f'onmouseenter="previewHighlight(\'{css_class}\')" onmouseleave="endPreviewHighlight()" '
                    f'onclick="highlightTokenType(\'{css_class}\', event)">'
                    f"<title>{html.escape(label)} · {name} · {format_tokens(value)}</title></rect>"
                )
                x_cursor += seg_width
            # Total label carries the grand total (always visible, 2026-07-21
            # feedback: the colored per-type number alone wasn't enough,
            # the total needs to stay visible alongside it) plus one
            # separator+tspan per series, hidden by CSS unless
            # body[data-highlight] selects that series - clicking a
            # legend chip appends " · <that type>" next to the still-visible
            # grand total instead of replacing it. The per-series tspans
            # are italic and tinted that series' color so it reads as
            # "the filtered slice," not a second real total.
            series_tspans = "".join(
                f'<tspan class="total-only {css_class}"> · {format_tokens(series_by_project.get(project, {}).get(css_class, 0))}</tspan>'
                for css_class in ("series-1", "series-2", "series-3", "series-4")
            )
            total_text = (
                f'<text x="{label_x + bar_max_width + 6}" y="{y + 20}" class="chart-axis-label proj-total">'
                f'<tspan class="total-grand">{format_tokens(total)}{days_running}</tspan>'
                f"{series_tspans}"
                "</text>"
            )
            rows.append(
                f'<text x="{label_x}" y="{y}" class="project-chart-label"{title_attr}>{html.escape(label)}{open_marker}</text>'
                f'<rect x="{label_x}" y="{y + 9}" width="{bar_max_width}" height="14" class="project-chart-track" />'
                f"{''.join(seg_rects)}"
                f"{total_text}"
            )
            y += row_height
        height = len(projects) * row_height + 20
        return rows, height

    visible_projects = ordered_projects[:10]
    hidden_projects = ordered_projects[10:]

    visible_rows, visible_height = render_rows(visible_projects)
    visible_svg = f'<svg viewBox="0 0 {width} {visible_height}" class="project-chart-svg">{"".join(visible_rows)}</svg>'

    more_html = ""
    if hidden_projects:
        hidden_rows, hidden_height = render_rows(hidden_projects)
        hidden_svg = (
            f'<svg viewBox="0 0 {width} {hidden_height}" class="project-chart-svg" '
            f'id="project-chart-more" style="display:none">{"".join(hidden_rows)}</svg>'
        )
        more_html = (
            f'<div class="project-chart-toggle" id="project-chart-toggle" onclick="toggleProjectsMore()">'
            f"+ {len(hidden_projects)} projetos históricos</div>"
            f"{hidden_svg}"
        )

    # Bar color now maps to token type (see render_rows above), sharing
    # the exact same legend markup/classes/click handler as the Sessões
    # tab - clicking a chip highlights that type's segments here too via
    # the same body[data-highlight~=...] CSS, no separate wiring needed.
    overall_totals = {
        css_class: sum(series.get(css_class, 0) for series in series_by_project.values())
        for css_class in ("series-1", "series-2", "series-3", "series-4")
    }
    legend = render_composition_legend(overall_totals) + (
        '<div class="project-chart-legend"><span class="legend-chip">● sessão aberta agora</span></div>'
    )
    return (
        '<div class="project-chart-container">'
        '<h3 class="project-chart-title">Total de tokens por projeto</h3>'
        '<p class="project-chart-sort-note">ordenado por atividade mais recente primeiro</p>'
        f"{legend}"
        f"{visible_svg}"
        f"{more_html}"
        "</div>"
    )


def render_html(records, subagents_by_session, chart_records, project_first_seen, project_last_seen, open_projects):
    group_colors = assign_group_colors(chart_records)
    groups = build_project_groups(records, group_colors)

    # Alberto: omit zero-token sections entirely (2026-07-20) - a
    # session that never recorded any input/output tokens (aborted
    # before the first turn, etc.) adds a row with nothing useful in
    # it. Filter before deciding whether to render a group header at
    # all, so a group whose every session was zero-token doesn't leave
    # a header with no rows under it.
    visible_groups = []
    for group_index, group in enumerate(groups):
        visible_records = [
            r for r in group["records"]
            if (r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0) > 0
        ]
        if visible_records:
            visible_groups.append((group_index, {**group, "records": visible_records}))

    # Distinguish sessions that got the SAME AI-generated task_label
    # (2026-07-21) - the labeler can independently produce an identical
    # short summary for two genuinely different sessions, and with
    # nothing else visible in that column they read as duplicates.
    # Numbering only kicks in for a label actually shared by 2+ distinct
    # session_ids, in first-seen order across the whole table.
    label_counts = {}
    for _, group in visible_groups:
        for r in group["records"]:
            label = r.get("task_label")
            if label:
                label_counts[label] = label_counts.get(label, 0) + 1
    label_seen = {}

    rows_html = []
    panels_html = []
    for group_index, group in visible_groups:
        rows_html.append(render_group_header(group_index, group))
        for record in group["records"]:
            session_id = record.get("session_id") or ""
            label = record.get("task_label")
            if label and label_counts.get(label, 0) > 1:
                label_seen[label] = label_seen.get(label, 0) + 1
                record = {**record, "task_label": f"{label} ({label_seen[label]})"}
            session_subagents = subagents_by_session.get(session_id, [])
            visible_subagent_count = sum(
                1 for s in session_subagents
                if (s.get("input_tokens", 0) or 0) + (s.get("output_tokens", 0) or 0) > 0
            )
            rows_html.append(
                render_session_row(record, False, group_colors, group_index, visible_subagent_count)
            )
            panels_html.append(render_detail_panel(session_id, session_subagents, False))

    trend_html = render_trend_chart(chart_records, group_colors)
    project_totals_html = render_project_totals_chart(chart_records, project_first_seen, project_last_seen, open_projects)

    style = """
    <style>
      :root {
        --surface-1: #fcfcfb;
        --text-primary: #0b0b0b;
        --text-secondary: #52514e;
        --text-muted: #898781;
        --border: #e1e0d9;
        --series-1: #2a78d6;
        --series-2: #1baf7a;
        --series-3: #eda100;
        --series-4: #008300;
        --group-1: #2a78d6; --group-1-tint: rgba(42,120,214,0.08);
        --group-2: #1baf7a; --group-2-tint: rgba(27,175,122,0.08);
        --group-3: #eda100; --group-3-tint: rgba(237,161,0,0.08);
        --group-4: #008300; --group-4-tint: rgba(0,131,0,0.08);
        --group-5: #4a3aa7; --group-5-tint: rgba(74,58,167,0.08);
        --group-6: #e34948; --group-6-tint: rgba(227,73,72,0.08);
        --group-7: #e87ba4; --group-7-tint: rgba(232,123,164,0.08);
        --group-8: #eb6834; --group-8-tint: rgba(235,104,52,0.08);
        --group-9: #6bc62f; --group-9-tint: rgba(107,198,47,0.08);
        --group-10: #4dc62f; --group-10-tint: rgba(77,198,47,0.08);
        --group-11: #2fc62f; --group-11-tint: rgba(47,198,47,0.08);
        --group-12: #2fc64d; --group-12-tint: rgba(47,198,77,0.08);
        --group-13: #2fc66b; --group-13-tint: rgba(47,198,107,0.08);
        --group-14: #2fc68a; --group-14-tint: rgba(47,198,138,0.08);
        --group-15: #2fc6a8; --group-15-tint: rgba(47,198,168,0.08);
        --group-16: #2fc6c6; --group-16-tint: rgba(47,198,198,0.08);
        --group-17: #2fa8c6; --group-17-tint: rgba(47,168,198,0.08);
        --group-18: #2f8ac6; --group-18-tint: rgba(47,138,198,0.08);
        --group-19: #2f6bc6; --group-19-tint: rgba(47,107,198,0.08);
        --group-20: #2f4dc6; --group-20-tint: rgba(47,77,198,0.08);
        --group-21: #2f2fc6; --group-21-tint: rgba(47,47,198,0.08);
        --group-22: #4d2fc6; --group-22-tint: rgba(77,47,198,0.08);
        --group-23: #6b2fc6; --group-23-tint: rgba(107,47,198,0.08);
        --group-24: #8a2fc6; --group-24-tint: rgba(138,47,198,0.08);
        --group-25: #a82fc6; --group-25-tint: rgba(168,47,198,0.08);
        --group-26: #c62fc6; --group-26-tint: rgba(198,47,198,0.08);
        --group-27: #c62fa8; --group-27-tint: rgba(198,47,168,0.08);
        --group-28: #c62f8a; --group-28-tint: rgba(198,47,138,0.08);
        --group-29: #c62f6b; --group-29-tint: rgba(198,47,107,0.08);
        --group-30: #c62f4d; --group-30-tint: rgba(198,47,77,0.08);
        --group-none: #898781; --group-none-tint: transparent;
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --surface-1: #1a1a19;
          --text-primary: #ffffff;
          --text-secondary: #c3c2b7;
          --text-muted: #898781;
          --border: #2c2c2a;
          --series-1: #3987e5;
          --series-2: #199e70;
          --series-3: #c98500;
          --series-4: #008300;
          --group-1: #3987e5; --group-1-tint: rgba(57,135,229,0.12);
          --group-2: #199e70; --group-2-tint: rgba(25,158,112,0.12);
          --group-3: #c98500; --group-3-tint: rgba(201,133,0,0.12);
          --group-4: #008300; --group-4-tint: rgba(0,131,0,0.12);
          --group-5: #9085e9; --group-5-tint: rgba(144,133,233,0.12);
          --group-6: #e66767; --group-6-tint: rgba(230,103,103,0.12);
          --group-7: #d55181; --group-7-tint: rgba(213,81,129,0.12);
          --group-8: #d95926; --group-8-tint: rgba(217,89,38,0.12);
          --group-9: #8cdc56; --group-9-tint: rgba(140,220,86,0.12);
          --group-10: #71dc56; --group-10-tint: rgba(113,220,86,0.12);
          --group-11: #56dc56; --group-11-tint: rgba(86,220,86,0.12);
          --group-12: #56dc71; --group-12-tint: rgba(86,220,113,0.12);
          --group-13: #56dc8c; --group-13-tint: rgba(86,220,140,0.12);
          --group-14: #56dca6; --group-14-tint: rgba(86,220,166,0.12);
          --group-15: #56dcc1; --group-15-tint: rgba(86,220,193,0.12);
          --group-16: #56dcdc; --group-16-tint: rgba(86,220,220,0.12);
          --group-17: #56c1dc; --group-17-tint: rgba(86,193,220,0.12);
          --group-18: #56a6dc; --group-18-tint: rgba(86,166,220,0.12);
          --group-19: #568cdc; --group-19-tint: rgba(86,140,220,0.12);
          --group-20: #5671dc; --group-20-tint: rgba(86,113,220,0.12);
          --group-21: #5656dc; --group-21-tint: rgba(86,86,220,0.12);
          --group-22: #7156dc; --group-22-tint: rgba(113,86,220,0.12);
          --group-23: #8c56dc; --group-23-tint: rgba(140,86,220,0.12);
          --group-24: #a656dc; --group-24-tint: rgba(166,86,220,0.12);
          --group-25: #c156dc; --group-25-tint: rgba(193,86,220,0.12);
          --group-26: #dc56dc; --group-26-tint: rgba(220,86,220,0.12);
          --group-27: #dc56c1; --group-27-tint: rgba(220,86,193,0.12);
          --group-28: #dc56a6; --group-28-tint: rgba(220,86,166,0.12);
          --group-29: #dc568c; --group-29-tint: rgba(220,86,140,0.12);
          --group-30: #dc5671; --group-30-tint: rgba(220,86,113,0.12);
          --group-none: #898781; --group-none-tint: transparent;
        }
      }
      .trend-container { padding: 12px 16px; border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 5; background: var(--surface-1); }
      .trend-mode-tabs { display: flex; gap: 4px; margin-bottom: 8px; }
      .trend-pane { display: none; }
      .trend-pane.active { display: block; }
      .trend-scroll { overflow-x: auto; max-width: 100%; }
      .trend-svg { height: 150px; display: block; margin: 0 auto; }
      .chart-highlight-band { fill: var(--series-1); opacity: 0.06; }
      .project-chart-container { padding: 12px 16px; border-bottom: 1px solid var(--border); }
      .project-chart-title { font-size: 13px; margin: 0 0 8px; color: var(--text-secondary); }
      .project-chart-sort-note { font-size: 10px; margin: -4px 0 8px; color: var(--text-muted); }
      .project-chart-svg { width: 100%; max-width: 760px; display: block; }
      .project-chart-label { font-size: 10px; fill: var(--text-primary); font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
      .project-chart-track { fill: var(--border); }
      .project-chart-legend { display: flex; flex-wrap: wrap; row-gap: 4px; gap: 14px; padding: 6px 0 0; font-size: 10px; color: var(--text-secondary); }
      .project-chart-toggle { font-size: 11px; color: var(--series-1); cursor: pointer; padding: 4px 0; }
      .project-chart-toggle:hover { text-decoration: underline; }
      .chart-gridline { stroke: var(--border); stroke-width: 1; }
      .chart-axis-label { fill: var(--text-muted); font-size: 10px; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
      .chart-bar { cursor: pointer; }
      .chart-bar.group-color-1 { fill: var(--group-1); }
      .chart-bar.group-color-2 { fill: var(--group-2); }
      .chart-bar.group-color-3 { fill: var(--group-3); }
      .chart-bar.group-color-4 { fill: var(--group-4); }
      .chart-bar.group-color-5 { fill: var(--group-5); }
      .chart-bar.group-color-6 { fill: var(--group-6); }
      .chart-bar.group-color-7 { fill: var(--group-7); }
      .chart-bar.group-color-8 { fill: var(--group-8); }
      .chart-bar.group-color-9 { fill: var(--group-9); }
      .chart-bar.group-color-10 { fill: var(--group-10); }
      .chart-bar.group-color-11 { fill: var(--group-11); }
      .chart-bar.group-color-12 { fill: var(--group-12); }
      .chart-bar.group-color-13 { fill: var(--group-13); }
      .chart-bar.group-color-14 { fill: var(--group-14); }
      .chart-bar.group-color-15 { fill: var(--group-15); }
      .chart-bar.group-color-16 { fill: var(--group-16); }
      .chart-bar.group-color-17 { fill: var(--group-17); }
      .chart-bar.group-color-18 { fill: var(--group-18); }
      .chart-bar.group-color-19 { fill: var(--group-19); }
      .chart-bar.group-color-20 { fill: var(--group-20); }
      .chart-bar.group-color-21 { fill: var(--group-21); }
      .chart-bar.group-color-22 { fill: var(--group-22); }
      .chart-bar.group-color-23 { fill: var(--group-23); }
      .chart-bar.group-color-24 { fill: var(--group-24); }
      .chart-bar.group-color-25 { fill: var(--group-25); }
      .chart-bar.group-color-26 { fill: var(--group-26); }
      .chart-bar.group-color-27 { fill: var(--group-27); }
      .chart-bar.group-color-28 { fill: var(--group-28); }
      .chart-bar.group-color-29 { fill: var(--group-29); }
      .chart-bar.group-color-30 { fill: var(--group-30); }
      .chart-bar.group-color-none { fill: var(--group-none); }
      .chart-bar.series-1 { fill: var(--series-1); }
      .chart-bar.series-2 { fill: var(--series-2); }
      .chart-bar.series-3 { fill: var(--series-3); }
      .chart-bar.series-4 { fill: var(--series-4); }
      body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; background: var(--surface-1); color: var(--text-primary); }
      .tab-bar { display: flex; gap: 4px; padding: 8px 16px 0; border-bottom: 1px solid var(--border); }
      .tab-btn { padding: 6px 14px; font-size: 12px; cursor: pointer; color: var(--text-muted); border-bottom: 2px solid transparent; }
      .tab-btn.active { color: var(--text-primary); border-bottom-color: var(--series-1); font-weight: bold; }
      .tab-pane { display: none; }
      .tab-pane.active { display: block; }
      .main-row { display: flex; }
      .session-list { width: 62%; border-right: 1px solid var(--border); overflow-x: auto; font-family: monospace; }
      /* width: fit-content + min-width: 100% (2026-07-21) - a flex row
         with no explicit width stretches to fill .session-list's box
         (62% of the page) but does NOT grow past that to fit overflowing
         children; the row's own background then only covers that
         original width while content (and .session-list's
         overflow-x:auto scrollbar) extends further right, so the
         group-color tint stopped partway across a horizontally-scrolled
         row instead of following the content. fit-content lets the row
         grow to match its actual (wider) content when needed. */
      .session-row { display: flex; align-items: center; gap: 8px; padding: 6px 10px; cursor: pointer; border-bottom: 1px solid var(--border); font-size: 12px; width: fit-content; min-width: 100%; }
      .session-row > span:not(:last-child) { border-right: 1px solid var(--border); padding-right: 8px; }
      .session-row:hover { background: rgba(42, 120, 214, 0.08); }
      .session-row.active { background: rgba(42, 120, 214, 0.16); font-weight: bold; }
      .session-row .sid { color: var(--text-muted); width: 90px; flex-shrink: 0; }
      .session-row .subagent-col { width: 28px; flex-shrink: 0; text-align: center; }
      .session-row .tokens-total { color: var(--text-secondary); width: 55px; flex-shrink: 0; text-align: right; }
      .session-row .tmux-window { width: 90px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .session-header { position: sticky; top: 0; z-index: 1; background: var(--surface-1); font-weight: bold; color: var(--text-muted); font-size: 10px; cursor: default; border-bottom: 2px solid var(--border); }
      .session-header:hover { background: var(--surface-1); }
      .session-row .date-col { width: 65px; flex-shrink: 0; }
      .session-row .label-col { width: 300px; flex-shrink: 0; white-space: normal; word-break: break-word; }
      .session-row .tag-col { width: 50px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .session-row .branch-col { width: 70px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .session-row .duration-col { width: 55px; flex-shrink: 0; text-align: right; }
      .session-row .comp-header { width: 90px; flex-shrink: 0; }
      .comp-legend { display: flex; gap: 14px; padding: 8px 10px; font-size: 11px; color: var(--text-secondary); border-bottom: 1px solid var(--border); font-family: monospace; }
      .legend-chip { display: inline-flex; align-items: center; }
      .comp-legend .legend-chip { cursor: pointer; }
      .legend-swatch { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; }
      .legend-swatch.group-color-1 { background: var(--group-1); }
      .legend-swatch.group-color-2 { background: var(--group-2); }
      .legend-swatch.group-color-3 { background: var(--group-3); }
      .legend-swatch.group-color-4 { background: var(--group-4); }
      .legend-swatch.group-color-5 { background: var(--group-5); }
      .legend-swatch.group-color-6 { background: var(--group-6); }
      .legend-swatch.group-color-7 { background: var(--group-7); }
      .legend-swatch.group-color-8 { background: var(--group-8); }
      .legend-swatch.group-color-9 { background: var(--group-9); }
      .legend-swatch.group-color-10 { background: var(--group-10); }
      .legend-swatch.group-color-11 { background: var(--group-11); }
      .legend-swatch.group-color-12 { background: var(--group-12); }
      .legend-swatch.group-color-13 { background: var(--group-13); }
      .legend-swatch.group-color-14 { background: var(--group-14); }
      .legend-swatch.group-color-15 { background: var(--group-15); }
      .legend-swatch.group-color-16 { background: var(--group-16); }
      .legend-swatch.group-color-17 { background: var(--group-17); }
      .legend-swatch.group-color-18 { background: var(--group-18); }
      .legend-swatch.group-color-19 { background: var(--group-19); }
      .legend-swatch.group-color-20 { background: var(--group-20); }
      .legend-swatch.group-color-21 { background: var(--group-21); }
      .legend-swatch.group-color-22 { background: var(--group-22); }
      .legend-swatch.group-color-23 { background: var(--group-23); }
      .legend-swatch.group-color-24 { background: var(--group-24); }
      .legend-swatch.group-color-25 { background: var(--group-25); }
      .legend-swatch.group-color-26 { background: var(--group-26); }
      .legend-swatch.group-color-27 { background: var(--group-27); }
      .legend-swatch.group-color-28 { background: var(--group-28); }
      .legend-swatch.group-color-29 { background: var(--group-29); }
      .legend-swatch.group-color-30 { background: var(--group-30); }
      .legend-swatch.group-color-none { background: var(--group-none); }
      .legend-swatch.series-1 { background: var(--series-1); }
      .legend-swatch.series-2 { background: var(--series-2); }
      .legend-swatch.series-3 { background: var(--series-3); }
      .legend-swatch.series-4 { background: var(--series-4); }
      .legend-total { font-weight: 600; }
      body[data-highlight] .legend-chip { opacity: 0.4; }
      body[data-highlight] .legend-chip .legend-total { font-weight: 400; }
      body[data-highlight~="series-1"] .legend-chip[data-series="series-1"],
      body[data-highlight~="series-2"] .legend-chip[data-series="series-2"],
      body[data-highlight~="series-3"] .legend-chip[data-series="series-3"],
      body[data-highlight~="series-4"] .legend-chip[data-series="series-4"] { opacity: 1; font-weight: 700; }
      body[data-highlight~="series-1"] .legend-chip[data-series="series-1"] .legend-total,
      body[data-highlight~="series-2"] .legend-chip[data-series="series-2"] .legend-total,
      body[data-highlight~="series-3"] .legend-chip[data-series="series-3"] .legend-total,
      body[data-highlight~="series-4"] .legend-chip[data-series="series-4"] .legend-total { font-weight: 700; font-size: 13px; opacity: 1; }
      /* Spotlight pattern: dim everything, then un-dim the highlighted
         series. For .comp-seg the un-dim rule outranks this one on
         specificity alone (extra .series-N class); for .chart-bar.series-N
         the two tie on specificity and the un-dim rule wins only because
         it's declared AFTER this one - keep this block before the un-dim
         rules below, do not reorder. */
      body[data-highlight] .comp-seg, body[data-highlight] .chart-bar.series-1, body[data-highlight] .chart-bar.series-2, body[data-highlight] .chart-bar.series-3, body[data-highlight] .chart-bar.series-4 { opacity: 0.25; }
      body[data-highlight~="series-1"] .comp-seg.series-1, body[data-highlight~="series-1"] .chart-bar.series-1 { opacity: 1; }
      body[data-highlight~="series-2"] .comp-seg.series-2, body[data-highlight~="series-2"] .chart-bar.series-2 { opacity: 1; }
      body[data-highlight~="series-3"] .comp-seg.series-3, body[data-highlight~="series-3"] .chart-bar.series-3 { opacity: 1; }
      body[data-highlight~="series-4"] .comp-seg.series-4, body[data-highlight~="series-4"] .chart-bar.series-4 { opacity: 1; }
      body[data-highlight~="series-1"] .comp-seg.series-1 { outline: 1px solid var(--series-1); outline-offset: 1px; }
      body[data-highlight~="series-2"] .comp-seg.series-2 { outline: 1px solid var(--series-2); outline-offset: 1px; }
      body[data-highlight~="series-3"] .comp-seg.series-3 { outline: 1px solid var(--series-3); outline-offset: 1px; }
      body[data-highlight~="series-4"] .comp-seg.series-4 { outline: 1px solid var(--series-4); outline-offset: 1px; }
      body[data-highlight~="series-1"] .chart-bar.series-1 { stroke: var(--series-1); stroke-width: 1.5px; }
      body[data-highlight~="series-2"] .chart-bar.series-2 { stroke: var(--series-2); stroke-width: 1.5px; }
      body[data-highlight~="series-3"] .chart-bar.series-3 { stroke: var(--series-3); stroke-width: 1.5px; }
      body[data-highlight~="series-4"] .chart-bar.series-4 { stroke: var(--series-4); stroke-width: 1.5px; }
      /* Same spotlight pattern as series-N above, for "por projeto"'s
         group-color-N bars/legend (2026-07-21) - the two share one
         data-highlight attribute since only one trend-mode pane is ever
         visible at once. */
      body[data-highlight] .chart-bar.group-color-1, body[data-highlight] .chart-bar.group-color-2, body[data-highlight] .chart-bar.group-color-3, body[data-highlight] .chart-bar.group-color-4, body[data-highlight] .chart-bar.group-color-5, body[data-highlight] .chart-bar.group-color-6, body[data-highlight] .chart-bar.group-color-7, body[data-highlight] .chart-bar.group-color-8, body[data-highlight] .chart-bar.group-color-9, body[data-highlight] .chart-bar.group-color-10, body[data-highlight] .chart-bar.group-color-11, body[data-highlight] .chart-bar.group-color-12, body[data-highlight] .chart-bar.group-color-13, body[data-highlight] .chart-bar.group-color-14, body[data-highlight] .chart-bar.group-color-15, body[data-highlight] .chart-bar.group-color-16, body[data-highlight] .chart-bar.group-color-17, body[data-highlight] .chart-bar.group-color-18, body[data-highlight] .chart-bar.group-color-19, body[data-highlight] .chart-bar.group-color-20, body[data-highlight] .chart-bar.group-color-21, body[data-highlight] .chart-bar.group-color-22, body[data-highlight] .chart-bar.group-color-23, body[data-highlight] .chart-bar.group-color-24, body[data-highlight] .chart-bar.group-color-25, body[data-highlight] .chart-bar.group-color-26, body[data-highlight] .chart-bar.group-color-27, body[data-highlight] .chart-bar.group-color-28, body[data-highlight] .chart-bar.group-color-29, body[data-highlight] .chart-bar.group-color-30, body[data-highlight] .chart-bar.group-color-none { opacity: 0.25; }
      body[data-highlight~="group-color-1"] .chart-bar.group-color-1 { opacity: 1; stroke: var(--group-1); stroke-width: 1.5px; }
      body[data-highlight~="group-color-2"] .chart-bar.group-color-2 { opacity: 1; stroke: var(--group-2); stroke-width: 1.5px; }
      body[data-highlight~="group-color-3"] .chart-bar.group-color-3 { opacity: 1; stroke: var(--group-3); stroke-width: 1.5px; }
      body[data-highlight~="group-color-4"] .chart-bar.group-color-4 { opacity: 1; stroke: var(--group-4); stroke-width: 1.5px; }
      body[data-highlight~="group-color-5"] .chart-bar.group-color-5 { opacity: 1; stroke: var(--group-5); stroke-width: 1.5px; }
      body[data-highlight~="group-color-6"] .chart-bar.group-color-6 { opacity: 1; stroke: var(--group-6); stroke-width: 1.5px; }
      body[data-highlight~="group-color-7"] .chart-bar.group-color-7 { opacity: 1; stroke: var(--group-7); stroke-width: 1.5px; }
      body[data-highlight~="group-color-8"] .chart-bar.group-color-8 { opacity: 1; stroke: var(--group-8); stroke-width: 1.5px; }
      body[data-highlight~="group-color-9"] .chart-bar.group-color-9 { opacity: 1; stroke: var(--group-9); stroke-width: 1.5px; }
      body[data-highlight~="group-color-10"] .chart-bar.group-color-10 { opacity: 1; stroke: var(--group-10); stroke-width: 1.5px; }
      body[data-highlight~="group-color-11"] .chart-bar.group-color-11 { opacity: 1; stroke: var(--group-11); stroke-width: 1.5px; }
      body[data-highlight~="group-color-12"] .chart-bar.group-color-12 { opacity: 1; stroke: var(--group-12); stroke-width: 1.5px; }
      body[data-highlight~="group-color-13"] .chart-bar.group-color-13 { opacity: 1; stroke: var(--group-13); stroke-width: 1.5px; }
      body[data-highlight~="group-color-14"] .chart-bar.group-color-14 { opacity: 1; stroke: var(--group-14); stroke-width: 1.5px; }
      body[data-highlight~="group-color-15"] .chart-bar.group-color-15 { opacity: 1; stroke: var(--group-15); stroke-width: 1.5px; }
      body[data-highlight~="group-color-16"] .chart-bar.group-color-16 { opacity: 1; stroke: var(--group-16); stroke-width: 1.5px; }
      body[data-highlight~="group-color-17"] .chart-bar.group-color-17 { opacity: 1; stroke: var(--group-17); stroke-width: 1.5px; }
      body[data-highlight~="group-color-18"] .chart-bar.group-color-18 { opacity: 1; stroke: var(--group-18); stroke-width: 1.5px; }
      body[data-highlight~="group-color-19"] .chart-bar.group-color-19 { opacity: 1; stroke: var(--group-19); stroke-width: 1.5px; }
      body[data-highlight~="group-color-20"] .chart-bar.group-color-20 { opacity: 1; stroke: var(--group-20); stroke-width: 1.5px; }
      body[data-highlight~="group-color-21"] .chart-bar.group-color-21 { opacity: 1; stroke: var(--group-21); stroke-width: 1.5px; }
      body[data-highlight~="group-color-22"] .chart-bar.group-color-22 { opacity: 1; stroke: var(--group-22); stroke-width: 1.5px; }
      body[data-highlight~="group-color-23"] .chart-bar.group-color-23 { opacity: 1; stroke: var(--group-23); stroke-width: 1.5px; }
      body[data-highlight~="group-color-24"] .chart-bar.group-color-24 { opacity: 1; stroke: var(--group-24); stroke-width: 1.5px; }
      body[data-highlight~="group-color-25"] .chart-bar.group-color-25 { opacity: 1; stroke: var(--group-25); stroke-width: 1.5px; }
      body[data-highlight~="group-color-26"] .chart-bar.group-color-26 { opacity: 1; stroke: var(--group-26); stroke-width: 1.5px; }
      body[data-highlight~="group-color-27"] .chart-bar.group-color-27 { opacity: 1; stroke: var(--group-27); stroke-width: 1.5px; }
      body[data-highlight~="group-color-28"] .chart-bar.group-color-28 { opacity: 1; stroke: var(--group-28); stroke-width: 1.5px; }
      body[data-highlight~="group-color-29"] .chart-bar.group-color-29 { opacity: 1; stroke: var(--group-29); stroke-width: 1.5px; }
      body[data-highlight~="group-color-30"] .chart-bar.group-color-30 { opacity: 1; stroke: var(--group-30); stroke-width: 1.5px; }
      body[data-highlight~="group-color-none"] .chart-bar.group-color-none { opacity: 1; stroke: var(--group-none); stroke-width: 1.5px; }
      body[data-highlight~="group-color-1"] .legend-chip:has(.legend-swatch.group-color-1),
      body[data-highlight~="group-color-2"] .legend-chip:has(.legend-swatch.group-color-2),
      body[data-highlight~="group-color-3"] .legend-chip:has(.legend-swatch.group-color-3),
      body[data-highlight~="group-color-4"] .legend-chip:has(.legend-swatch.group-color-4),
      body[data-highlight~="group-color-5"] .legend-chip:has(.legend-swatch.group-color-5),
      body[data-highlight~="group-color-6"] .legend-chip:has(.legend-swatch.group-color-6),
      body[data-highlight~="group-color-7"] .legend-chip:has(.legend-swatch.group-color-7),
      body[data-highlight~="group-color-9"] .legend-chip:has(.legend-swatch.group-color-9),
      body[data-highlight~="group-color-10"] .legend-chip:has(.legend-swatch.group-color-10),
      body[data-highlight~="group-color-11"] .legend-chip:has(.legend-swatch.group-color-11),
      body[data-highlight~="group-color-12"] .legend-chip:has(.legend-swatch.group-color-12),
      body[data-highlight~="group-color-13"] .legend-chip:has(.legend-swatch.group-color-13),
      body[data-highlight~="group-color-14"] .legend-chip:has(.legend-swatch.group-color-14),
      body[data-highlight~="group-color-15"] .legend-chip:has(.legend-swatch.group-color-15),
      body[data-highlight~="group-color-16"] .legend-chip:has(.legend-swatch.group-color-16),
      body[data-highlight~="group-color-17"] .legend-chip:has(.legend-swatch.group-color-17),
      body[data-highlight~="group-color-18"] .legend-chip:has(.legend-swatch.group-color-18),
      body[data-highlight~="group-color-19"] .legend-chip:has(.legend-swatch.group-color-19),
      body[data-highlight~="group-color-20"] .legend-chip:has(.legend-swatch.group-color-20),
      body[data-highlight~="group-color-21"] .legend-chip:has(.legend-swatch.group-color-21),
      body[data-highlight~="group-color-22"] .legend-chip:has(.legend-swatch.group-color-22),
      body[data-highlight~="group-color-23"] .legend-chip:has(.legend-swatch.group-color-23),
      body[data-highlight~="group-color-24"] .legend-chip:has(.legend-swatch.group-color-24),
      body[data-highlight~="group-color-25"] .legend-chip:has(.legend-swatch.group-color-25),
      body[data-highlight~="group-color-26"] .legend-chip:has(.legend-swatch.group-color-26),
      body[data-highlight~="group-color-27"] .legend-chip:has(.legend-swatch.group-color-27),
      body[data-highlight~="group-color-28"] .legend-chip:has(.legend-swatch.group-color-28),
      body[data-highlight~="group-color-29"] .legend-chip:has(.legend-swatch.group-color-29),
      body[data-highlight~="group-color-30"] .legend-chip:has(.legend-swatch.group-color-30) { opacity: 1; font-weight: 700; }
      .proj-total .total-only { display: none; }
      body[data-highlight~="series-1"] .proj-total .total-only.series-1 { display: inline; font-style: italic; fill: var(--series-1); }
      body[data-highlight~="series-2"] .proj-total .total-only.series-2 { display: inline; font-style: italic; fill: var(--series-2); }
      body[data-highlight~="series-3"] .proj-total .total-only.series-3 { display: inline; font-style: italic; fill: var(--series-3); }
      body[data-highlight~="series-4"] .proj-total .total-only.series-4 { display: inline; font-style: italic; fill: var(--series-4); }
      .comp-bar { display: flex; gap: 2px; width: 90px; height: 10px; flex-shrink: 0; background: var(--border); }
      .comp-bar-empty { background: var(--border); }
      .comp-seg.series-1 { background: var(--series-1); }
      .comp-seg.series-2 { background: var(--series-2); }
      .comp-seg.series-3 { background: var(--series-3); }
      .comp-seg.series-4 { background: var(--series-4); }
      .session-row.group-color-1 { background: var(--group-1-tint); border-left: 3px solid var(--group-1); }
      .session-row.group-color-2 { background: var(--group-2-tint); border-left: 3px solid var(--group-2); }
      .session-row.group-color-3 { background: var(--group-3-tint); border-left: 3px solid var(--group-3); }
      .session-row.group-color-4 { background: var(--group-4-tint); border-left: 3px solid var(--group-4); }
      .session-row.group-color-5 { background: var(--group-5-tint); border-left: 3px solid var(--group-5); }
      .session-row.group-color-6 { background: var(--group-6-tint); border-left: 3px solid var(--group-6); }
      .session-row.group-color-7 { background: var(--group-7-tint); border-left: 3px solid var(--group-7); }
      .session-row.group-color-8 { background: var(--group-8-tint); border-left: 3px solid var(--group-8); }
      .session-row.group-color-9 { background: var(--group-9-tint); border-left: 3px solid var(--group-9); }
      .session-row.group-color-10 { background: var(--group-10-tint); border-left: 3px solid var(--group-10); }
      .session-row.group-color-11 { background: var(--group-11-tint); border-left: 3px solid var(--group-11); }
      .session-row.group-color-12 { background: var(--group-12-tint); border-left: 3px solid var(--group-12); }
      .session-row.group-color-13 { background: var(--group-13-tint); border-left: 3px solid var(--group-13); }
      .session-row.group-color-14 { background: var(--group-14-tint); border-left: 3px solid var(--group-14); }
      .session-row.group-color-15 { background: var(--group-15-tint); border-left: 3px solid var(--group-15); }
      .session-row.group-color-16 { background: var(--group-16-tint); border-left: 3px solid var(--group-16); }
      .session-row.group-color-17 { background: var(--group-17-tint); border-left: 3px solid var(--group-17); }
      .session-row.group-color-18 { background: var(--group-18-tint); border-left: 3px solid var(--group-18); }
      .session-row.group-color-19 { background: var(--group-19-tint); border-left: 3px solid var(--group-19); }
      .session-row.group-color-20 { background: var(--group-20-tint); border-left: 3px solid var(--group-20); }
      .session-row.group-color-21 { background: var(--group-21-tint); border-left: 3px solid var(--group-21); }
      .session-row.group-color-22 { background: var(--group-22-tint); border-left: 3px solid var(--group-22); }
      .session-row.group-color-23 { background: var(--group-23-tint); border-left: 3px solid var(--group-23); }
      .session-row.group-color-24 { background: var(--group-24-tint); border-left: 3px solid var(--group-24); }
      .session-row.group-color-25 { background: var(--group-25-tint); border-left: 3px solid var(--group-25); }
      .session-row.group-color-26 { background: var(--group-26-tint); border-left: 3px solid var(--group-26); }
      .session-row.group-color-27 { background: var(--group-27-tint); border-left: 3px solid var(--group-27); }
      .session-row.group-color-28 { background: var(--group-28-tint); border-left: 3px solid var(--group-28); }
      .session-row.group-color-29 { background: var(--group-29-tint); border-left: 3px solid var(--group-29); }
      .session-row.group-color-30 { background: var(--group-30-tint); border-left: 3px solid var(--group-30); }
      .session-row.group-color-none { border-left: 3px solid var(--group-none); }
      .group-header { display: flex; align-items: center; gap: 6px; padding: 6px 10px; font-size: 11px; font-weight: bold; cursor: pointer; border-bottom: 1px solid var(--border); background: var(--surface-1); width: fit-content; min-width: 100%; }
      .group-header.group-color-1 { border-left: 3px solid var(--group-1); }
      .group-header.group-color-2 { border-left: 3px solid var(--group-2); }
      .group-header.group-color-3 { border-left: 3px solid var(--group-3); }
      .group-header.group-color-4 { border-left: 3px solid var(--group-4); }
      .group-header.group-color-5 { border-left: 3px solid var(--group-5); }
      .group-header.group-color-6 { border-left: 3px solid var(--group-6); }
      .group-header.group-color-7 { border-left: 3px solid var(--group-7); }
      .group-header.group-color-8 { border-left: 3px solid var(--group-8); }
      .group-header.group-color-9 { border-left: 3px solid var(--group-9); }
      .group-header.group-color-10 { border-left: 3px solid var(--group-10); }
      .group-header.group-color-11 { border-left: 3px solid var(--group-11); }
      .group-header.group-color-12 { border-left: 3px solid var(--group-12); }
      .group-header.group-color-13 { border-left: 3px solid var(--group-13); }
      .group-header.group-color-14 { border-left: 3px solid var(--group-14); }
      .group-header.group-color-15 { border-left: 3px solid var(--group-15); }
      .group-header.group-color-16 { border-left: 3px solid var(--group-16); }
      .group-header.group-color-17 { border-left: 3px solid var(--group-17); }
      .group-header.group-color-18 { border-left: 3px solid var(--group-18); }
      .group-header.group-color-19 { border-left: 3px solid var(--group-19); }
      .group-header.group-color-20 { border-left: 3px solid var(--group-20); }
      .group-header.group-color-21 { border-left: 3px solid var(--group-21); }
      .group-header.group-color-22 { border-left: 3px solid var(--group-22); }
      .group-header.group-color-23 { border-left: 3px solid var(--group-23); }
      .group-header.group-color-24 { border-left: 3px solid var(--group-24); }
      .group-header.group-color-25 { border-left: 3px solid var(--group-25); }
      .group-header.group-color-26 { border-left: 3px solid var(--group-26); }
      .group-header.group-color-27 { border-left: 3px solid var(--group-27); }
      .group-header.group-color-28 { border-left: 3px solid var(--group-28); }
      .group-header.group-color-29 { border-left: 3px solid var(--group-29); }
      .group-header.group-color-30 { border-left: 3px solid var(--group-30); }
      .group-header.group-color-none { border-left: 3px solid var(--group-none); }
      .group-icon { width: 10px; display: inline-block; }
      .detail-container { flex: 1; }
      .detail-panel { padding: 16px; font-family: monospace; }
      .detail-close { float: right; background: none; border: none; font-size: 18px; cursor: pointer; color: var(--text-muted); line-height: 1; padding: 0; }
      .detail-close:hover { color: var(--text-primary); }
      .subagent-row { display: grid; grid-template-columns: 0.6fr minmax(200px, 1.6fr) 0.9fr 0.6fr 0.7fr; padding: 4px 8px; border-bottom: 1px solid var(--border); }
      .subagent-row > span:not(:last-child) { border-right: 1px solid var(--border); padding-right: 8px; }
      .subagent-row > span:nth-child(2) { white-space: normal; word-break: break-word; }
      .subagent-title { font-size: 12px; margin: 12px 0 6px; color: var(--text-secondary); }
      .subagent-row.subagent-header { font-weight: bold; color: var(--text-muted); font-size: 10px; border-bottom: 2px solid var(--border); }
      .subagent-row-odd { background: rgba(0,0,0,0.02); }
      .empty { color: var(--text-muted); }
    </style>
    """
    script = """
    <script>
    function showSession(id) {
      var row = document.getElementById('row-' + id);
      if (row) {
        var groupId = row.getAttribute('data-group');
        var header = document.getElementById('group-' + groupId);
        if (header && header.classList.contains('collapsed')) { toggleGroup(parseInt(groupId, 10)); }
      }
      document.querySelectorAll('.detail-panel').forEach(function(el) { el.style.display = 'none'; });
      document.querySelectorAll('.session-row').forEach(function(el) { el.classList.remove('active'); });
      var panel = document.getElementById('detail-' + id);
      if (panel) { panel.style.display = 'block'; }
      if (row) { row.classList.add('active'); }
    }
    function closeDetail(id) {
      var panel = document.getElementById('detail-' + id);
      if (panel) { panel.style.display = 'none'; }
      var row = document.getElementById('row-' + id);
      if (row) { row.classList.remove('active'); }
    }
    function showTab(name) {
      document.querySelectorAll('.tab-pane').forEach(function(el) { el.classList.remove('active'); });
      // Scoped to .tab-bar's own buttons - .trend-mode-tabs reuses the
      // same .tab-btn class for its visual style and has its own
      // independent active state (showTrendMode below).
      document.querySelectorAll('.tab-bar .tab-btn').forEach(function(el) { el.classList.remove('active'); });
      var pane = document.getElementById('tab-' + name);
      var btn = document.getElementById('tab-btn-' + name);
      if (pane) { pane.classList.add('active'); }
      if (btn) { btn.classList.add('active'); }
    }
    function showTrendMode(name) {
      document.querySelectorAll('.trend-pane').forEach(function(el) { el.classList.remove('active'); });
      document.querySelectorAll('.trend-mode-tabs .tab-btn').forEach(function(el) { el.classList.remove('active'); });
      var pane = document.getElementById('trend-pane-' + name);
      var btn = document.getElementById('trend-btn-' + name);
      if (pane) { pane.classList.add('active'); }
      if (btn) { btn.classList.add('active'); }
    }
    function toggleProjectsMore() {
      var svg = document.getElementById('project-chart-more');
      var toggle = document.getElementById('project-chart-toggle');
      if (!svg) { return; }
      var hidden = svg.style.display === 'none';
      svg.style.display = hidden ? 'block' : 'none';
      if (toggle) { toggle.classList.toggle('expanded', hidden); }
    }
    function toggleGroup(id) {
      var header = document.getElementById('group-' + id);
      if (!header) { return; }
      var collapsed = header.classList.contains('collapsed');
      var rows = document.querySelectorAll('.session-row[data-group="' + id + '"]');
      rows.forEach(function(el) { el.style.display = collapsed ? 'flex' : 'none'; });
      header.classList.toggle('collapsed');
      var icon = header.querySelector('.group-icon');
      if (icon) { icon.textContent = collapsed ? '▾' : '▸'; }
    }
    // Shared by highlightTokenType (token-type legend/bars) and
    // highlightProject (por-projeto legend/bars, 2026-07-21) - the two
    // never show on screen at once (only one trend-mode-tab is visible),
    // so they can safely share the one data-highlight attribute/CSS
    // instead of needing two independent highlight systems.
    function toggleHighlight(cls, event, allClasses) {
      var body = document.body;
      var current = body.getAttribute('data-highlight');
      var currentSet = current ? current.split(' ') : [];
      // shift = select every option at once; ctrl/cmd = remove just this
      // one from whatever's selected; plain click = single-select toggle.
      if (event && event.shiftKey) {
        currentSet = allClasses.slice();
      } else if (event && (event.ctrlKey || event.metaKey)) {
        var index = currentSet.indexOf(cls);
        if (index !== -1) { currentSet.splice(index, 1); }
      } else {
        currentSet = (currentSet.length === 1 && currentSet[0] === cls) ? [] : [cls];
      }
      if (currentSet.length === 0) {
        body.removeAttribute('data-highlight');
      } else {
        body.setAttribute('data-highlight', currentSet.join(' '));
      }
    }
    function highlightTokenType(type, event) {
      toggleHighlight(type, event, ['series-1', 'series-2', 'series-3', 'series-4']);
    }
    function highlightProject(groupClass, event) {
      // The set of group-color-N classes actually in play varies by
      // project count, so it's read from the currently-rendered
      // "por projeto" legend rather than hardcoded like token types are.
      var swatches = document.querySelectorAll('#trend-pane-pane .legend-swatch');
      var allClasses = Array.from(swatches).map(function(el) {
        return Array.from(el.classList).filter(function(c) { return c.indexOf('group-color') === 0; })[0];
      }).filter(Boolean);
      toggleHighlight(groupClass, event, allClasses);
    }
    // Hover previews the same highlight a click would commit (2026-07-21)
    // - mouseenter shows it, mouseleave restores whatever was selected
    // before the hover started. A real click still goes through
    // highlightTokenType/highlightProject above and persists normally.
    var _highlightBeforeHover = null;
    function previewHighlight(cls) {
      if (_highlightBeforeHover === null) {
        _highlightBeforeHover = document.body.getAttribute('data-highlight') || '';
      }
      document.body.setAttribute('data-highlight', cls);
    }
    function endPreviewHighlight() {
      if (_highlightBeforeHover !== null) {
        if (_highlightBeforeHover) {
          document.body.setAttribute('data-highlight', _highlightBeforeHover);
        } else {
          document.body.removeAttribute('data-highlight');
        }
        _highlightBeforeHover = null;
      }
    }
    document.querySelectorAll('.trend-scroll').forEach(function(el) { el.scrollLeft = el.scrollWidth; });
    </script>
    """
    tabs_html = (
        '<div class="tab-bar">'
        '<span class="tab-btn active" id="tab-btn-sessions" onclick="showTab(\'sessions\')">Sessões</span>'
        '<span class="tab-btn" id="tab-btn-panorama" onclick="showTab(\'panorama\')">Panorama</span>'
        "</div>"
    )
    sessions_tab_html = (
        '<div class="tab-pane active" id="tab-sessions">'
        '<div class="main-row">'
        f'<div class="session-list">{render_composition_legend(compute_token_type_totals(records))}{render_session_header()}{"".join(rows_html)}</div>'
        f'<div class="detail-container">{"".join(panels_html)}</div>'
        "</div>"
        "</div>"
    )
    panorama_tab_html = (
        '<div class="tab-pane" id="tab-panorama">'
        f"{project_totals_html}"
        "</div>"
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Claude Code Metrics Dashboard</title>{style}</head>"
        # Opens with every token type already selected (2026-07-21) - the
        # dashboard shows everything unfiltered by default, so the legend
        # should reflect that instead of opening in an unhighlighted state
        # that reads as "nothing selected."
        '<body data-highlight="series-1 series-2 series-3 series-4">'
        f"{trend_html}"
        f"{tabs_html}"
        f"{sessions_tab_html}"
        f"{panorama_tab_html}"
        f"{script}"
        "</body></html>"
    )


def main():
    parser = argparse.ArgumentParser(description="Report on logged Claude Code session metrics.")
    parser.add_argument("--tag", default=None, help="Filter by session tag")
    parser.add_argument("--project", default=None, help="Filter by project path")
    parser.add_argument("-n", "--limit", type=int, default=20, help="Number of most recent sessions to show")
    parser.add_argument("--html", action="store_true", help="Generate an HTML dashboard instead of printing a table")
    parser.add_argument("--days", type=int, default=None, help="Only include sessions from the last N days")
    parser.add_argument(
        "--all", action="store_true",
        help="Show the full history, bypassing the default 30-day active-project window",
    )
    args = parser.parse_args()

    records = load_records(METRICS_FILE)
    records = dedupe_sessions(records)
    records = filter_records(records, tag=args.tag, project=args.project)
    all_time_records = records

    if args.days is not None:
        effective_days = args.days
    elif args.all or not args.html:
        # No explicit --days and no --all: only the --html dashboard
        # defaults to a 30-day active-project window. Terminal-table mode
        # keeps its original behavior (no default period) per this plan's
        # "terminal mode stays behaviorally unchanged" constraint.
        effective_days = None
    else:
        # No explicit --days and no --all, --html requested: default to a
        # 60-day active-project window (2026-07-21: widened from 30 so the
        # chart's per-day width - already 3x'd per earlier feedback -
        # extends further into the past instead of mostly sitting on an
        # unused floor width) rather than the entire history, so the
        # everyday dashboard view stays focused on what's actually being
        # worked on.
        effective_days = 60

    # The chart always covers the full history (2026-07-21: "por que não
    # cobrir mais datas?") - only the session-list table is bounded by
    # --days/-n. chart_records is all_time_records (already has
    # --tag/--project applied, just not the days window), independent of
    # whatever the table's window/limit end up being below.
    chart_records = all_time_records
    records = filter_by_days(records, effective_days)
    records = records[-args.limit:]

    if args.html:
        subagent_records = dedupe_subagents(load_records(SUBAGENTS_FILE))
        agent_label_cache = load_agent_label_cache()
        # Real data has duplicate agent_id/session_id records (the metrics
        # log has no dedup). Deduping to one representative per key before
        # fanning out to the thread pool guarantees each key is processed
        # by exactly one thread - without this, two threads racing on the
        # same key could interleave a real label with a failed one, and
        # negative-caching (get_agent_label/get_task_label above) would
        # then permanently lock in whichever thread wrote last, even if it
        # was the failure. Deduping also avoids redundant subprocess calls
        # for the same underlying record.
        #
        # The representative must be a SOURCE-BEARING duplicate when one
        # exists, not just the first one seen: real data has sessions where
        # the first-seen duplicate lacks internal_name/transcript_path
        # (found during final review) - picking it as representative would
        # generate no label, negative-cache the failure, and silently lose
        # a label another duplicate of the same session could have
        # produced. Falls back to first-seen only when no duplicate has a
        # usable source.
        unique_agents = {}
        for subagent in subagent_records:
            key = subagent.get("agent_id")
            if key is None:
                continue
            existing = unique_agents.get(key)
            if existing is None:
                unique_agents[key] = subagent
            elif not existing.get("agent_transcript_path") and subagent.get("agent_transcript_path"):
                unique_agents[key] = subagent
        # max_workers=1 (fully serial): each worker spawns a full
        # `claude -p ... --model haiku` CLI subprocess (a whole Node
        # process), not a lightweight thread task. Even 3 concurrent
        # subprocesses was still enough to spike load average past 25 and
        # keep growing swap on an 8GB machine during a cold-cache backfill
        # (see PROJECT-LOG 2026-07-20) - serial + `nice` in
        # compress_to_label trades throughput for never fighting the
        # machine, which is the explicit tradeoff wanted for a
        # once-per-history backfill. The cache is also saved every
        # SAVE_EVERY completions, not just at the end - a killed/interrupted
        # run keeps whatever it already labeled instead of repeating the
        # whole cold-cache scan next time.
        SAVE_EVERY = 10
        agent_keys = list(unique_agents.keys())
        label_by_agent_id = {}
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {
                executor.submit(get_agent_label, unique_agents[k], agent_label_cache): k
                for k in agent_keys
            }
            for i, future in enumerate(as_completed(futures), 1):
                k = futures[future]
                label_by_agent_id[k] = future.result()
                if i % SAVE_EVERY == 0:
                    save_agent_label_cache(agent_label_cache)
        for subagent in subagent_records:
            subagent["agent_label"] = label_by_agent_id.get(subagent.get("agent_id"))
        save_agent_label_cache(agent_label_cache)
        subagents_by_session = group_subagents_by_session(subagent_records)

        label_cache = load_label_cache()
        unique_sessions = {}
        for record in records:
            key = record.get("session_id")
            if key is None:
                continue
            existing = unique_sessions.get(key)
            if existing is None:
                unique_sessions[key] = record
            elif not (existing.get("internal_name") or existing.get("transcript_path")) and (
                record.get("internal_name") or record.get("transcript_path")
            ):
                unique_sessions[key] = record
        session_keys = list(unique_sessions.keys())
        label_by_session_id = {}
        with ThreadPoolExecutor(max_workers=1) as executor:
            futures = {
                executor.submit(get_task_label, unique_sessions[k], label_cache): k
                for k in session_keys
            }
            for i, future in enumerate(as_completed(futures), 1):
                k = futures[future]
                label_by_session_id[k] = future.result()
                if i % SAVE_EVERY == 0:
                    save_label_cache(label_cache)
        for record in records:
            record["task_label"] = label_by_session_id.get(record.get("session_id"))
        save_label_cache(label_cache)
        project_first_seen = compute_project_first_seen(all_time_records)
        project_last_seen = compute_project_last_seen(all_time_records)
        open_projects = get_open_projects()
        html_output = render_html(records, subagents_by_session, chart_records, project_first_seen, project_last_seen, open_projects)
        output_path = os.path.expanduser("~/.claude/metrics/dashboard.html")
        with open(output_path, "w") as f:
            f.write(html_output)
        print(output_path)
        return

    print(format_table(records))
    print(format_summary(records))


if __name__ == "__main__":
    main()
