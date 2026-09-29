"""Number/date formatting helpers shared by both dashboard entry points."""

from __future__ import annotations

import re
import zlib
from collections.abc import Iterable

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


# The families every chart knows, in the slot each one prefers. Matched as a
# whole hyphen-separated word, so "claude-opus-4-8" and "claude-3-opus-..."
# are both opus.
_MODEL_FAMILIES = ("opus", "sonnet", "haiku", "fable")
_MODEL_FAMILY = re.compile(r"(?:^|-)(" + "|".join(_MODEL_FAMILIES) + r")(?:-|$)")


def _family_rank(model: str) -> int:
    """The model's family's place in _MODEL_FAMILIES, or one past the end."""
    match = _MODEL_FAMILY.search(model)
    return _MODEL_FAMILIES.index(match.group(1)) if match else len(_MODEL_FAMILIES)


def _preferred_model_slot(model: str, slots: int) -> int:
    rank = _family_rank(model)
    if rank < min(len(_MODEL_FAMILIES), slots):
        return rank
    # crc32, not hash(): Python salts str hashes per process, and a colour
    # that followed hash() would change every time the dashboard starts.
    return zlib.crc32(model.encode()) % slots


def assign_model_slots(models: Iterable[str], slots: int) -> dict[str, int | None]:
    """A colour slot per model id, the same whichever period is on screen.

    Both dashboards used to colour the "by model" chart by each model's
    rank in the period being shown, so a model changed colour whenever
    another one outspent it (backlog 2026-09-24 #6). Here a model's slot
    comes from its id: the four known families take slots 0-3, anything
    else a stable hash of its id.

    Two models can want one slot - two versions of a family always do - so
    the ties are settled over the WHOLE set of models passed in, which is
    why callers pass every model in the history rather than those in one
    period: in id order (families first), the first to want a slot gets it,
    and each of the rest takes the next free one after the slot it wanted.
    A model's slot therefore only moves when a model new to the history
    competes for it - wants the same slot and sorts first, or takes a slot
    the model had probed its way to. Once every slot is taken, the rest get
    None - the caller's shared "other" colour.
    """
    ordered = sorted(set(models), key=lambda model: (_family_rank(model), model))
    assignment: dict[str, int | None] = {}
    taken: set[int] = set()
    waiting: list[str] = []
    for model in ordered:
        preferred = _preferred_model_slot(model, slots)
        if preferred in taken:
            waiting.append(model)
            continue
        assignment[model] = preferred
        taken.add(preferred)
    for model in waiting:
        preferred = _preferred_model_slot(model, slots)
        free = [(preferred + step) % slots for step in range(1, slots)]
        slot = next((candidate for candidate in free if candidate not in taken), None)
        assignment[model] = slot
        if slot is not None:
            taken.add(slot)
    return assignment


def format_tokens(count: int | float | None) -> str:
    if count is None:
        return "-"
    count = int(count)
    if abs(count) >= 1_000_000_000_000:
        return f"{count / 1_000_000_000_000:.1f}T"
    if abs(count) >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    if abs(count) >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if abs(count) >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


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
