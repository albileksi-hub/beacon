"""Viewport width, reduced to a bucket at the boundary.

An exact viewport width is one of the higher-entropy signals in a browser
fingerprint -- "1437 pixels wide" narrows a person down considerably, and in
combination with the other columns it would undo much of the point of this
project. The bucket answers the question a site owner actually has ("do I need
to care about phones?") and carries almost none of the entropy, so the pixel
value is used once and discarded.
"""

# Upper bound (exclusive) and the label for everything below it.
BREAKPOINTS = (
    (480, "Phone"),
    (768, "Large phone"),
    (1024, "Tablet"),
    (1440, "Laptop"),
)
WIDEST = "Desktop"
UNKNOWN = "Unknown"


def bucket(width: int | None) -> str:
    if width is None or width <= 0:
        return UNKNOWN

    for upper_bound, label in BREAKPOINTS:
        if width < upper_bound:
            return label

    return WIDEST
