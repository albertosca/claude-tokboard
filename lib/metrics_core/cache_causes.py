"""Why the cache was written: cold start, rewrite or incremental growth.

Works on the deduplicated per-call rows (`read_calls`), so a replayed message
counts once. Only aggregates leave this module. Every threshold below is a
named constant: they describe how the classification reads the data, not a
limit of the account.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

COLD_START = "cold_start"
REWRITE = "rewrite"
INCREMENTAL = "incremental"
CAUSES = (COLD_START, REWRITE, INCREMENTAL)

# A later call that writes at least this share of its own context lost its
# cache (an idle gap or a changed prefix) instead of growing it.
REWRITE_SHARE = 0.5
# A gap longer than this on the main conversation outlives the 1-hour tier.
MAIN_IDLE_SECONDS = 3600
# A gap this long on a subagent outlives the 5-minute tier.
SUBAGENT_IDLE_SECONDS = 300
# Calls per subagent run: the upper bound of each size bin (None = no bound).
SUBAGENT_SIZE_BOUNDS = (10, 30, None)
# The main conversation writing this share of its creation to the 5-minute
# tier, over at least MINIMUM_TIER_SAMPLE tokens, is the alert condition.
TIER_ALERT_SHARE = 0.5
MINIMUM_TIER_SAMPLE = 100_000
# A version whose median first request is this much larger than the previous
# version's (each over at least MINIMUM_VERSION_CONVERSATIONS) is flagged.
FIRST_REQUEST_JUMP = 0.20
MINIMUM_VERSION_CONVERSATIONS = 5
# Projects listed in the first-request-by-project table, largest median first.
PROJECTS_SHOWN = 8
# Sessions listed in the rewritten-after-idle table.
SESSIONS_SHOWN = 5


def _epoch(timestamp: Any) -> float | None:
    if not isinstance(timestamp, str):
        return None
    try:
        return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _number(call: dict[str, Any], field: str) -> int:
    value = call.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _classified(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every call with its cause, creation, context and idle gap."""
    by_conversation: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for call in calls:
        moment = _epoch(call.get("timestamp"))
        if moment is not None:
            by_conversation.setdefault(str(call.get("conversation")), []).append((moment, call))
    classified = []
    for conversation, placed in by_conversation.items():
        placed.sort(key=lambda item: item[0])
        sidechain = bool(placed[0][1].get("sidechain"))
        previous = None
        previous_context = 0
        for position, (moment, call) in enumerate(placed):
            cache_5m, cache_1h = _number(call, "cache_5m"), _number(call, "cache_1h")
            creation = cache_5m + cache_1h
            context = _number(call, "input") + _number(call, "cache_read") + creation
            cache_read = _number(call, "cache_read")
            if position == 0:
                cause = COLD_START
            elif (
                creation > 0
                and creation >= REWRITE_SHARE * context
                and cache_read < REWRITE_SHARE * previous_context
            ):
                # A big write alone is also what early growth looks like; the
                # cache was lost only if it did not serve most of the last context.
                cause = REWRITE
            else:
                cause = INCREMENTAL
            classified.append(
                {
                    "conversation": conversation,
                    "sidechain": sidechain,
                    "moment": moment,
                    "cause": cause,
                    "cache_5m": cache_5m,
                    "cache_1h": cache_1h,
                    "creation": creation,
                    "context": context,
                    "gap": None if previous is None else moment - previous,
                    "position": position,
                    "calls": len(placed),
                    "version": call.get("claude_code_version"),
                }
            )
            previous = moment
            previous_context = context
    return classified


def _idle_rewrite(item: dict[str, Any]) -> bool:
    limit = SUBAGENT_IDLE_SECONDS if item["sidechain"] else MAIN_IDLE_SECONDS
    return item["cause"] == REWRITE and item["gap"] is not None and item["gap"] > limit


def _window_causes(items: list[dict[str, Any]]) -> dict[str, Any]:
    causes = dict.fromkeys(CAUSES, 0)
    main = {"cache_5m": 0, "cache_1h": 0}
    subagent = {"cache_5m": 0, "cache_1h": 0}
    after_idle = 0
    for item in items:
        causes[item["cause"]] += item["creation"]
        tier = subagent if item["sidechain"] else main
        tier["cache_5m"] += item["cache_5m"]
        tier["cache_1h"] += item["cache_1h"]
        if _idle_rewrite(item):
            after_idle += item["creation"]
    return {
        "causes": causes,
        "rewrite_after_idle": after_idle,
        "main": main,
        "subagent": subagent,
        "total": sum(causes.values()),
    }


def _share(tier: dict[str, int]) -> float | None:
    total = tier["cache_5m"] + tier["cache_1h"]
    return None if total == 0 else tier["cache_5m"] / total


def _size_bin(calls: int) -> int:
    for index, bound in enumerate(SUBAGENT_SIZE_BOUNDS):
        if bound is None or calls <= bound:
            return index
    return len(SUBAGENT_SIZE_BOUNDS) - 1  # pragma: no cover - the last bound is None


