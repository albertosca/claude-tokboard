"""The cache-creation causes as strings, shared by both dashboards."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .cache_causes import CAUSES, FIRST_REQUEST_JUMP, SUBAGENT_SIZE_BOUNDS
from .format import format_tokens
from .i18n import session_date, t

# Weekly windows shown, newest first.
RECENT_CAUSE_WINDOWS = 4


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
    return {
        "lang": lang,
        "empty": not (
            shown or any(entry["runs"] for entry in causes["subagent_sizes"]) or first["versions"]
        ),
        "heading": t("causes.heading", lang),
        "note": t("causes.note", lang),
        "alert": alert,
        "windows": [_window_row(window, lang) for window in shown],
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
        "first_jump": jump,
    }
