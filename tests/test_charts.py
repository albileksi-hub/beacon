import pytest

from app.services import charts


def test_no_data_produces_an_empty_chart():
    chart = charts.build([], [])

    assert chart.is_empty
    assert chart.points == []
    assert chart.curve == ""


def test_a_flat_zero_series_still_draws_a_baseline():
    # The scale falls back to 1 rather than dividing by a zero peak.
    chart = charts.build([0, 0, 0], ["a", "b", "c"], height=100, padding=10)

    assert chart.is_empty
    assert [point.y for point in chart.points] == [90.0, 90.0, 90.0]


def test_the_peak_reaches_the_top_of_the_plot_area():
    chart = charts.build([0, 5, 10], ["a", "b", "c"], height=100, padding=10)

    assert chart.peak == 10
    assert chart.points[-1].y == 10.0  # the padding line
    assert chart.points[0].y == 90.0
    assert chart.points[1].y == 50.0


def test_points_are_evenly_spaced_across_the_width():
    chart = charts.build([1, 1, 1], ["a", "b", "c"], width=100, padding=10)

    assert [point.x for point in chart.points] == [10.0, 50.0, 90.0]


def test_a_single_point_is_centred():
    chart = charts.build([3], ["only"], width=100, padding=10)

    assert [point.x for point in chart.points] == [50.0]


def test_labels_and_values_travel_with_their_points():
    chart = charts.build([4, 9], ["monday", "tuesday"], width=100)

    assert [(point.label, point.value) for point in chart.points] == [
        ("monday", 4),
        ("tuesday", 9),
    ]


def test_the_area_path_is_closed_along_the_baseline():
    chart = charts.build([1, 2], ["a", "b"], width=100, height=50, padding=10)

    assert chart.area.startswith("M 10.00,50")
    assert chart.area.endswith("L 90.00,50 Z")


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
    chart = charts.build([0, 0, 100, 0, 0], list("abcde"), height=100, padding=0)

    # SVG y runs downwards: 100 is a count of zero, 0 is the peak.
    heights = _sample(chart.curve)
    assert max(heights) <= 100.0 + 1e-6, "the curve dipped below zero visitors"
    assert min(heights) >= 0.0 - 1e-6, "the curve rose above the peak"


def test_a_rising_series_stays_rising():
    chart = charts.build([1, 2, 3, 4, 5], list("abcde"), height=100, padding=0)

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
    chart = charts.build([0, 5, 10], ["a", "b", "c"], height=100, padding=10)

    assert [line.value for line in chart.gridlines] == [10, 5, 0]
    assert [line.y for line in chart.gridlines] == [10.0, 50.0, 90.0]


def test_points_are_scaled_to_the_axis_not_the_peak():
    # Peak 96 draws against an axis of 100, so it stops just short of the top.
    chart = charts.build([96], ["a"], height=100, padding=0)

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
    chart = charts.build([0, 1, 100], ["a", "b", "c"], height=100, padding=0)

    heights = _sample(chart.curve)
    assert max(heights) <= 100.0 + 1e-6, "the curve dipped below zero visitors"
    assert min(heights) >= 0.0 - 1e-6, "the curve rose above the peak"
