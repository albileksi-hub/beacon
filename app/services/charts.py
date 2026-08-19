"""Geometry for the dashboard's inline SVG charts.

Computed server-side so the page renders without JavaScript, and so the project
needs neither a charting library nor the Node toolchain to build one.

The curve is a monotone cubic spline rather than straight segments. Ordinary
smoothing overshoots: a run of small values next to a spike bulges the curve
below zero, drawing visitor counts that never happened. Fritsch-Carlson
constrains the tangents so the curve can never leave the range of the data it
passes through.
"""

import math
from dataclasses import dataclass

DEFAULT_WIDTH = 820
DEFAULT_HEIGHT = 240
DEFAULT_PADDING = 10

SPARKLINE_WIDTH = 120
SPARKLINE_HEIGHT = 28

# Multiples a person would actually choose for an axis. Scaling to the raw peak
# instead would label the gridlines 344 and 172. A peak above the last step
# rounds up to the next whole decade.
_AXIS_STEPS = (1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8)


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float
    value: int
    label: str


@dataclass(frozen=True, slots=True)
class Gridline:
    y: float
    value: int


@dataclass(frozen=True, slots=True)
class Chart:
    width: int
    height: int
    peak: int
    ceiling: int
    curve: str
    area: str
    points: list[Point]
    gridlines: list[Gridline]

    @property
    def is_empty(self) -> bool:
        return self.peak == 0


def axis_ceiling(peak: int) -> int:
    """Round a peak up to a round number for the top of the axis."""
    if peak <= 0:
        return 1
    if peak <= 5:
        return peak

    magnitude = int(10 ** (len(str(peak)) - 1))
    for step in _AXIS_STEPS:
        candidate = int(magnitude * step)
        if candidate >= peak:
            return candidate

    return magnitude * 10


def _tangents(xs: list[float], ys: list[float]) -> list[float]:
    """Fritsch-Carlson tangents: smooth, but never overshooting the data.

    Called only with two or more points; _curve_through handles the shorter
    cases before it gets here.
    """
    count = len(xs)
    widths = [xs[i + 1] - xs[i] for i in range(count - 1)]
    slopes = [(ys[i + 1] - ys[i]) / widths[i] for i in range(count - 1)]

    tangents = [slopes[0]]
    for i in range(1, count - 1):
        # A turning point gets a flat tangent, which is what stops the curve
        # bulging past the values on either side of it.
        if slopes[i - 1] * slopes[i] <= 0:
            tangents.append(0.0)
        else:
            tangents.append((slopes[i - 1] + slopes[i]) / 2)
    tangents.append(slopes[-1])

    for i, slope in enumerate(slopes):
        if slope == 0:
            tangents[i] = tangents[i + 1] = 0.0
            continue

        alpha = tangents[i] / slope
        beta = tangents[i + 1] / slope
        magnitude = math.hypot(alpha, beta)
        if magnitude > 3:
            scale = 3 / magnitude
            tangents[i] = scale * alpha * slope
            tangents[i + 1] = scale * beta * slope

    return tangents


def _curve_through(points: list[Point]) -> str:
    """An SVG path following the points as a monotone cubic spline.

    Callers guarantee at least one point.
    """
    if len(points) == 1:
        return f"M {points[0].x:.2f},{points[0].y:.2f}"

    xs = [point.x for point in points]
    ys = [point.y for point in points]
    tangents = _tangents(xs, ys)

    path = [f"M {xs[0]:.2f},{ys[0]:.2f}"]
    for i in range(len(points) - 1):
        third = (xs[i + 1] - xs[i]) / 3
        path.append(
            f"C {xs[i] + third:.2f},{ys[i] + tangents[i] * third:.2f}"
            f" {xs[i + 1] - third:.2f},{ys[i + 1] - tangents[i + 1] * third:.2f}"
            f" {xs[i + 1]:.2f},{ys[i + 1]:.2f}"
        )

    return " ".join(path)


def _plot(
    values: list[int], labels: list[str], *, width: int, height: int, padding: int
) -> tuple[list[Point], int, int]:
    peak = max(values)
    ceiling = axis_ceiling(peak)
    usable_height = height - 2 * padding
    span = width - 2 * padding

    points = [
        Point(
            x=padding + (span * index / (len(values) - 1) if len(values) > 1 else span / 2),
            y=height - padding - (value / ceiling) * usable_height,
            value=value,
            label=label,
        )
        for index, (value, label) in enumerate(zip(values, labels, strict=True))
    ]
    return points, peak, ceiling


def build(
    values: list[int],
    labels: list[str],
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    padding: int = DEFAULT_PADDING,
) -> Chart:
    """Turn a series into a curve, a filled area, and gridlines."""
    if not values:
        return Chart(
            width=width,
            height=height,
            peak=0,
            ceiling=1,
            curve="",
            area="",
            points=[],
            gridlines=[],
        )

    points, peak, ceiling = _plot(
        values, labels, width=width, height=height, padding=padding
    )
    curve = _curve_through(points)

    # The fill reuses the stroke's path, dropped to the baseline at both ends,
    # so the two can never disagree about where the line runs.
    area = (
        f"M {points[0].x:.2f},{height} L {points[0].x:.2f},{points[0].y:.2f} "
        + curve[curve.index("C") :]
        + f" L {points[-1].x:.2f},{height} Z"
        if len(points) > 1
        else ""
    )

    gridlines = [
        Gridline(
            y=round(height - padding - fraction * (height - 2 * padding), 2),
            value=int(ceiling * fraction),
        )
        for fraction in (1.0, 0.5, 0.0)
    ]

    return Chart(
        width=width,
        height=height,
        peak=peak,
        ceiling=ceiling,
        curve=curve,
        area=area,
        points=points,
        gridlines=gridlines,
    )


def sparkline(
    values: list[int],
    *,
    width: int = SPARKLINE_WIDTH,
    height: int = SPARKLINE_HEIGHT,
    padding: int = 3,
) -> str:
    """A bare curve for the headline tiles: no axis, no labels, no dots."""
    if not values or max(values) <= 0:
        return ""

    points, _, _ = _plot(
        values, [""] * len(values), width=width, height=height, padding=padding
    )
    return _curve_through(points)
