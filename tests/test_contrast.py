"""Colour contrast, checked rather than asserted.

The design notes claim WCAG AA on every text pair, with the tightest at 5.02:1.
Nothing enforced it. This palette has been rewritten several times -- teal,
orange, near-monochrome, slate and indigo, and now yellow -- and a contrast
regression during one of those would have been invisible: the page still
renders, the colours still look deliberate, and only a reader who needed the
contrast would ever find out.

Read from the stylesheet's own tokens so the check follows the palette instead
of restating a number somebody typed once.
"""

import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "static" / "dashboard.css"

# WCAG 2.1: 4.5:1 for body text, 3:1 for large text and for graphics that
# carry meaning.
BODY_TEXT = 4.5
LARGE_TEXT = 3.0

# Foreground token, background token, and how the pair is used.
TEXT_PAIRS = [
    ("text", "bg", "body text on the page"),
    ("text", "panel", "body text on a card"),
    ("text-soft", "panel", "secondary text on a card"),
    ("muted", "bg", "muted text on the page"),
    ("muted", "panel", "muted text on a card"),
    ("accent", "bg", "accent text on the page"),
    ("accent", "panel", "accent text on a card"),
    ("on-accent", "accent-fill", "text on the primary button"),
    ("up", "panel", "a rise, on a card"),
    ("down", "panel", "a fall, on a card"),
    ("danger-text", "danger-bg", "an error message"),
]


def _tokens(selector: str) -> dict[str, str]:
    """The custom properties defined under one selector."""
    css = CSS.read_text(encoding="utf-8")
    start = css.index(selector)
    block = css[start : css.index("}", start)]
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{3,8})\s*;", block))


def _luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _ratio(one: str, two: str) -> float:
    a, b = _luminance(one), _luminance(two)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("theme", [":root {", ':root[data-theme="light"] {'])
@pytest.mark.parametrize(("front", "back", "usage"), TEXT_PAIRS)
def test_every_text_pair_clears_wcag_aa(theme, front, back, usage):
    """Both themes. Dark is the default; light is what the toggle asks for."""
    tokens = _tokens(theme)
    assert front in tokens and back in tokens, f"--{front}/--{back} missing from {theme}"

    ratio = _ratio(tokens[front], tokens[back])

    assert ratio >= BODY_TEXT, (
        f"{usage}: --{front} ({tokens[front]}) on --{back} ({tokens[back]}) "
        f"is {ratio:.2f}:1, below the {BODY_TEXT}:1 that body text needs"
    )


@pytest.mark.parametrize("theme", [":root {", ':root[data-theme="light"] {'])
def test_the_live_dot_clears_the_threshold_for_graphics(theme):
    """It carries meaning on its own, so 3:1 applies rather than 4.5:1."""
    tokens = _tokens(theme)

    ratio = _ratio(tokens["live"], tokens["panel"])

    assert ratio >= LARGE_TEXT, (
        f"the live dot is {ratio:.2f}:1 on a card, below {LARGE_TEXT}:1"
    )
