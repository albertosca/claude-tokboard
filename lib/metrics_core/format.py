"""Number/date formatting helpers shared by both dashboard entry points."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class ModelColorSlot:
    """One model's place in the shared, history-wide colour palette.

    palette_index always points at a real palette colour (0-based), even
    once every slot is claimed: a model that appears after the palette is
    full is not left uncoloured, it reuses an earlier model's colour
    instead (cycling through the palette in the order overflow models
    themselves appeared). is_overflow marks that reuse, and the caller
    must give an overflow model its OWN highlight identity - never the
    class its colour's original owner renders with, and never the shared
    "no model" class either - or clicking one would light up an unrelated
    band that merely happens to share a colour.
    """

    palette_index: int
    is_overflow: bool


def assign_model_slots(models: Iterable[str], slots: int) -> dict[str, ModelColorSlot]:
    """A colour slot per model id, permanent once given.

    Both dashboards used to colour the "by model" chart by each model's
    rank in the period being shown, so a model changed colour whenever
    another one outspent it (backlog 2026-09-24 #6). Then a model's slot
    came from a stable hash of its id, which fixed that but introduced a
    subtler break: every render recomputed the WHOLE assignment from
    scratch over the current model set, sorted by id, so a model newly
    added to the history could sort before an existing one and win a slot
    the existing model had already been given render after render.

    The fix is to stop deriving order from the id at all. `models` here is
    walked in the order its caller iterates it, and each model's FIRST
    occurrence in that order is its "first appearance" - callers pass every
    model in the full history (not just one period) in the order its
    earliest record appears there, which is what makes this permanent: the
    history only grows, so a model already assigned a slot is never
    revisited by a later call, only skipped past. Ties (two models with no
    prior occurrence competing in the same call) cannot happen for a real
    caller, since a model id is only ever "first seen" once - the ordering
    here exists to make that explicit, not to break a tie.

    The four well-known families (opus/sonnet/haiku/fable) still reserve
    slots 0-3 ahead of everything else, because that reservation is what
    lets someone recognise "opus is always the same swatch" across
    projects and renders without reading a legend first - it costs
    nothing when a family model is actually first to appear (the common
    case), and only stops applying once that reservation has already gone
    to an earlier-appearing sibling version or, this being a soft
    preference rather than a hard one, a generic model that happened to
    appear first and claimed it as an ordinary free slot.

    Every other model - and any family model whose reserved slot is
    already gone - takes the lowest-numbered slot still free at the moment
    it is processed, in first-appearance order. Once every slot is taken,
    the palette is reused rather than left unclaimed - see
    ModelColorSlot.is_overflow.

    If the author deletes part of the history, the next render only knows
    about what remains: a model's "first appearance" is recomputed from
    the survivors, so a model that used to appear second can become first
    and take over slot 0. Colour stability is a property of an
    append-only history, not a promise that survives editing it.
    """
    first_seen_order: dict[str, int] = {}
    for index, model in enumerate(models):
        first_seen_order.setdefault(model, index)

    ordered = sorted(
        first_seen_order, key=lambda model: (_family_rank(model), first_seen_order[model])
    )

    assignment: dict[str, ModelColorSlot] = {}
    taken: set[int] = set()
    family_slot_count = min(len(_MODEL_FAMILIES), slots)
    next_free_slot = 0
    overflow_count = 0
    for model in ordered:
        rank = _family_rank(model)
        if rank < family_slot_count and rank not in taken:
            assignment[model] = ModelColorSlot(palette_index=rank, is_overflow=False)
            taken.add(rank)
            continue
        while next_free_slot < slots and next_free_slot in taken:
            next_free_slot += 1
        if next_free_slot < slots:
            assignment[model] = ModelColorSlot(palette_index=next_free_slot, is_overflow=False)
            taken.add(next_free_slot)
            next_free_slot += 1
            continue
        assignment[model] = ModelColorSlot(palette_index=overflow_count % slots, is_overflow=True)
        overflow_count += 1
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
