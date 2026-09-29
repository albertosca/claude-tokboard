import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "make-coverage-badge.py"


@pytest.fixture(scope="module")
def script() -> types.ModuleType:
    # Hyphenated filename, so it cannot be imported with a normal `import`
    # statement - load it as a module by path instead (same pattern as
    # tests/test_backfill_script.py for scripts/backfill-token-dedup.py).
    spec = importlib.util.spec_from_file_location("make_coverage_badge", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- badge_color -------------------------------------------------------
# Pin the exact thresholds and colours: this is the "green at 100%, a
# different colour below" contract the task brief requires, and a future
# edit to the ramp should have to change these assertions deliberately.


def test_badge_color_is_brightgreen_at_exactly_100(script):
    assert script.badge_color(100.0) == "#4c1"


def test_badge_color_falls_to_green_just_below_100(script):
    # 99.99 is what a single uncovered branch out of thousands looks like -
    # the whole point of the ramp is that this must NOT still read as
    # brightgreen, or the badge would lie about the gate being satisfied.
    assert script.badge_color(99.99) == "#97ca00"


def test_badge_color_green_at_its_own_lower_boundary(script):
    assert script.badge_color(90.0) == "#97ca00"


def test_badge_color_yellow_just_below_the_green_boundary(script):
    assert script.badge_color(89.99) == "#dfb317"


def test_badge_color_yellow_at_its_own_lower_boundary(script):
    assert script.badge_color(75.0) == "#dfb317"


def test_badge_color_orange_just_below_the_yellow_boundary(script):
    assert script.badge_color(74.99) == "#fe7d37"


def test_badge_color_orange_at_its_own_lower_boundary(script):
    assert script.badge_color(50.0) == "#fe7d37"


def test_badge_color_red_just_below_the_orange_boundary(script):
    assert script.badge_color(49.99) == "#e05d44"


def test_badge_color_red_at_zero(script):
    assert script.badge_color(0.0) == "#e05d44"


# --- format_percent ------------------------------------------------------


def test_format_percent_rounds_to_nearest_whole_number(script):
    assert script.format_percent(93.4) == "93%"
    assert script.format_percent(93.6) == "94%"


def test_format_percent_keeps_a_clean_100(script):
    assert script.format_percent(100.0) == "100%"


def test_format_percent_rounds_up_from_the_midpoint(script):
    # Python's round() uses banker's rounding (round-half-to-even), so this
    # specific value pins which way a genuine .5 case lands rather than
    # leaving it to whatever round() happens to do.
    assert script.format_percent(94.5) == "94%"
    assert script.format_percent(95.5) == "96%"


# --- render_svg ------------------------------------------------------------


def test_render_svg_embeds_the_rounded_value_and_the_label(script):
    svg = script.render_svg(87.2)
    assert "coverage" in svg
    assert "87%" in svg


def test_render_svg_uses_the_color_for_its_own_percent(script):
    svg_full = script.render_svg(100.0)
    assert "#4c1" in svg_full

    svg_low = script.render_svg(10.0)
    assert "#e05d44" in svg_low
    assert "#4c1" not in svg_low


def test_render_svg_is_well_formed(script):
    svg = script.render_svg(100.0)
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert svg.rstrip().endswith("</svg>")
    assert svg.count("<rect") == 4  # clip rect + label rect + value rect + gradient overlay rect
    assert svg.count("<text") == 2  # label text + value text


def test_render_svg_gives_a_shorter_value_a_narrower_segment(script):
    # "100%" (4 chars) must render wider than a shorter value like "9%" (2
    # chars) - proves the value segment's width tracks its own text rather
    # than being a fixed constant.
    svg_wide = script.render_svg(100.0)
    svg_narrow = script.render_svg(9.0)
    assert script._text_width("100%") > script._text_width("9%")
    assert "100%" in svg_wide
    assert "9%" in svg_narrow


# --- write_badge -----------------------------------------------------------


def test_write_badge_creates_parent_directories_and_writes_the_svg(script, tmp_path):
    output_path = tmp_path / "nested" / "coverage.svg"

    script.write_badge(76.0, output_path)

    assert output_path.exists()
    content = output_path.read_text()
    assert "76%" in content
    assert content.endswith("</svg>\n")


def test_write_badge_overwrites_an_existing_file(script, tmp_path):
    output_path = tmp_path / "coverage.svg"
    output_path.write_text("stale content")

    script.write_badge(100.0, output_path)

    assert "stale content" not in output_path.read_text()
    assert "100%" in output_path.read_text()


# --- get_coverage_percent ---------------------------------------------------


def test_get_coverage_percent_parses_the_totals_field_from_coverage_json(script, monkeypatch):
    fake_stdout = json.dumps({"totals": {"percent_covered": 93.75, "num_statements": 400}})

    def fake_run(command, capture_output, check, text):  # mirrors subprocess.run's own signature
        assert command == ["coverage", "json", "-o", "-"]
        assert capture_output is True
        assert check is True
        assert text is True
        return subprocess.CompletedProcess(command, 0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr(script.subprocess, "run", fake_run)

    assert script.get_coverage_percent() == 93.75


# --- main --------------------------------------------------------------


def test_main_writes_to_the_default_path_when_no_argument_given(script, monkeypatch, tmp_path):
    fake_default = tmp_path / "coverage.svg"
    monkeypatch.setattr(script, "DEFAULT_OUTPUT", fake_default)
    monkeypatch.setattr(script, "get_coverage_percent", lambda: 100.0)

    exit_code = script.main(["make-coverage-badge.py"])

    assert exit_code == 0
    assert fake_default.exists()
    assert "100%" in fake_default.read_text()


def test_main_writes_to_the_path_given_as_an_argument(script, monkeypatch, tmp_path):
    output_path = tmp_path / "custom.svg"
    monkeypatch.setattr(script, "get_coverage_percent", lambda: 42.0)

    exit_code = script.main(["make-coverage-badge.py", str(output_path)])

    assert exit_code == 0
    assert output_path.exists()
    assert "42%" in output_path.read_text()


def test_module_guard_calls_main_and_exits_with_its_return_code(monkeypatch, tmp_path):
    # `if __name__ == "__main__": sys.exit(main())` is never exercised by
    # loading this module under any other name, including the shared
    # `script` fixture (same pattern as test_backfill_script.py's own
    # module-guard test). Re-loading it with __name__ pre-set to
    # "__main__" runs the real file, so argv is pointed at a tmp_path
    # output file and `coverage json -o -` is monkeypatched to avoid
    # depending on this test run's own coverage state.
    output_path = tmp_path / "guard.svg"
    fake_stdout = json.dumps({"totals": {"percent_covered": 100.0}})
    monkeypatch.setattr(sys, "argv", ["make-coverage-badge.py", str(output_path)])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *positional_arguments, **keyword_arguments: subprocess.CompletedProcess(
            positional_arguments[0], 0, stdout=fake_stdout, stderr=""
        ),
    )

    spec = importlib.util.spec_from_file_location("__main__", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 0
    assert output_path.exists()
    assert "100%" in output_path.read_text()
