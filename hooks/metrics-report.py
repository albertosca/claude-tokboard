#!/usr/bin/env python3
import argparse
import glob
import html
import json
import os
import subprocess
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
    assignment = {}
    next_slot = 0
    for record in records:
        project = record.get("project_path")
        if project in assignment:
            continue
        if project is None:
            assignment[project] = "group-color-none"
        else:
            slot = (next_slot % 8) + 1
            assignment[project] = f"group-color-{slot}"
            next_slot += 1
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
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


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
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", "haiku"],
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


def get_task_label(record, cache):
    session_id = record.get("session_id")
    if not session_id:
        return None
    if session_id in cache:
        return cache[session_id]

    internal_name = record.get("internal_name")
    if internal_name and len(internal_name.split()) <= 6:
        cache[session_id] = internal_name
        return internal_name

    source = internal_name or find_away_summary(record.get("transcript_path"))
    if not source:
        source = cold_summary_source(record.get("transcript_path"))
    if not source:
        return None

    label = compress_to_label(source)
    if label:
        cache[session_id] = label
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
        return cache[agent_id]

    source = cold_summary_source(subagent_record.get("agent_transcript_path"))
    if not source:
        return None

    label = compress_to_label(source)
    if label:
        cache[agent_id] = label
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


def render_composition_legend(totals):
    return (
        '<div class="comp-legend">'
        f'<span class="legend-chip" onclick="highlightTokenType(\'series-1\')"><span class="legend-swatch series-1"></span>input · '
        f'<span class="legend-total" data-series="series-1">{format_tokens(totals.get("series-1", 0))}</span></span>'
        f'<span class="legend-chip" onclick="highlightTokenType(\'series-2\')"><span class="legend-swatch series-2"></span>output · '
        f'<span class="legend-total" data-series="series-2">{format_tokens(totals.get("series-2", 0))}</span></span>'
        f'<span class="legend-chip" onclick="highlightTokenType(\'series-3\')"><span class="legend-swatch series-3"></span>cache read · '
        f'<span class="legend-total" data-series="series-3">{format_tokens(totals.get("series-3", 0))}</span></span>'
        f'<span class="legend-chip" onclick="highlightTokenType(\'series-4\')"><span class="legend-swatch series-4"></span>cache creation · '
        f'<span class="legend-total" data-series="series-4">{format_tokens(totals.get("series-4", 0))}</span></span>'
        "</div>"
    )


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
        parts.append(
            f'<span class="comp-seg {css_class}" style="width:{pct:.2f}%" '
            f'onclick="highlightTokenType(\'{css_class}\')"></span>'
        )
    return f'<div class="comp-bar">{"".join(parts)}</div>'


