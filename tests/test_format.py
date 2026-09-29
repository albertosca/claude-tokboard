import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import metrics_core as mr

REPO = Path(__file__).resolve().parent.parent

# --- format_tokens ---------------------------------------------------


def test_format_tokens_none_is_a_dash():
    assert mr.format_tokens(None) == "-"


def test_format_tokens_under_a_thousand_is_plain():
    assert mr.format_tokens(0) == "0"
    assert mr.format_tokens(999) == "999"


def test_format_tokens_scales_through_every_suffix():
    assert mr.format_tokens(1_500) == "1.5k"
    assert mr.format_tokens(2_400_000) == "2.4M"
    assert mr.format_tokens(3_700_000_000) == "3.7B"
    assert mr.format_tokens(1_900_000_000_000) == "1.9T"


def test_format_tokens_boundaries_pick_the_larger_unit():
    # Each threshold is >=, so exactly-at-the-boundary reads as 1.0 of
    # the bigger unit rather than 1000 of the smaller one.
    assert mr.format_tokens(1_000) == "1.0k"
    assert mr.format_tokens(1_000_000) == "1.0M"
    assert mr.format_tokens(1_000_000_000) == "1.0B"
    assert mr.format_tokens(1_000_000_000_000) == "1.0T"


def test_format_tokens_uses_magnitude_so_negatives_scale_too():
    # The comparisons are on abs(n) but the formatting divides n itself.
    assert mr.format_tokens(-1_500) == "-1.5k"
    assert mr.format_tokens(-999) == "-999"


def test_format_tokens_truncates_a_float_toward_zero():
    assert mr.format_tokens(1500.9) == "1.5k"
    # Under rounding, 999.9 would become 1000 -> "1.0k" - this only
    # passes under truncation, which discriminates int(n) from round(n).
    assert mr.format_tokens(999.9) == "999"


# --- format_duration -------------------------------------------------


def test_format_duration_none_is_a_dash():
    assert mr.format_duration(None) == "-"


def test_format_duration_under_a_minute_is_seconds():
    assert mr.format_duration(0) == "0s"
    assert mr.format_duration(59) == "59s"


def test_format_duration_at_a_minute_switches_units():
    assert mr.format_duration(60) == "1min"


def test_format_duration_rounds_minutes_to_whole_numbers():
    # 90s is 1.5min, and the format spec is .0f - Python rounds half to
    # even, so this is 2min, not 1min.
    assert mr.format_duration(90) == "2min"
    assert mr.format_duration(3_540) == "59min"


def test_format_duration_at_an_hour_switches_again_and_pads_minutes():
    assert mr.format_duration(3_600) == "1h00min"
    assert mr.format_duration(3_660) == "1h01min"
    assert mr.format_duration(7_200) == "2h00min"


# --- format_date -----------------------------------------------------


def test_format_date_empty_or_none_is_a_dash():
    assert mr.format_date(None, lang="pt") == "-"
    assert mr.format_date("", lang="pt") == "-"


def test_format_date_unparseable_is_a_dash():
    assert mr.format_date("not a timestamp", lang="pt") == "-"


def test_format_date_renders_day_month_and_local_time():
    # Built from the local zone so this asserts a real formatted value
    # without hardcoding the machine's UTC offset.
    moment = datetime(2026, 3, 7, 14, 5).astimezone()
    assert mr.format_date(moment.isoformat(), lang="pt") == "07/03 14:05"


def test_format_date_accepts_a_trailing_z_and_converts_to_local():
    # The Z suffix is replaced with +00:00 before parsing, so the result
    # is that UTC instant expressed in the local zone.
    utc_moment = datetime(2026, 3, 7, 14, 5, tzinfo=timezone.utc)
    expected = utc_moment.astimezone().strftime("%d/%m %H:%M")
    assert mr.format_date("2026-03-07T14:05:00Z", lang="pt") == expected


# --- model_display_label ---------------------------------------------


def test_model_display_label_drops_the_vendor_prefix():
    # Every model in this data starts with "claude-", so carrying it into a
    # legend of six entries is six times the same word and no information.
    assert mr.model_display_label("claude-opus-5") == "opus-5"
    assert mr.model_display_label("claude-sonnet-5") == "sonnet-5"


def test_model_display_label_drops_a_trailing_date_suffix():
    # Real ids are not uniformly formatted: five are clean, one carries a
    # release date. Shortening happens HERE and never in storage, so the
    # stored history keeps the full id and a display change needs no
    # re-backfill (spec decision 3 of the capture sub-project).
    assert mr.model_display_label("claude-haiku-4-5-20251001") == "haiku-4-5"


def test_model_display_label_leaves_an_unrecognised_shape_alone():
    # Better a long label than a wrong one: anything that is not the known
    # shape is shown as it was recorded.
    assert mr.model_display_label("gpt-4o") == "gpt-4o"
    assert mr.model_display_label("unknown") == "unknown"


