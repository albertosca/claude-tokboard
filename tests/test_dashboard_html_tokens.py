"""Colour tokens of the HTML dashboard's stylesheet, measured rather than
eyeballed (2026-10-05 review, item 7).

--text-muted carries real information - the session id, the tmux window,
axis labels, sort notes - often at 9-11px. Against the light surface it
measured 3.5:1, under WCAG's 4.5:1 for small text.
"""

from __future__ import annotations

import re

import pytest

from dashboard_html_assets import STYLE


def _theme_blocks() -> dict[str, str]:
    light = STYLE[STYLE.index(":root {") : STYLE.index("@media (prefers-color-scheme: dark)")]
    dark_start = STYLE.index("@media (prefers-color-scheme: dark)")
    dark = STYLE[dark_start : STYLE.index("}\n      }", dark_start)]
    return {"light": light, "dark": dark}


def _token(block: str, name: str) -> str:
    match = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", block)
    assert match, f"--{name} not found"
    return match.group(1)


def _relative_luminance(hex_colour: str) -> float:
    channels = []
    for offset in (1, 3, 5):
        value = int(hex_colour[offset : offset + 2], 16) / 255
        channels.append(value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted(
        (_relative_luminance(foreground), _relative_luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("token", ["text-muted", "text-secondary", "text-primary"])
def test_every_text_token_meets_small_text_contrast_on_its_surface(theme: str, token: str) -> None:
    block = _theme_blocks()[theme]
    ratio = _contrast(_token(block, token), _token(block, "surface-1"))
    assert ratio >= 4.5, f"--{token} in {theme}: {ratio:.2f}:1"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_others_bucket_recedes_towards_the_surface(theme: str) -> None:
    # "Others" is filler - everything outside the eight coloured projects -
    # and on a real history it is most of every old bar. Measured in the
    # browser (2026-10-05): a gray far from the surface drew the eye before
    # the coloured projects did. It must sit closer to the surface than the
    # "no project" gray, which it must still be told apart from.
    block = _theme_blocks()[theme]
    surface = _relative_luminance(_token(block, "surface-1"))
    others = _relative_luminance(_token(block, "group-other"))
    no_project = _relative_luminance(_token(block, "group-none"))
    assert abs(others - surface) < abs(no_project - surface)
