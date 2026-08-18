"""Geometry for the dashboard's inline SVG chart.

Computed server-side so the page renders without JavaScript, and so the
project needs neither a charting library nor the Node toolchain to build one.
"""

from dataclasses import dataclass

DEFAULT_WIDTH = 760
DEFAULT_HEIGHT = 200
DEFAULT_PADDING = 6


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float
    value: int
    label: str


@dataclass(frozen=True, slots=True)
class Chart:
    width: int
    height: int
    peak: int
    line: str
    area: str
    points: list[Point]

    @property
    def is_empty(self) -> bool:
        return self.peak == 0


def build(
    values: list[int],
    labels: list[str],
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    padding: int = DEFAULT_PADDING,
) -> Chart:
    """Turn a series into an SVG polyline and a closed area path."""
    if not values:
        return Chart(width=width, height=height, peak=0, line="", area="", points=[])

    peak = max(values)
    # A flat-zero series still has to draw a baseline rather than divide by zero.
    scale_to = peak or 1
    usable_height = height - 2 * padding
    span = width - 2 * padding

    points = [
        Point(
            x=padding + (span * index / (len(values) - 1) if len(values) > 1 else span / 2),
            y=height - padding - (value / scale_to) * usable_height,
            value=value,
            label=label,
        )
        for index, (value, label) in enumerate(zip(values, labels, strict=True))
    ]

    line = " ".join(f"{point.x:.2f},{point.y:.2f}" for point in points)
    area = (
        f"M {points[0].x:.2f},{height} "
        + " ".join(f"L {point.x:.2f},{point.y:.2f}" for point in points)
        + f" L {points[-1].x:.2f},{height} Z"
    )

    return Chart(width=width, height=height, peak=peak, line=line, area=area, points=points)
