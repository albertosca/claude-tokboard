"""The cache-creation causes as strings, shared by both dashboards."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .cache_causes import CAUSES, FIRST_REQUEST_JUMP, SUBAGENT_SIZE_BOUNDS
from .format import format_tokens
from .i18n import session_date, t

# Weekly windows shown, newest first.
RECENT_CAUSE_WINDOWS = 4


def _session_label(entry: dict[str, Any], lang: str) -> str:
    started = datetime.fromtimestamp(entry["started"], tz=timezone.utc).isoformat()
    day = session_date(started, lang)
    return f"{entry['project']} · {day}" if entry["project"] else day


def _percent(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole else 0


def _share(part: int, whole: int, lang: str) -> str:
    return t("causes.share", lang, amount=format_tokens(part), percent=_percent(part, whole))


def _tier_share(tier: dict[str, int], lang: str) -> str:
    total = tier["cache_5m"] + tier["cache_1h"]
    return t("causes.none", lang) if total == 0 else f"{_percent(tier['cache_5m'], total)}%"


def _window_row(window: dict[str, Any], lang: str) -> dict[str, str]:
    start = datetime.fromtimestamp(window["start"], tz=timezone.utc).isoformat()
    label = t("meter.window", lang, start=session_date(start, lang))
    if window["open"]:
        label += " · " + t("meter.outcome.open", lang)
    if not window["covered"]:
        label += t("meter.outcome.partial", lang)
    total = window["total"]
    row = {"window": label, "total": format_tokens(total)}
    for cause in CAUSES:
        row[cause] = _share(window["causes"][cause], total, lang)
    row["after_idle"] = _share(window["rewrite_after_idle"], total, lang)
    row["main_short"] = _tier_share(window["main"], lang)
    row["subagent_short"] = _tier_share(window["subagent"], lang)
    return row


def _shares(amounts: dict[str, int]) -> dict[str, int]:
    """Whole-number shares that sum to exactly 100 (largest remainder), or all
    zero when there is nothing to share. Rounding each share alone can add up
    to 99 or 101, which a stacked bar would show as a gap or an overflow."""
    total = sum(amounts.values())
    if total == 0:
        return dict.fromkeys(amounts, 0)
    shares = {cause: 100 * amount // total for cause, amount in amounts.items()}
    by_remainder = sorted(
        amounts, key=lambda cause: (-(100 * amounts[cause] % total), CAUSES.index(cause))
    )
    for cause in by_remainder[: 100 - sum(shares.values())]:
        shares[cause] += 1
    return shares


def _bar(window: dict[str, Any], row: dict[str, str], lang: str) -> dict[str, Any]:
    shares = _shares({cause: window["causes"][cause] for cause in CAUSES})
    segments = [
        {
            "cause": cause,
            "label": str(t(f"causes.cause.{cause}", lang)),
            "percent": shares[cause],
        }
        for cause in CAUSES
    ]
    parts = ", ".join(f"{segment['label']} {segment['percent']}%" for segment in segments)
    return {
        "window": row["window"],
        "segments": segments,
        "alt": t("causes.bar_alt", lang, window=row["window"], parts=parts),
    }


def _size_label(index: int, lang: str) -> str:
    bound = SUBAGENT_SIZE_BOUNDS[index]
    if index == 0:
        return t("causes.size.upto", lang, bound=bound)
    if bound is None:
        return t("causes.size.over", lang, bound=SUBAGENT_SIZE_BOUNDS[index - 1])
    previous = SUBAGENT_SIZE_BOUNDS[index - 1] or 0
    return t("causes.size.between", lang, low=previous + 1, high=bound)


def describe_cache_causes(causes: dict[str, Any], lang: str) -> dict[str, Any]:
    shown = causes["windows"][:RECENT_CAUSE_WINDOWS]
    current = next((window for window in causes["windows"] if window["open"]), None)
    alert = ""
    if causes["tier_alert"] and current is not None:
        main = current["main"]
        alert = t(
            "causes.tier_alert",
            lang,
            percent=_percent(main["cache_5m"], main["cache_5m"] + main["cache_1h"]),
        )
    first = causes["first_requests"]
    jump = ""
    if first["jump"] is not None:
        by_version = {entry["version"]: entry["median"] for entry in first["versions"]}
        jump = t(
            "causes.first_jump",
            lang,
            percent=round(100 * FIRST_REQUEST_JUMP),
            before=f"{first['jump']['from']} ({format_tokens(by_version[first['jump']['from']])})",
            after=f"{first['jump']['to']} ({format_tokens(by_version[first['jump']['to']])})",
        )
    rows = [_window_row(window, lang) for window in shown]
    bars = [_bar(window, rows[index], lang) for index, window in enumerate(shown)]
    headline = ""
    if shown and shown[0]["total"] and shown[0]["covered"]:
        ranked = sorted(bars[0]["segments"], key=lambda segment: -segment["percent"])
        # A tie names no dominant cause: picking the first would be arbitrary.
        if ranked[0]["percent"] > ranked[1]["percent"]:
            headline = t(
                "causes.headline", lang, percent=ranked[0]["percent"], cause=ranked[0]["label"]
            )
    view: dict[str, Any] = {
        "lang": lang,
        "empty": not (
            shown
            or any(entry["runs"] for entry in causes["subagent_sizes"])
            or first["versions"]
            or causes["first_by_project"]
            or causes["idle_sessions"]
            or causes["short_by_project"]
        ),
        "heading": t("causes.heading", lang),
        "note": t("causes.note", lang),
        "alert": alert,
        "windows": rows,
        "sizes": [
            {
                "size": _size_label(index, lang),
                "runs": str(entry["runs"]),
                "creation": format_tokens(entry["creation"]),
                "rewrite": _share(entry["rewrite"], entry["creation"], lang),
            }
            for index, entry in enumerate(causes["subagent_sizes"])
        ],
        "first_requests": [
            {
                "version": entry["version"],
                "conversations": str(entry["conversations"]),
                "median": format_tokens(entry["median"]),
            }
            for entry in first["versions"]
        ],
        "first_by_project": [
            {
                "project": entry["project"],
                "conversations": str(entry["conversations"]),
                "median": format_tokens(entry["median"]),
            }
            for entry in causes["first_by_project"]
        ],
        "idle_sessions": [
            {
                "session": _session_label(entry, lang),
                "tokens": format_tokens(entry["tokens"]),
                "resumes": str(entry["resumes"]),
            }
            for entry in causes["idle_sessions"]
        ],
        "short_by_project": [
            {
                "project": entry["project"],
                "short": str(entry["short"]),
                "share": f"{_percent(entry['short'], entry['conversations'])}%",
                "creation": format_tokens(entry["creation"]),
            }
            for entry in causes["short_by_project"]
        ],
        "first_jump": jump,
        "headline": headline,
        "bars": bars,
        "cause_labels": {cause: str(t(f"causes.cause.{cause}", lang)) for cause in CAUSES},
        "terms_title": t("causes.terms_title", lang),
    }
    view["titles"] = {
        key: t("details.summary", lang, name=t(caption_key, lang), count=len(view[rows_key]))
        for key, caption_key, rows_key in (
            ("windows", "causes.table_caption", "windows"),
            ("sizes", "causes.sizes_caption", "sizes"),
            ("first", "causes.first_caption", "first_requests"),
            ("by_project", "causes.by_project_caption", "first_by_project"),
            ("idle", "causes.idle_caption", "idle_sessions"),
            ("short", "causes.short_caption", "short_by_project"),
        )
    }
    return view
