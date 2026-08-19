import pytest

from app.schemas import Change, StatsSummary


def test_views_per_visitor_is_derived():
    summary = StatsSummary.of(visitors=4, pageviews=10)

    assert summary.views_per_visitor == 2.5


def test_views_per_visitor_of_an_empty_period_is_zero_not_a_crash():
    assert StatsSummary.of(visitors=0, pageviews=0).views_per_visitor == 0.0


@pytest.mark.parametrize(
    ("current", "previous", "percent", "direction"),
    [
        (150, 100, 50.0, "up"),
        (50, 100, -50.0, "down"),
        (100, 100, 0.0, "flat"),
        (0, 100, -100.0, "down"),
    ],
)
def test_change_between_two_periods(current, previous, percent, direction):
    change = Change.between(current, previous)

    assert change.percent == percent
    assert change.direction == direction


def test_growth_from_nothing_has_no_percentage():
    """A jump from zero is not a percentage increase, and saying it is lies."""
    change = Change.between(80, 0)

    assert change.percent is None
    assert change.direction == "flat"
    assert change.current == 80
