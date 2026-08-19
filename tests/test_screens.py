import pytest

from app.services.screens import UNKNOWN, WIDEST, bucket


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (320, "Phone"),
        (390, "Phone"),
        (479, "Phone"),
        (480, "Large phone"),
        (767, "Large phone"),
        (768, "Tablet"),
        (1023, "Tablet"),
        (1024, "Laptop"),
        (1439, "Laptop"),
        (1440, WIDEST),
        (3840, WIDEST),
    ],
)
def test_widths_fall_into_their_bucket(width, expected):
    assert bucket(width) == expected


@pytest.mark.parametrize("width", [None, 0, -1])
def test_a_missing_or_impossible_width_is_unknown(width):
    assert bucket(width) == UNKNOWN


def test_nearby_widths_collapse_together():
    """The point of bucketing: 1436 and 1437 must not be distinguishable."""
    assert bucket(1436) == bucket(1437)