def render_session_row(record, is_active, group_colors, group_index):
    session_id = record.get("session_id") or ""
    tag = html.escape(record.get("tag") or "-")
    branch = html.escape(record.get("git_branch") or "-")
    tmux_window = html.escape(record.get("tmux_window") or "-")
    task_label = html.escape(record.get("task_label") or "-")
    date_str = format_date(record.get("timestamp_start"))
    duration = record.get("duration_seconds")
    duration_str = f"{duration:.0f}s" if duration is not None else "-"
    total_tokens = format_tokens((record.get("input_tokens", 0) or 0) + (record.get("output_tokens", 0) or 0))
    active_class = " active" if is_active else ""
    group_class = group_colors.get(record.get("project_path"), "group-color-none")
    return (
        f'<div class="session-row {group_class}{active_class}" id="row-{session_id}" '
        f'data-group="{group_index}" style="display:none" '
        f"onclick=\"showSession('{session_id}')\">"
        f'<span class="sid">{session_id[:12]}</span>'
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
    if not subagents:
        body = '<p class="empty">Nenhum subagente nesta sessão.</p>'
    else:
        title = f'<h4 class="subagent-title">Subagentes despachados ({len(subagents)})</h4>'
        header = (
            '<div class="subagent-row subagent-header">'
            "<span>tarefa</span><span>tokens</span><span>tools</span><span>duração</span>"
            "</div>"
        )
        rows = [header]
        for i, subagent in enumerate(subagents):
            duration = subagent.get("duration_seconds")
            duration_str = f"{duration:.0f}s" if duration is not None else "-"
            agent_type = html.escape(subagent.get("agent_type") or "-")
            agent_label = html.escape(subagent.get("agent_label") or "-")
            in_tok = format_tokens(subagent.get("input_tokens", 0))
            out_tok = format_tokens(subagent.get("output_tokens", 0))
            zebra_class = " subagent-row-odd" if i % 2 == 1 else ""
            rows.append(
                f'<div class="subagent-row{zebra_class}">'
                f"<span>{agent_type} — {agent_label}</span>"
                f'<span>{in_tok}/{out_tok} tok</span>'
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


def aggregate_by_day(records):
    dated = [r for r in records if r.get("timestamp_start")]
    days = {}
    day_order = []
    for r in dated:
        timestamp_start = r.get("timestamp_start")
        try:
            day_key = datetime.fromisoformat(timestamp_start.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
        except (ValueError, AttributeError, TypeError):
            continue
        if day_key not in days:
            days[day_key] = []
            day_order.append(day_key)
        days[day_key].append(r)
    day_order.sort()
    return [{"day": day, "records": days[day]} for day in day_order]


def render_trend_chart(records, group_colors):
    days = aggregate_by_day(records)
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
    candidate_width = max(400, 40 + n * 16)
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
        day_dt = datetime.strptime(days[idx]["day"], "%Y-%m-%d")
        tick_date = day_dt.strftime("%d/%m")
        x_axis.append(
            f'<text x="{x:.1f}" y="{height - 4}" class="chart-axis-label" text-anchor="middle">{tick_date}</text>'
        )
    x_axis_html = "".join(x_axis)

    # Highlight band for the last 7 calendar days present in the data.
    highlight_html = ""
    last_day_dt = datetime.strptime(days[-1]["day"], "%Y-%m-%d")
    highlight_start_idx = None
    for i, day in enumerate(days):
        day_dt = datetime.strptime(day["day"], "%Y-%m-%d")
        if (last_day_dt - day_dt).days < 7:
            highlight_start_idx = i
            break
    if highlight_start_idx is not None and highlight_start_idx > 0:
        hx = padding_left + highlight_start_idx * step
        hwidth = (n - highlight_start_idx) * step
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
                    f'class="chart-bar {group_class}">'
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
                bars.append(
                    f'<rect x="{x:.1f}" y="{y_cursor:.1f}" width="{bar_width:.1f}" height="{seg_height:.1f}" '
                    f'class="chart-bar {css_class}" onclick="highlightTokenType(\'{css_class}\')">'
                    f"<title>{day['day']} - {format_tokens(value)} tokens</title></rect>"
                )
        return "".join(bars)

    pane_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" id="chart-pane" preserveAspectRatio="xMidYMid meet" '
        f'style="width:{width}px;min-width:{width}px">'
        f"{highlight_html}{build_y_axis(max_pane)}{x_axis_html}{bars_for_pane()}</svg>"
    )
    tokens_svg = (
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" id="chart-tokens" preserveAspectRatio="xMidYMid meet" '
        f'style="display:none;width:{width}px;min-width:{width}px">'
        f"{highlight_html}{build_y_axis(max_tokens)}{x_axis_html}{bars_for_tokens()}</svg>"
    )

    # Always scrollable now, not just in the floor-hit (scrollable=True)
    # case: with the width cap removed (this task), a long real history can
    # make the chart genuinely very wide — the outer .trend-scroll container
    # (CSS, always overflow-x:auto) keeps the page itself from breaking, and
    # the auto-scroll-to-end script (below, in render_html) shows the most
    # recent days by default rather than the oldest. data-floor-hit is kept
    # purely as a debugging/testing signal for whether the (now much harder
    # to reach, see the comment on candidate_width above) 8px floor engaged.
    return (
        '<div class="trend-container">'
        '<div class="chart-toggle">'
        '<span class="chart-toggle-btn active" id="toggle-pane" onclick="showChartMode(\'pane\')">por projeto</span>'
        '<span class="chart-toggle-btn" id="toggle-tokens" onclick="showChartMode(\'tokens\')">por tipo de token</span>'
        "</div>"
        f'<div class="trend-scroll" data-floor-hit="{str(scrollable).lower()}">'
        f"{pane_svg}{tokens_svg}"
        "</div>"
        "</div>"
    )


def render_project_totals_chart(records, project_first_seen, project_last_seen, open_projects):
    totals = {}
    for r in records:
        project = r.get("project_path")
        totals[project] = totals.get(project, 0) + (r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0)
    if not totals:
        return ""

    # Sorted by most-recent-activity, not by token volume — answers "what
    # have I touched lately" rather than "what costs the most," and pairs
    # naturally with the top-10-then-collapse cutoff below (the most
    # recently active projects are the ones most likely to matter right now).
    ordered_projects = sorted(totals.keys(), key=lambda p: project_last_seen.get(p, ""), reverse=True)
    max_total = max(totals.values()) or 1

    row_height = 24
    bar_max_width = 300
    label_x = 4
    width = 460

    def render_rows(projects):
        rows = []
        y = 14
        for project in projects:
            total = totals[project]
            label = project_display_label(project)
            title_attr = f' title="{html.escape(project)}"' if project else ""
            bar_width = (total / max_total) * bar_max_width if max_total else 0
            is_open = project in open_projects
            bar_class = "project-color-open" if is_open else "project-color-historical"
            days_running = ""
            first_seen = project_first_seen.get(project)
            if first_seen:
                try:
                    first_dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00")).astimezone()
                    days_running = f" · há {(datetime.now().astimezone() - first_dt).days} dias"
                except (ValueError, AttributeError, TypeError):
                    days_running = ""
            rows.append(
                f'<text x="{label_x}" y="{y}" class="project-chart-label"{title_attr}>{html.escape(label)}</text>'
                f'<rect x="{label_x}" y="{y + 6}" width="{bar_max_width}" height="14" class="project-chart-track" />'
                f'<rect x="{label_x}" y="{y + 6}" width="{bar_width:.1f}" height="14" class="project-chart-bar {bar_class}" />'
                f'<text x="{label_x + bar_max_width + 6}" y="{y + 17}" class="chart-axis-label">{format_tokens(total)}{days_running}</text>'
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

    legend = (
        '<div class="project-chart-legend">'
        '<span class="legend-chip"><span class="legend-swatch project-color-historical"></span>histórico</span>'
        '<span class="legend-chip"><span class="legend-swatch project-color-open"></span>sessão aberta agora</span>'
        "</div>"
    )
    return (
        '<div class="project-chart-container">'
        '<h3 class="project-chart-title">Total de tokens por projeto</h3>'
        f"{visible_svg}"
        f"{more_html}"
        f"{legend}"
        "</div>"
    )


def render_html(records, subagents_by_session, chart_records, project_first_seen, project_last_seen, open_projects):
    group_colors = assign_group_colors(chart_records)
    groups = build_project_groups(records, group_colors)
    rows_html = []
    panels_html = []
    for group_index, group in enumerate(groups):
        rows_html.append(render_group_header(group_index, group))
        for record in group["records"]:
            session_id = record.get("session_id") or ""
            rows_html.append(render_session_row(record, False, group_colors, group_index))
            panels_html.append(
                render_detail_panel(session_id, subagents_by_session.get(session_id, []), False)
            )

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
          --group-none: #898781; --group-none-tint: transparent;
        }
      }
      .trend-container { padding: 12px 16px; border-bottom: 1px solid var(--border); }
      .trend-scroll { overflow-x: auto; max-width: 100%; }
      .trend-svg { height: auto; display: block; margin: 0 auto; }
      .chart-highlight-band { fill: var(--series-1); opacity: 0.06; }
      .project-chart-container { padding: 12px 16px; border-bottom: 1px solid var(--border); }
      .project-chart-title { font-size: 13px; margin: 0 0 8px; color: var(--text-secondary); }
      .project-chart-svg { width: 100%; max-width: 600px; display: block; }
      .project-chart-label { font-size: 10px; fill: var(--text-primary); }
      .project-chart-track { fill: var(--border); }
      .project-chart-bar.project-color-historical { fill: var(--series-1); }
      .project-chart-bar.project-color-open { fill: var(--series-2); }
      .project-chart-legend { display: flex; gap: 14px; padding: 6px 0 0; font-size: 10px; color: var(--text-secondary); }
      .project-chart-toggle { font-size: 11px; color: var(--series-1); cursor: pointer; padding: 4px 0; }
      .project-chart-toggle:hover { text-decoration: underline; }
      .legend-swatch.project-color-historical { background: var(--series-1); }
      .legend-swatch.project-color-open { background: var(--series-2); }
      .chart-toggle { display: flex; gap: 6px; padding: 0 0 6px; font-size: 11px; }
      .chart-toggle-btn { padding: 3px 8px; border-radius: 4px; cursor: pointer; color: var(--text-muted); }
      .chart-toggle-btn.active { background: rgba(42, 120, 214, 0.15); border: 1px solid var(--series-1); color: var(--text-primary); }
      .chart-gridline { stroke: var(--border); stroke-width: 1; }
      .chart-axis-label { fill: var(--text-muted); font-size: 8px; }
      .chart-bar { cursor: pointer; }
      .chart-bar.group-color-1 { fill: var(--group-1); }
      .chart-bar.group-color-2 { fill: var(--group-2); }
      .chart-bar.group-color-3 { fill: var(--group-3); }
      .chart-bar.group-color-4 { fill: var(--group-4); }
      .chart-bar.group-color-5 { fill: var(--group-5); }
      .chart-bar.group-color-6 { fill: var(--group-6); }
      .chart-bar.group-color-7 { fill: var(--group-7); }
      .chart-bar.group-color-8 { fill: var(--group-8); }
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
      .session-list { width: 62%; border-right: 1px solid var(--border); max-height: calc(100vh - 60px); overflow-y: auto; overflow-x: auto; font-family: monospace; }
      .session-row { display: flex; align-items: center; gap: 8px; padding: 6px 10px; cursor: pointer; border-bottom: 1px solid var(--border); font-size: 12px; }
      .session-row:hover { background: rgba(42, 120, 214, 0.08); }
      .session-row.active { background: rgba(42, 120, 214, 0.16); font-weight: bold; }
      .session-row .sid { color: var(--text-muted); width: 90px; flex-shrink: 0; }
      .session-row .tokens-total { color: var(--text-secondary); width: 55px; flex-shrink: 0; text-align: right; }
      .session-row .tmux-window { width: 90px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .session-header { position: sticky; top: 0; z-index: 1; background: var(--surface-1); font-weight: bold; color: var(--text-muted); font-size: 10px; cursor: default; border-bottom: 2px solid var(--border); }
      .session-header:hover { background: var(--surface-1); }
      .session-row .date-col { width: 65px; flex-shrink: 0; }
      .session-row .label-col { width: 160px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .session-row .tag-col { width: 50px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .session-row .branch-col { width: 70px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .session-row .duration-col { width: 55px; flex-shrink: 0; text-align: right; }
      .session-row .comp-header { width: 90px; flex-shrink: 0; }
      .comp-legend { display: flex; gap: 14px; padding: 8px 10px; font-size: 11px; color: var(--text-secondary); border-bottom: 1px solid var(--border); font-family: monospace; }
      .legend-chip { display: inline-flex; align-items: center; }
      .legend-swatch { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; }
      .legend-swatch.series-1 { background: var(--series-1); }
      .legend-swatch.series-2 { background: var(--series-2); }
      .legend-swatch.series-3 { background: var(--series-3); }
      .legend-swatch.series-4 { background: var(--series-4); }
      .legend-total { font-weight: 600; }
      body[data-highlight] .legend-total { opacity: 0.4; font-weight: 400; }
      body[data-highlight="series-1"] .legend-total[data-series="series-1"],
      body[data-highlight="series-2"] .legend-total[data-series="series-2"],
      body[data-highlight="series-3"] .legend-total[data-series="series-3"],
      body[data-highlight="series-4"] .legend-total[data-series="series-4"] { opacity: 1; font-weight: 700; font-size: 13px; }
      body[data-highlight="series-1"] .comp-seg:not(.series-1), body[data-highlight="series-1"] .chart-bar.series-2, body[data-highlight="series-1"] .chart-bar.series-3, body[data-highlight="series-1"] .chart-bar.series-4 { opacity: 0.25; }
      body[data-highlight="series-2"] .comp-seg:not(.series-2), body[data-highlight="series-2"] .chart-bar.series-1, body[data-highlight="series-2"] .chart-bar.series-3, body[data-highlight="series-2"] .chart-bar.series-4 { opacity: 0.25; }
      body[data-highlight="series-3"] .comp-seg:not(.series-3), body[data-highlight="series-3"] .chart-bar.series-1, body[data-highlight="series-3"] .chart-bar.series-2, body[data-highlight="series-3"] .chart-bar.series-4 { opacity: 0.25; }
      body[data-highlight="series-4"] .comp-seg:not(.series-4), body[data-highlight="series-4"] .chart-bar.series-1, body[data-highlight="series-4"] .chart-bar.series-2, body[data-highlight="series-4"] .chart-bar.series-3 { opacity: 0.25; }
      body[data-highlight="series-1"] .comp-seg.series-1 { outline: 1px solid var(--series-1); outline-offset: 1px; }
      body[data-highlight="series-2"] .comp-seg.series-2 { outline: 1px solid var(--series-2); outline-offset: 1px; }
      body[data-highlight="series-3"] .comp-seg.series-3 { outline: 1px solid var(--series-3); outline-offset: 1px; }
      body[data-highlight="series-4"] .comp-seg.series-4 { outline: 1px solid var(--series-4); outline-offset: 1px; }
      body[data-highlight="series-1"] .chart-bar.series-1 { stroke: var(--series-1); stroke-width: 1.5px; }
      body[data-highlight="series-2"] .chart-bar.series-2 { stroke: var(--series-2); stroke-width: 1.5px; }
      body[data-highlight="series-3"] .chart-bar.series-3 { stroke: var(--series-3); stroke-width: 1.5px; }
      body[data-highlight="series-4"] .chart-bar.series-4 { stroke: var(--series-4); stroke-width: 1.5px; }
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
      .session-row.group-color-none { border-left: 3px solid var(--group-none); }
      .group-header { display: flex; align-items: center; gap: 6px; padding: 6px 10px; font-size: 11px; font-weight: bold; cursor: pointer; border-bottom: 1px solid var(--border); background: var(--surface-1); }
      .group-header.group-color-1 { border-left: 3px solid var(--group-1); }
      .group-header.group-color-2 { border-left: 3px solid var(--group-2); }
      .group-header.group-color-3 { border-left: 3px solid var(--group-3); }
      .group-header.group-color-4 { border-left: 3px solid var(--group-4); }
      .group-header.group-color-5 { border-left: 3px solid var(--group-5); }
      .group-header.group-color-6 { border-left: 3px solid var(--group-6); }
      .group-header.group-color-7 { border-left: 3px solid var(--group-7); }
      .group-header.group-color-8 { border-left: 3px solid var(--group-8); }
      .group-header.group-color-none { border-left: 3px solid var(--group-none); }
      .group-icon { width: 10px; display: inline-block; }
      .detail-container { flex: 1; }
      .detail-panel { padding: 16px; font-family: monospace; }
      .detail-close { float: right; background: none; border: none; font-size: 18px; cursor: pointer; color: var(--text-muted); line-height: 1; padding: 0; }
      .detail-close:hover { color: var(--text-primary); }
      .subagent-row { display: grid; grid-template-columns: 1.6fr 0.9fr 0.6fr 0.7fr; padding: 4px 8px; border-bottom: 1px solid var(--border); }
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
      document.querySelectorAll('.tab-btn').forEach(function(el) { el.classList.remove('active'); });
      var pane = document.getElementById('tab-' + name);
      var btn = document.getElementById('tab-btn-' + name);
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
    function highlightTokenType(type) {
      var body = document.body;
      var current = body.getAttribute('data-highlight');
      if (current === type) {
        body.removeAttribute('data-highlight');
      } else {
        body.setAttribute('data-highlight', type);
      }
    }
    function showChartMode(mode) {
      var pane = document.getElementById('chart-pane');
      var tokens = document.getElementById('chart-tokens');
      if (pane) { pane.style.display = mode === 'pane' ? 'block' : 'none'; }
      if (tokens) { tokens.style.display = mode === 'tokens' ? 'block' : 'none'; }
      var paneBtn = document.getElementById('toggle-pane');
      var tokensBtn = document.getElementById('toggle-tokens');
      if (paneBtn) { paneBtn.classList.toggle('active', mode === 'pane'); }
      if (tokensBtn) { tokensBtn.classList.toggle('active', mode === 'tokens'); }
    }
    document.querySelectorAll('.trend-scroll').forEach(function(el) {
      el.scrollLeft = el.scrollWidth;
    });
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
        f"{trend_html}"
        f"{project_totals_html}"
        "</div>"
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Claude Code Metrics Dashboard</title>{style}</head>"
        "<body>"
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
        # 30-day active-project window rather than the entire history, so
        # the everyday dashboard view stays focused on what's actually
        # being worked on.
        effective_days = 30

    records = filter_by_days(records, effective_days)
    if effective_days is None:
        # No period in effect (either --all, or an explicit --days that
        # somehow resolved to None — can't currently happen, but the
        # bound-to--n fallback is the safe default either way): the chart
        # shouldn't render the entire unbounded history — fall back to the
        # same -n-limited set the table shows.
        records = records[-args.limit:]
        chart_records = records
    else:
        # A period IS in effect (the new 30-day default, or an explicit
        # --days): the chart reflects the whole period, uncapped by -n.
        chart_records = records
        records = records[-args.limit:]

    if args.html:
        subagent_records = load_records(SUBAGENTS_FILE)
        agent_label_cache = load_agent_label_cache()
        for subagent in subagent_records:
            subagent["agent_label"] = get_agent_label(subagent, agent_label_cache)
        save_agent_label_cache(agent_label_cache)
        subagents_by_session = group_subagents_by_session(subagent_records)
        label_cache = load_label_cache()
        for record in records:
            record["task_label"] = get_task_label(record, label_cache)
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
