"""Number/date formatting helpers shared by both dashboard entry points."""

from __future__ import annotations

import re

from .i18n import session_date

# A trailing release date on a model id, e.g. claude-haiku-4-5-20251001.
# Exactly eight digits at the end, so a version that happens to end in a
# number (claude-opus-4-8) keeps it.
_MODEL_DATE_SUFFIX = re.compile(r"-\d{8}$")


def model_display_label(model: str) -> str:
    """A model id shortened for a legend, never for storage.

    Ids are stored exactly as the transcript wrote them (capture spec,
    decision 3) so the history never loses information and a change here
    never requires re-running the backfill. Two things are dropped: the
    "claude-" prefix, which every id in this data shares and which would be
    the same word six times in one legend, and a trailing eight-digit
    release date, which only one id carries. Anything that does not match
    those shapes is returned untouched - a long label is better than a
    wrong one.
    """
    label = model[len("claude-") :] if model.startswith("claude-") else model
    return _MODEL_DATE_SUFFIX.sub("", label)


def format_tokens(n: int | float | None) -> str:
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


def format_duration(seconds: int | float | None) -> str:
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


def format_date(timestamp_start: str | None, *, lang: str) -> str:
    return session_date(timestamp_start, lang)
