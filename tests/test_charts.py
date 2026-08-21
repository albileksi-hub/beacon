import pytest

from app.services import charts


def plot(values, labels, **kwargs):
    """A chart with no axis furniture around it.

    The gutter and the axis band are insets that hold the labels, so a test
    about where the curve lands says so by switching them off rather than
    doing the arithmetic around them.
    """
    return charts.build(values, labels, gutter=0, axis_band=0, **kwargs)


def test_no_data_produces_an_empty_chart():
    chart = charts.build([], [])

    assert chart.is_empty
    assert chart.points == []
    assert chart.curve == ""


def test_a_flat_zero_series_still_draws_a_baseline():
    # The scale falls back to 1 rather than dividing by a zero peak.
    chart = plot([0, 0, 0], ["a", "b", "c"], height=100, padding=10)

    assert chart.is_empty
    assert [point.y for point in chart.points] == [90.0, 90.0, 90.0]


def test_the_peak_reaches_the_top_of_the_plot_area():
    chart = plot([0, 5, 10], ["a", "b", "c"], height=100, padding=10)

    assert chart.peak == 10
    assert chart.points[-1].y == 10.0  # the padding line
    assert chart.points[0].y == 90.0
    assert chart.points[1].y == 50.0


def test_points_are_evenly_spaced_across_the_width():
    chart = plot([1, 1, 1], ["a", "b", "c"], width=100, padding=10)

    assert [point.x for point in chart.points] == [10.0, 50.0, 90.0]


def test_a_single_point_is_centred():
    chart = plot([3], ["only"], width=100, padding=10)

    assert [point.x for point in chart.points] == [50.0]


def test_labels_and_values_travel_with_their_points():
    chart = charts.build([4, 9], ["monday", "tuesday"], width=100)

    assert [(point.label, point.value) for point in chart.points] == [
        ("monday", 4),
        ("tuesday", 9),
    ]


def test_the_area_path_is_closed_along_the_baseline():
    chart = plot([1, 2], ["a", "b"], width=100, height=50, padding=10)

    assert chart.area.startswith("M 10.00,40.00")
    assert chart.area.endswith("L 90.00,40.00 Z")


def test_the_area_follows_the_same_curve_as_the_stroke():
    """Two paths that disagreed would draw a fill detached from its line."""
    chart = charts.build([3, 9, 4], ["a", "b", "c"])

    assert chart.curve[chart.curve.index("C") :] in chart.area


def test_the_curve_is_a_cubic_spline_not_straight_segments():
    chart = charts.build([1, 9, 3, 7], ["a", "b", "c", "d"])

    assert chart.curve.startswith("M ")
    assert chart.curve.count("C") == 3  # one segment between each pair


def _sample(path: str, steps: int = 16) -> list[float]:
    """Every y the curve actually passes through, not just its knots."""
    numbers = path.replace("M", " ").replace("C", " ").split()
    pairs = [tuple(float(part) for part in token.split(",")) for token in numbers]

    heights: list[float] = []
    start = pairs[0]
    for index in range(1, len(pairs), 3):
        first, second, end = pairs[index], pairs[index + 1], pairs[index + 2]
        for step in range(steps + 1):
            t = step / steps
            u = 1 - t
            heights.append(
                u**3 * start[1]
                + 3 * u**2 * t * first[1]
                + 3 * u * t**2 * second[1]
                + t**3 * end[1]
            )
        start = end
    return heights


def test_the_curve_never_overshoots_the_data():
    """The reason for a monotone spline rather than an ordinary smooth one.

    A spike surrounded by zeroes makes a naive curve bulge below the baseline,
    drawing visitor counts that never happened.
    """
    chart = plot([0, 0, 100, 0, 0], list("abcde"), height=100, padding=0)

    # SVG y runs downwards: 100 is a count of zero, 0 is the peak.
    heights = _sample(chart.curve)
    assert max(heights) <= 100.0 + 1e-6, "the curve dipped below zero visitors"
    assert min(heights) >= 0.0 - 1e-6, "the curve rose above the peak"


def test_a_rising_series_stays_rising():
    chart = plot([1, 2, 3, 4, 5], list("abcde"), height=100, padding=0)

    heights = _sample(chart.curve)
    assert heights == sorted(heights, reverse=True)


def test_a_sparkline_is_just_the_curve():
    path = charts.sparkline([1, 5, 2, 8])

    assert path.startswith("M ")
    assert "C" in path


def test_a_flat_or_absent_sparkline_draws_nothing():
    assert charts.sparkline([]) == ""
    assert charts.sparkline([0, 0, 0]) == ""


def test_a_single_point_chart_has_no_area_to_fill():
    chart = charts.build([5], ["only"])

    assert chart.area == ""
    assert chart.curve.startswith("M ")


