#!/usr/bin/env python3
import argparse
import html
import json
import os
import subprocess
from datetime import datetime

METRICS_FILE = os.path.expanduser("~/.claude/metrics/sessions.jsonl")
SUBAGENTS_FILE = os.path.expanduser("~/.claude/metrics/subagents.jsonl")
TASK_LABELS_FILE = os.path.expanduser("~/.claude/metrics/task_labels.json")


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


def assign_window_colors(records):
    assignment = {}
    next_slot = 0
    for record in records:
        window = record.get("tmux_window")
        if window in assignment:
            continue
        if window is None:
            assignment[window] = "window-color-none"
        else:
            slot = (next_slot % 8) + 1
            assignment[window] = f"window-color-{slot}"
            next_slot += 1
    return assignment


def group_records_by_window(records):
    ordered_desc = list(reversed(records))
    groups = {}
    window_order = []
    for record in ordered_desc:
        window = record.get("tmux_window")
        if window not in groups:
            groups[window] = []
            window_order.append(window)
        groups[window].append(record)
    grouped = []
    for window in window_order:
        grouped.extend(groups[window])
    return grouped


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


def render_composition_legend():
    return (
        '<div class="comp-legend">'
        '<span class="legend-chip"><span class="legend-swatch series-1"></span>input</span>'
        '<span class="legend-chip"><span class="legend-swatch series-2"></span>output</span>'
        '<span class="legend-chip"><span class="legend-swatch series-3"></span>cache read</span>'
        '<span class="legend-chip"><span class="legend-swatch series-4"></span>cache creation</span>'
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
        parts.append(f'<span class="comp-seg {css_class}" style="width:{pct:.2f}%"></span>')
    return f'<div class="comp-bar">{"".join(parts)}</div>'


def render_session_row(record, is_active, window_colors):
    session_id = record.get("session_id") or ""
    tag = html.escape(record.get("tag") or "-")
    branch = html.escape(record.get("git_branch") or "-")
    tmux_window_raw = record.get("tmux_window")
    tmux_window = html.escape(tmux_window_raw or "-")
    task_label = html.escape(record.get("task_label") or "-")
    date_str = format_date(record.get("timestamp_start"))
    duration = record.get("duration_seconds")
    duration_str = f"{duration:.0f}s" if duration is not None else "-"
    total_tokens = format_tokens((record.get("input_tokens", 0) or 0) + (record.get("output_tokens", 0) or 0))
    active_class = " active" if is_active else ""
    window_class = window_colors.get(tmux_window_raw, "window-color-none")
    return (
        f'<div class="session-row {window_class}{active_class}" id="row-{session_id}" '
        f"onclick=\"showSession('{session_id}')\">"
        f'<span class="sid">{session_id[:12]}</span>'
        f"<span>{date_str}</span>"
        f'<span class="tmux-window">{tmux_window}</span>'
        f"<span>{task_label}</span>"
        f"<span>{tag}</span><span>{branch}</span>"
        f"<span>{duration_str}</span>"
        f'<span class="tokens-total">{total_tokens}</span>'
        f"{render_composition_bar(record)}"
        f"</div>"
    )


def render_detail_panel(session_id, subagents, is_active):
    display = "block" if is_active else "none"
    if not subagents:
        body = '<p class="empty">Nenhum subagente nesta sessão.</p>'
    else:
        rows = []
        for subagent in subagents:
            duration = subagent.get("duration_seconds")
            duration_str = f"{duration:.0f}s" if duration is not None else "-"
            rows.append(
                '<div class="subagent-row">'
                f'<span>{subagent.get("agent_type", "-")}</span>'
                f"<span>{duration_str}</span>"
                f'<span>{subagent.get("input_tokens", 0)}/{subagent.get("output_tokens", 0)} tok</span>'
                f'<span>{subagent.get("tool_call_count", 0)} tools</span>'
                "</div>"
            )
        body = "\n".join(rows)
    return (
        f'<div class="detail-panel" id="detail-{session_id}" style="display:{display}">'
        f"<h3>{session_id}</h3>{body}</div>"
    )


def render_trend_line(records):
    ordered = [r for r in records if r.get("timestamp_start")]
    ordered.sort(key=lambda r: r["timestamp_start"])
    if len(ordered) < 2:
        return ""

    totals = [(r.get("input_tokens", 0) or 0) + (r.get("output_tokens", 0) or 0) for r in ordered]
    max_total = max(totals) or 1

    width = 800
    height = 120
    padding = 10
    n = len(ordered)
    step = (width - 2 * padding) / (n - 1)

    points = []
    for i, total in enumerate(totals):
        x = padding + i * step
        y = height - padding - (total / max_total) * (height - 2 * padding)
        points.append((x, y))

    polyline_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)

    circles = []
    for (x, y), record, total in zip(points, ordered, totals):
        session_id = record.get("session_id") or ""
        label = format_tokens(total)
        date_str = format_date(record.get("timestamp_start"))
        circles.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="trend-point" '
            f"onclick=\"showSession('{session_id}')\">"
            f"<title>{date_str} - {label} tokens</title></circle>"
        )

    return (
        '<div class="trend-container">'
        f'<svg viewBox="0 0 {width} {height}" class="trend-svg" preserveAspectRatio="none">'
        f'<polyline points="{polyline_points}" class="trend-line" fill="none" />'
        f"{''.join(circles)}"
        "</svg></div>"
    )


