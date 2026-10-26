"""Number/date formatting helpers shared by both dashboard entry points."""

from __future__ import annotations

import math
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


def model_family(model: str) -> str:
    """The model's family word (opus, sonnet, haiku, fable), or "other"."""
    match = _MODEL_FAMILY.search(model)
    return match.group(1) if match else "other"


# The last (major, minor) of each family that still used the older tokenizer,
# which turns the same text into about three quarters as many tokens (backlog
# 2026-10-07 #8: 9,490 against 12,602 for one 30,000-character sample, x1.33).
# Everything newer, and any family not listed, counts as the newer tokenizer.
_OLDER_TOKENIZER_LAST_VERSION = {"haiku": (4, 5), "sonnet": (4, 6), "opus": (4, 6)}
_VERSION_AFTER_FAMILY = re.compile(r"-(\d{1,2})(?:-(\d{1,2}))?(?=-|$)")
_VERSION_BEFORE_FAMILY = re.compile(r"^claude-(\d{1,2})(?:-(\d{1,2}))?-")


def uses_older_tokenizer(model: str) -> bool:
    """True when the model is a family release known to use the older tokenizer.

    A model whose family or version cannot be read is NOT claimed as older:
    the caller would rather miss one than invent a discount.
    """
    family = model_family(model)
    last_version = _OLDER_TOKENIZER_LAST_VERSION.get(family)
    if last_version is None:
        return False
    before = _VERSION_BEFORE_FAMILY.match(model)
    after = _VERSION_AFTER_FAMILY.search(model[model.index(family) + len(family) :])
    found = before or after
    if found is None:
        return False
    return (int(found.group(1)), int(found.group(2) or 0)) <= last_version


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


def _lowest_free_slot(taken: set[int], slots: int, *, start: int) -> int | None:
    return next((candidate for candidate in range(start, slots) if candidate not in taken), None)


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

    Models are processed in that first-appearance order and NOTHING ELSE -
    fix round 1 (backlog sweep 3 / package M) found that an earlier version
    sorted by (family_rank, first_seen_order), which put family_rank FIRST:
    every family model was processed before every non-family one regardless
    of when it actually first appeared, so a family model arriving LATER
    than an existing model could still go first here and take a slot the
    existing model already held. Reproduced through the real caller path
    (order_models_by_first_appearance -> assign_model_slots): a lone
    "local-llama" given the only slot there is, then "claude-opus-4-8"
    added afterwards, used to bump local-llama out of the slot it already
    held. Sorting purely by arrival order (ties, which cannot occur for a
    real caller, broken by id) is what makes "an existing model never
    moves" hold for every model, family or not - see
    test_assign_model_slots_is_a_monotonic_subset_over_hundreds_of_random_histories
    for this checked as a property, not just at the two hand-picked
    scenarios above.

    The four well-known families (opus/sonnet/haiku/fable) still reserve
    slots 0-3 as a SOFT preference: when a family model is processed (in
    its true arrival order) and its own rank is still free, it takes that
    slot - which is free of charge whenever a family model is actually
    first to appear (the common case for this project, since the history
    is Claude Code's own token usage). A non-family model, when its turn
    comes, actively SKIPS those reserved-but-open slots in favour of a
    higher, non-reserved one - keeping slots 0-3 available for a family
    model that has not appeared yet is what makes the reservation still
    mean something beyond "whichever model happened to be first". A
    non-family model only falls back into a reserved slot once every
    non-reserved one is already taken (still better than jumping straight
    to overflow while a real slot sits open). Once a slot is taken by
    EITHER kind of model, it is never reassigned - a family model whose own
    reserved rank has already gone to an earlier-arriving model (family or
    not) falls into this same non-reserved-first search, exactly like a
    generic model would.

    Once every slot is taken, the palette is reused rather than left
    unclaimed - see ModelColorSlot.is_overflow.

    If the author deletes part of the history, the next render only knows
    about what remains: a model's "first appearance" is recomputed from
    the survivors, so a model that used to appear second can become first
    and take over slot 0. Colour stability is a property of an
    append-only history, not a promise that survives editing it.

    The same is true the other way round: a repair script that writes a
    model map onto an OLD row (e.g. scripts/backfill-tokens-by-model.py)
    can give that model an earlier first appearance than it had before the
    run, re-ranking colours once. Accepted rather than guarded against -
    such runs are explicit and rare, and "first appearance" meaning true
    historical time rather than whatever order records happen to load in
    is the whole design (see order_models_by_first_appearance).
    """
    first_seen_order: dict[str, int] = {}
    for index, model in enumerate(models):
        first_seen_order.setdefault(model, index)

    ordered = sorted(first_seen_order, key=lambda model: (first_seen_order[model], model))

    assignment: dict[str, ModelColorSlot] = {}
    taken: set[int] = set()
    family_slot_count = min(len(_MODEL_FAMILIES), slots)
    overflow_count = 0
    for model in ordered:
        rank = _family_rank(model)
        if rank < family_slot_count and rank not in taken:
            assignment[model] = ModelColorSlot(palette_index=rank, is_overflow=False)
            taken.add(rank)
            continue
        slot = _lowest_free_slot(taken, slots, start=family_slot_count)
        if slot is None:
            slot = _lowest_free_slot(taken, slots, start=0)
        if slot is not None:
            assignment[model] = ModelColorSlot(palette_index=slot, is_overflow=False)
            taken.add(slot)
            continue
        assignment[model] = ModelColorSlot(palette_index=overflow_count % slots, is_overflow=True)
        overflow_count += 1
    return assignment


# Token counts are whole numbers, so a step is one of these factors times a
# power of ten, and never below 1 (an axis reading 0.25 tokens is noise).
NICE_STEP_FACTORS = (1, 2, 2.5, 5, 10)


def nice_axis_ticks(maximum_value: float, tick_count: int = 4) -> list[int]:
    """Axis ticks at round values, from 0 up to the first round value at or
    above maximum_value. An even split of the raw maximum (the old
    behaviour of both trend charts) printed 2.0B, 4.1B, 6.1B, 8.1B."""
    if maximum_value <= 0:
        return [0, 1]
    raw_step = maximum_value / tick_count
    magnitude = max(1, 10 ** math.floor(math.log10(raw_step)))
    step = next(
        factor * magnitude
        for factor in NICE_STEP_FACTORS
        if factor * magnitude >= raw_step and float(factor * magnitude).is_integer()
    )
    count = math.ceil(maximum_value / step - 1e-9)
    return [int(step * index) for index in range(count + 1)]


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