def test_model_display_label_does_not_mistake_a_version_for_a_date():
    # "4-5" is a version, "20251001" is a date. Only an 8-digit run is
    # dropped, so a model whose version happens to end in digits keeps them.
    assert mr.model_display_label("claude-opus-4-8") == "opus-4-8"
    assert mr.model_display_label("claude-fable-5-1") == "fable-5-1"


# --- assign_model_slots ----------------------------------------------
#
# A model's colour in the "by model" chart used to be its RANK in the
# period on screen, so the same model changed colour between periods
# whenever the ranking changed (backlog 2026-09-24 #6).


def test_assign_model_slots_gives_the_known_families_fixed_slots():
    slots = mr.assign_model_slots(
        ["claude-haiku-4-5-20251001", "claude-fable-1", "claude-sonnet-4-5", "claude-opus-4-8"], 7
    )
    assert slots == {
        "claude-opus-4-8": mr.ModelColorSlot(palette_index=0, is_overflow=False),
        "claude-sonnet-4-5": mr.ModelColorSlot(palette_index=1, is_overflow=False),
        "claude-haiku-4-5-20251001": mr.ModelColorSlot(palette_index=2, is_overflow=False),
        "claude-fable-1": mr.ModelColorSlot(palette_index=3, is_overflow=False),
    }


def test_assign_model_slots_keeps_an_existing_models_slot_when_a_new_one_sorts_before_it():
    # The bug this fixes (backlog sweep 3, package M / M1): slots used to
    # be settled by sorting every model's id from scratch on every call,
    # so a model newly added to the history could win a slot away from an
    # existing one just by sorting before it. Order now comes from the
    # CALLER - first-appearance in the real history, oldest first - never
    # from the id, so appending a model to the end, however its id sorts,
    # can only ever claim a slot nothing already holds.
    established = ["gpt-x", "local-llama", "mistral-7b"]
    before = mr.assign_model_slots(established, 5)
    after = mr.assign_model_slots([*established, "aaa-newcomer"], 5)
    for model in established:
        assert after[model] == before[model], model


def test_assign_model_slots_never_gives_two_models_one_slot_while_slots_remain():
    # Two versions of one family want the same slot - the opus that
    # appears later in this call falls through to an ordinary free slot
    # exactly like a non-family model would.
    models = [f"model-{index}" for index in range(7)] + ["claude-opus-4-1", "claude-opus-4-8"]
    slots = mr.assign_model_slots(models, 9)
    assert len(slots) == 9
    assert not any(slot.is_overflow for slot in slots.values())
    assert sorted(slot.palette_index for slot in slots.values()) == list(range(9))


def test_assign_model_slots_gives_overflow_models_a_reused_but_marked_slot():
    # Once every slot is taken, a model past the palette is NOT left
    # without one (that used to collapse it into the shared "no model"
    # colour and highlight, backlog sweep 3 / M1) - it reuses an earlier
    # model's palette_index, marked is_overflow so the caller can render
    # it with its own, distinct highlight identity instead.
    slots = mr.assign_model_slots([f"model-{index}" for index in range(10)], 7)
    real = {model: slot for model, slot in slots.items() if not slot.is_overflow}
    overflow = {model: slot for model, slot in slots.items() if slot.is_overflow}
    assert sorted(slot.palette_index for slot in real.values()) == list(range(7))
    assert len(overflow) == 3
    assert all(slot.palette_index in range(7) for slot in overflow.values())


def test_assign_model_slots_gives_different_overflow_models_different_palette_indices():
    # As long as overflow itself does not exceed a full second pass over
    # the palette, two overflow models never reuse the SAME colour as
    # each other - only a real slot holder's.
    slots = mr.assign_model_slots([f"model-{index}" for index in range(9)], 7)
    overflow_indices = [slot.palette_index for slot in slots.values() if slot.is_overflow]
    assert len(overflow_indices) == 2
    assert len(set(overflow_indices)) == 2


def test_assign_model_slots_is_the_same_in_every_process(tmp_path: Path) -> None:
    # Nothing here depends on Python's own hash() of a str, which is
    # salted per interpreter start (PYTHONHASHSEED) - but the whole point
    # of this function is a colour that never changes on its own, so it
    # is pinned against that anyway. HOME is a throwaway: importing
    # metrics_core aims its paths at HOME.
    script = (
        "import json, sys; sys.path.insert(0, 'lib'); import metrics_core as mr; "
        "slots = mr.assign_model_slots(['gpt-x', 'local-llama', 'mistral-7b'], 30); "
        "print(json.dumps("
        "{model: [slot.palette_index, slot.is_overflow] for model, slot in slots.items()}"
        "))"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "HOME": str(tmp_path), "PYTHONHASHSEED": seed},
            cwd=REPO,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        for seed in ("1", "2")
    }
    assert len(outputs) == 1, outputs