def render_html(records, subagents_by_session):
    window_colors = assign_window_colors(records)
    ordered = group_records_by_window(records)
    rows_html = []
    panels_html = []
    for i, record in enumerate(ordered):
        session_id = record.get("session_id") or ""
        is_active = i == 0
        rows_html.append(render_session_row(record, is_active, window_colors))
        panels_html.append(
            render_detail_panel(session_id, subagents_by_session.get(session_id, []), is_active)
        )

    trend_html = render_trend_line(records)

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
        --window-1: #2a78d6; --window-1-tint: rgba(42,120,214,0.08);
        --window-2: #1baf7a; --window-2-tint: rgba(27,175,122,0.08);
        --window-3: #eda100; --window-3-tint: rgba(237,161,0,0.08);
        --window-4: #008300; --window-4-tint: rgba(0,131,0,0.08);
        --window-5: #4a3aa7; --window-5-tint: rgba(74,58,167,0.08);
        --window-6: #e34948; --window-6-tint: rgba(227,73,72,0.08);
        --window-7: #e87ba4; --window-7-tint: rgba(232,123,164,0.08);
        --window-8: #eb6834; --window-8-tint: rgba(235,104,52,0.08);
        --window-none: #898781; --window-none-tint: transparent;
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
          --window-1: #3987e5; --window-1-tint: rgba(57,135,229,0.12);
          --window-2: #199e70; --window-2-tint: rgba(25,158,112,0.12);
          --window-3: #c98500; --window-3-tint: rgba(201,133,0,0.12);
          --window-4: #008300; --window-4-tint: rgba(0,131,0,0.12);
          --window-5: #9085e9; --window-5-tint: rgba(144,133,233,0.12);
          --window-6: #e66767; --window-6-tint: rgba(230,103,103,0.12);
          --window-7: #d55181; --window-7-tint: rgba(213,81,129,0.12);
          --window-8: #d95926; --window-8-tint: rgba(217,89,38,0.12);
          --window-none: #898781; --window-none-tint: transparent;
        }
      }
      .trend-container { padding: 12px 16px; border-bottom: 1px solid var(--border); }
      .trend-svg { width: 100%; height: 120px; display: block; }
      .trend-line { stroke: var(--series-1); stroke-width: 2; }
      .trend-point { fill: var(--series-1); cursor: pointer; }
      body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; background: var(--surface-1); color: var(--text-primary); }
      .main-row { display: flex; }
      .session-list { width: 46%; border-right: 1px solid var(--border); max-height: calc(100vh - 20px); overflow-y: auto; font-family: monospace; }
      .session-row { display: flex; align-items: center; gap: 8px; padding: 6px 10px; cursor: pointer; border-bottom: 1px solid var(--border); font-size: 12px; }
      .session-row:hover { background: rgba(42, 120, 214, 0.08); }
      .session-row.active { background: rgba(42, 120, 214, 0.16); font-weight: bold; }
      .session-row .sid { color: var(--text-muted); width: 90px; flex-shrink: 0; }
      .session-row .tokens-total { color: var(--text-secondary); width: 55px; flex-shrink: 0; text-align: right; }
      .session-row .tmux-window { width: 90px; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .comp-legend { display: flex; gap: 14px; padding: 8px 10px; font-size: 11px; color: var(--text-secondary); border-bottom: 1px solid var(--border); font-family: monospace; }
      .legend-swatch { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 4px; }
      .legend-swatch.series-1 { background: var(--series-1); }
      .legend-swatch.series-2 { background: var(--series-2); }
      .legend-swatch.series-3 { background: var(--series-3); }
      .legend-swatch.series-4 { background: var(--series-4); }
      .comp-bar { display: flex; gap: 2px; width: 90px; height: 10px; flex-shrink: 0; background: var(--border); }
      .comp-bar-empty { background: var(--border); }
      .comp-seg.series-1 { background: var(--series-1); }
      .comp-seg.series-2 { background: var(--series-2); }
      .comp-seg.series-3 { background: var(--series-3); }
      .comp-seg.series-4 { background: var(--series-4); }
      .session-row.window-color-1 { background: var(--window-1-tint); border-left: 3px solid var(--window-1); }
      .session-row.window-color-2 { background: var(--window-2-tint); border-left: 3px solid var(--window-2); }
      .session-row.window-color-3 { background: var(--window-3-tint); border-left: 3px solid var(--window-3); }
      .session-row.window-color-4 { background: var(--window-4-tint); border-left: 3px solid var(--window-4); }
      .session-row.window-color-5 { background: var(--window-5-tint); border-left: 3px solid var(--window-5); }
      .session-row.window-color-6 { background: var(--window-6-tint); border-left: 3px solid var(--window-6); }
      .session-row.window-color-7 { background: var(--window-7-tint); border-left: 3px solid var(--window-7); }
      .session-row.window-color-8 { background: var(--window-8-tint); border-left: 3px solid var(--window-8); }
      .session-row.window-color-none { border-left: 3px solid var(--window-none); }
      .detail-container { flex: 1; }
      .detail-panel { padding: 16px; font-family: monospace; }
      .subagent-row { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; padding: 4px 0; border-bottom: 1px solid var(--border); }
      .empty { color: var(--text-muted); }
    </style>
    """
    script = """
    <script>
    function showSession(id) {
      document.querySelectorAll('.detail-panel').forEach(function(el) { el.style.display = 'none'; });
      document.querySelectorAll('.session-row').forEach(function(el) { el.classList.remove('active'); });
      var panel = document.getElementById('detail-' + id);
      if (panel) { panel.style.display = 'block'; }
      var row = document.getElementById('row-' + id);
      if (row) { row.classList.add('active'); }
    }
    </script>
    """
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>Claude Code Metrics Dashboard</title>{style}</head>"
        "<body>"
        f"{trend_html}"
        '<div class="main-row">'
        f'<div class="session-list">{render_composition_legend()}{"".join(rows_html)}</div>'
        f'<div class="detail-container">{"".join(panels_html)}</div>'
        "</div>"
        f"{script}"
        "</body></html>"
    )


def main():
    parser = argparse.ArgumentParser(description="Report on logged Claude Code session metrics.")
    parser.add_argument("--tag", default=None, help="Filter by session tag")
    parser.add_argument("--project", default=None, help="Filter by project path")
    parser.add_argument("-n", "--limit", type=int, default=20, help="Number of most recent sessions to show")
    parser.add_argument("--html", action="store_true", help="Generate an HTML dashboard instead of printing a table")
    args = parser.parse_args()

    records = load_records(METRICS_FILE)
    records = filter_records(records, tag=args.tag, project=args.project)
    records = records[-args.limit:]

    if args.html:
        subagent_records = load_records(SUBAGENTS_FILE)
        subagents_by_session = group_subagents_by_session(subagent_records)
        label_cache = load_label_cache()
        for record in records:
            record["task_label"] = get_task_label(record, label_cache)
        save_label_cache(label_cache)
        html_output = render_html(records, subagents_by_session)
        output_path = os.path.expanduser("~/.claude/metrics/dashboard.html")
        with open(output_path, "w") as f:
            f.write(html_output)
        print(output_path)
        return

    print(format_table(records))
    print(format_summary(records))


if __name__ == "__main__":
    main()
