"""Geometry for the dashboard's inline SVG chart.

Computed server-side so the page renders without JavaScript, and so the
project needs neither a charting library nor the Node toolchain to build one.
"""

from dataclasses import dataclass

DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 220
DEFAULT_PADDING = 8

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
    line: str
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

    magnitude = 10 ** (len(str(peak)) - 1)
    for step in _AXIS_STEPS:
        candidate = int(magnitude * step)
        if candidate >= peak:
            return candidate

    return magnitude * 10


def build(
    values: list[int],
    labels: list[str],
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    padding: int = DEFAULT_PADDING,
) -> Chart:
    """Turn a series into an SVG polyline, a closed area path, and gridlines."""
    if not values:
        return Chart(
            width=width,
            height=height,
            peak=0,
            ceiling=1,
            line="",
            area="",
            points=[],
            gridlines=[],
        )

    peak = max(values)
    ceiling = axis_ceiling(peak)
    usable_height = height - 2 * padding
    span = width - 2 * padding

    def y_for(value: float) -> float:
        return height - padding - (value / ceiling) * usable_height

    points = [
        Point(
            x=padding + (span * index / (len(values) - 1) if len(values) > 1 else span / 2),
            y=y_for(value),
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

    gridlines = [
        Gridline(y=round(y_for(ceiling * fraction), 2), value=int(ceiling * fraction))
        for fraction in (1.0, 0.5, 0.0)
    ]

    return Chart(
        width=width,
        height=height,
        peak=peak,
        ceiling=ceiling,
        line=line,
        area=area,
        points=points,
        gridlines=gridlines,
    )
