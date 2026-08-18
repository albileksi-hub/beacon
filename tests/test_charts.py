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