@pytest.mark.parametrize(
    ("peak", "expected"),
    [
        (0, 1),
        (3, 3),
        (7, 8),
        (12, 12),
        (47, 50),
        (96, 100),
        (344, 400),
        (12099, 12500),
        # Past the last step, so it rounds to the next whole decade.
        (95, 100),
        (9500, 10000),
    ],
)
def test_the_axis_rounds_up_to_a_number_a_person_would_choose(peak, expected):
    assert charts.axis_ceiling(peak) == expected


def test_gridlines_span_the_axis():
    chart = plot([0, 5, 10], ["a", "b", "c"], height=100, padding=10)

    assert [line.value for line in chart.gridlines] == [0, 2, 4, 6, 8, 10]
    assert [line.y for line in chart.gridlines] == [90.0, 74.0, 58.0, 42.0, 26.0, 10.0]


def test_points_are_scaled_to_the_axis_not_the_peak():
    # Peak 96 draws against an axis of 100, so it stops just short of the top.
    chart = plot([96], ["a"], height=100, padding=0)

    assert chart.ceiling == 100
    assert chart.points[0].y == 4.0


def test_an_empty_chart_has_no_gridlines():
    assert charts.build([], []).gridlines == []


def test_a_cliff_edge_has_its_tangents_clamped():
    """Fritsch-Carlson's actual job.

    A gentle rise followed by a vertical one gives the middle point a tangent
    far steeper than the segment before it, and without clamping the curve
    swings well below the first value on its way up.
    """
    chart = plot([0, 1, 100], ["a", "b", "c"], height=100, padding=0)

    heights = _sample(chart.curve)
    assert max(heights) <= 100.0 + 1e-6, "the curve dipped below zero visitors"
    assert min(heights) >= 0.0 - 1e-6, "the curve rose above the peak"


@pytest.mark.parametrize(
    ("ceiling", "divisions"),
    [
        (125, 5),
        (100, 5),
        (200, 5),
        (400, 5),
        (12, 4),
        (9, 3),
        (2, 2),
        # Nothing divides it, so the axis is just its two ends.
        (1, 1),
        (7, 1),
    ],
)
def test_the_axis_is_cut_into_bands_that_divide_it_wholly(ceiling, divisions):
    """Why the gridlines are not simply halved.

    Halving a ceiling of 125 draws a line at 62.5 and labels it 62, so the
    line sits slightly off the value it claims. Choosing a divisor the ceiling
    actually divides by means every gridline is exactly where its label says.
    """
    assert charts.grid_divisions(ceiling) == divisions
    assert ceiling % divisions == 0


def test_every_gridline_lands_on_a_whole_number():
    chart = charts.build([0, 125], ["a", "b"])

    assert [line.value for line in chart.gridlines] == [0, 25, 50, 75, 100, 125]


def test_the_axis_furniture_is_inset_rather_than_drawn_over_the_plot():
    """The labels used to sit inside the plot, on top of the curve."""
    chart = charts.build([1, 2], ["a", "b"], width=200, height=100, padding=10)

    assert chart.plot_left == 10 + charts.DEFAULT_GUTTER
    assert chart.baseline == 100 - 10 - charts.DEFAULT_AXIS_BAND
    # No point starts left of the gutter or below the baseline.
    assert min(point.x for point in chart.points) >= chart.plot_left
    assert max(point.y for point in chart.points) <= chart.baseline


def test_an_empty_chart_still_reports_where_its_axis_would_be():
    """The template reads these before it knows whether there is data."""
    chart = charts.build([], [])

    assert chart.plot_left > 0
    assert chart.baseline > 0
    assert chart.ticks == []


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (1, [0]),
        (4, [0, 1, 2, 3]),
        (7, [0, 1, 2, 3, 4, 5, 6]),
        (13, [0, 2, 4, 6, 8, 10, 12]),
        (30, [0, 5, 10, 14, 19, 24, 29]),
    ],
)
def test_ticks_are_evenly_spaced_and_keep_both_ends(count, expected):
    positions = charts.tick_positions(count)

    assert positions == expected
    assert positions[0] == 0
    assert positions[-1] == count - 1
    assert len(positions) <= charts.MAX_TICKS


def test_ticks_are_points_so_a_label_stays_with_its_x():
    """A tick drawn at an x that belongs to a different bucket is a lie."""
    labels = [f"2026-08-{day:02d}" for day in range(1, 21)]
    chart = charts.build(list(range(20)), labels)

    assert len(chart.ticks) == charts.MAX_TICKS
    assert chart.ticks[0] is chart.points[0]
    assert chart.ticks[-1] is chart.points[-1]
    for tick in chart.ticks:
        assert tick in chart.points
