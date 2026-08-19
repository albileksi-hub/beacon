import pytest

from app.services import charts


def test_no_data_produces_an_empty_chart():
    chart = charts.build([], [])

    assert chart.is_empty
    assert chart.points == []
    assert chart.line == ""


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


def test_the_line_is_a_polyline_points_list():
    chart = charts.build([1, 2], ["a", "b"], width=100, height=50, padding=10)

    assert len(chart.line.split(" ")) == 2
    assert all("," in pair for pair in chart.line.split(" "))


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
