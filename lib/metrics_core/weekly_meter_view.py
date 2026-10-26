"""The weekly meter as strings and percentages, shared by both dashboards.

Both interfaces render this dict and nothing else, so they cannot disagree
about a number, a label or the state wording.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cache_causes import compute_cache_causes
from .cache_causes_view import describe_cache_causes
from .calls_store import CALLS_FILE_NAME, LIMIT_HITS_FILE_NAME, read_calls, read_limit_hits
from .format import format_tokens
from .i18n import session_date, t, tn
from .projects import color_group_key, project_display_label
from .records import load_records
from .weekly_windows import compute_weekly_meter

# Rows in the recent-windows table.
RECENT_WINDOWS = 8
# Room above the larger of the value and the range maximum, so the marker is
# never pinned to the edge of the bar.
SCALE_HEADROOM = 1.1


def points_label(points: int, lang: str) -> str:
    return tn("meter.points", points, lang)


def _day(epoch: float, lang: str) -> str:
    return session_date(datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(), lang)


def _tiers(five_minute: int, one_hour: int, lang: str) -> str:
    return t(
        "meter.tiers",
        lang,
        five_minute=format_tokens(five_minute),
        one_hour=format_tokens(one_hour),
    )


def _families(window: dict[str, Any], lang: str) -> str:
    """ "opus 93.2M · sonnet 12M": the window's cache creation by model family."""
    parts = [
        f"{t('others', lang) if family == 'other' else family} {format_tokens(total)}"
        for family, total in window["families"].items()
    ]
    return " · ".join(parts) if parts else "-"


def _row(window: dict[str, Any], current: dict[str, Any] | None, lang: str) -> dict[str, str]:
    if window is current:
        outcome = t("meter.outcome.open", lang)
    elif window["rejected"]:
        outcome = t(
            "meter.outcome.rejected",
            lang,
            accumulated=format_tokens(window["first_rejection_accumulated"]),
        )
    else:
        outcome = t("meter.outcome.clear", lang)
    if not window["covered"]:
        outcome += t("meter.outcome.partial", lang)
    label = t("meter.window", lang, start=_day(window["start"], lang))
    if window["inferred"]:
        label += t("meter.inferred", lang)
    return {
        "window": label,
        "accumulated": format_tokens(window["accumulated"]),
        "tiers": _tiers(window["cache_5m"], window["cache_1h"], lang),
        "families": _families(window, lang),
        "outcome": outcome,
    }


def _percent(amount: float, scale: float) -> float:
    return round(min(100.0, 100.0 * amount / scale), 1)


def describe_weekly_meter(meter: dict[str, Any], lang: str) -> dict[str, Any]:
    state = meter["state"]
    five_hour = t(
        "meter.five_hour",
        lang,
        accumulated=format_tokens(meter["five_hour"]["accumulated"]),
        rejected=meter["five_hour"]["rejected_windows"],
    )
    base: dict[str, Any] = {
        "heading": t("meter.heading", lang),
        "name": t("meter.name", lang),
        "lang": lang,
        "caveat": t("meter.caveat", lang),
        "five_hour": five_hour,
        "empty": state in ("no_data", "no_anchor"),
        "state": state,
        "rows": [],
        "range": None,
        "range_start_percent": None,
        "range_end_percent": None,
    }
    if base["empty"]:
        return {**base, "status": t(f"meter.status.{state}", lang)}

    observed = meter["range"]
    current = meter["current"]
    value = current["accumulated"] if current else 0
    peak = max(value, observed["maximum"] if observed else 0)
    scale = max(1, int(peak * SCALE_HEADROOM) + 1)
    if state == "hypothesis":
        status = points_label(observed["points"], lang)
    else:
        status = t(f"meter.status.{state}", lang)
        if observed is not None:
            status += f" ({points_label(observed['points'], lang)})"
    view = {
        **base,
        "status": status,
        "value": value,
        "value_text": format_tokens(value),
        "tiers": _tiers(current["cache_5m"], current["cache_1h"], lang) if current else "",
        "scale_maximum": scale,
        "fill_percent": _percent(value, scale),
        "rows": [_row(window, current, lang) for window in meter["windows"][:RECENT_WINDOWS]],
    }
    if observed is not None:
        view["range"] = t(
            "meter.range",
            lang,
            minimum=format_tokens(observed["minimum"]),
            maximum=format_tokens(observed["maximum"]),
        )
        view["range_start_percent"] = _percent(observed["minimum"], scale)
        view["range_end_percent"] = _percent(observed["maximum"], scale)
    return view


def _project_of(metrics_directory: Path, lang: str) -> dict[str, str]:
    """Conversation id to project label, through the session records: a call
    row carries the transcript's id, which is the session id."""
    mapping: dict[str, str] = {}
    for record in load_records(str(metrics_directory / "sessions.jsonl")):
        path = color_group_key(record.get("project_path"))
        if record.get("session_id") and path:
            mapping[str(record["session_id"])] = project_display_label(path, lang=lang)
    return mapping


def load_weekly_meter_view(metrics_directory: Path, lang: str, now: datetime) -> dict[str, Any]:
    """Read the stores and describe the meter and its causes: what each
    dashboard calls. The calls are read once for both."""
    calls = read_calls(metrics_directory / CALLS_FILE_NAME)
    meter = compute_weekly_meter(
        calls, read_limit_hits(metrics_directory / LIMIT_HITS_FILE_NAME), now
    )
    view = describe_weekly_meter(meter, lang)
    view["causes"] = describe_cache_causes(
        compute_cache_causes(calls, meter, _project_of(metrics_directory, lang)), lang
    )
    return view