def _subagent_sizes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bins: list[dict[str, Any]] = [
        {"bound": bound, "runs": set(), "creation": 0, "rewrite": 0}
        for bound in SUBAGENT_SIZE_BOUNDS
    ]
    for item in items:
        if not item["sidechain"]:
            continue
        entry = bins[_size_bin(item["calls"])]
        entry["runs"].add(item["conversation"])
        entry["creation"] += item["creation"]
        if item["cause"] == REWRITE:
            entry["rewrite"] += item["creation"]
    return [
        {
            "bound": entry["bound"],
            "runs": len(entry["runs"]),
            "creation": entry["creation"],
            "rewrite": entry["rewrite"],
        }
        for entry in bins
    ]


def _version_key(version: str) -> tuple[int, ...]:
    parts = []
    for piece in version.split("."):
        if not piece.isdigit():
            return ()
        parts.append(int(piece))
    return tuple(parts)


def _first_requests(items: list[dict[str, Any]]) -> dict[str, Any]:
    contexts: dict[str, list[int]] = {}
    for item in items:
        version = item["version"]
        if item["position"] == 0 and not item["sidechain"] and isinstance(version, str):
            contexts.setdefault(version, []).append(item["context"])
    versions: list[dict[str, Any]] = [
        {"version": version, "conversations": len(sizes), "median": int(median(sizes))}
        for version, sizes in sorted(contexts.items(), key=lambda pair: _version_key(pair[0]))
        if len(sizes) >= MINIMUM_VERSION_CONVERSATIONS
    ]
    jump = None
    if len(versions) >= 2:
        before, after = versions[-2]["median"], versions[-1]["median"]
        if before > 0 and after >= before * (1 + FIRST_REQUEST_JUMP):
            jump = {"from": versions[-2]["version"], "to": versions[-1]["version"]}
    return {"versions": versions, "jump": jump}


def _first_requests_by_project(
    items: list[dict[str, Any]], project_of: dict[str, str]
) -> list[dict[str, Any]]:
    """Median first request per project, for the projects with enough
    conversations. A conversation whose project is unknown is left out."""
    contexts: dict[str, list[int]] = {}
    for item in items:
        project = project_of.get(item["conversation"])
        if item["position"] == 0 and not item["sidechain"] and project:
            contexts.setdefault(project, []).append(item["context"])
    rows: list[dict[str, Any]] = [
        {"project": project, "conversations": len(sizes), "median": int(median(sizes))}
        for project, sizes in contexts.items()
        if len(sizes) >= MINIMUM_VERSION_CONVERSATIONS
    ]
    rows.sort(key=lambda row: (-row["median"], row["project"]))
    return rows[:PROJECTS_SHOWN]


def _idle_sessions(items: list[dict[str, Any]], project_of: dict[str, str]) -> list[dict[str, Any]]:
    """Main conversations ranked by the tokens they rewrote after an idle
    pause: what resuming an old session cost."""
    totals: dict[str, dict[str, Any]] = {}
    for item in items:
        entry = totals.setdefault(
            item["conversation"],
            {"started": item["moment"], "main": not item["sidechain"], "tokens": 0, "resumes": 0},
        )
        entry["started"] = min(entry["started"], item["moment"])
        if entry["main"] and _idle_rewrite(item):
            entry["tokens"] += item["creation"]
            entry["resumes"] += 1
    rows = [
        {
            "project": project_of.get(conversation, ""),
            "started": entry["started"],
            "tokens": entry["tokens"],
            "resumes": entry["resumes"],
        }
        for conversation, entry in totals.items()
        if entry["tokens"] > 0
    ]
    rows.sort(key=lambda row: (-row["tokens"], row["started"]))
    return rows[:SESSIONS_SHOWN]


def compute_cache_causes(
    calls: list[dict[str, Any]],
    meter: dict[str, Any],
    project_of: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Causes per recent weekly window, plus the all-history size and
    first-request views. `meter` is compute_weekly_meter's output; its windows
    decide the buckets. `project_of` maps a conversation id to a project label
    (call rows carry none, so the caller joins them through the sessions)."""
    items = _classified(calls)
    windows = []
    for window in meter["windows"]:
        inside = [item for item in items if window["start"] <= item["moment"] < window["end"]]
        windows.append(
            {
                "start": window["start"],
                "end": window["end"],
                "covered": window["covered"],
                "open": window is meter["current"],
                **_window_causes(inside),
            }
        )
    current = next((window for window in windows if window["open"]), None)
    alert = False
    if current is not None:
        share = _share(current["main"])
        sample = current["main"]["cache_5m"] + current["main"]["cache_1h"]
        alert = share is not None and share >= TIER_ALERT_SHARE and sample >= MINIMUM_TIER_SAMPLE
    return {
        "windows": windows,
        "tier_alert": alert,
        "subagent_sizes": _subagent_sizes(items),
        "first_requests": _first_requests(items),
        "first_by_project": _first_requests_by_project(items, project_of or {}),
        "idle_sessions": _idle_sessions(items, project_of or {}),
    }
