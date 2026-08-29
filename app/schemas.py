from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class EventIn(BaseModel):
    """The payload sent by the tracking script on every recorded interaction."""

    # Matches the sites.domain column. A shorter cap here would let a domain be
    # registered that could then never send an event.
    site_id: str = Field(min_length=1, max_length=253)
    name: str = Field(default="pageview", min_length=1, max_length=64)
    url: str = Field(max_length=2048)
    referrer: str | None = Field(default=None, max_length=2048)
    screen_width: int | None = Field(default=None, ge=0, le=20000)
    # Read as a string and parsed as a Decimal rather than declared a float:
    # JSON has no decimals, so 49.90 arrives as 49.899999999999999 through a
    # float and rounds back to the wrong cent often enough to notice on a
    # month's takings. Bounded well above any plausible order value.
    revenue: Decimal | None = Field(default=None, ge=0, le=1_000_000_000)

    @property
    def revenue_minor(self) -> int | None:
        """The amount in minor units, rounded half-up like a till would.

        Python rounds half to even by default, so 0.005 becomes 0.00 and 0.015
        becomes 0.02 -- correct for statistics and wrong for money.
        """
        if self.revenue is None:
            return None
        return int((self.revenue * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))

    @field_validator("name")
    @classmethod
    def must_name_something(cls, value: str) -> str:
        """A blank name would show up in the goals report as an empty row."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name cannot be blank")
        return cleaned

    @field_validator("url")
    @classmethod
    def must_be_absolute_http_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute http(s) URL")
        return value


class StatsSummary(BaseModel):
    visitors: int
    pageviews: int
    views_per_visitor: float
    bounce_rate: float
    revenue_minor: int

    @classmethod
    def of(
        cls, *, visitors: int, pageviews: int, bounces: int, revenue_minor: int = 0
    ) -> "StatsSummary":
        """Derive the rates, guarding the empty period rather than dividing by it.

        ``bounces`` is required rather than defaulted. A forgotten argument
        would render as a confident 0.0% on every dashboard, which is a worse
        failure than the TypeError this raises instead.
        """
        return cls(
            visitors=visitors,
            pageviews=pageviews,
            views_per_visitor=round(pageviews / visitors, 2) if visitors else 0.0,
            # Against visitors rather than against visits-with-a-pageview. The
            # two differ only for someone who fired a custom event without ever
            # loading a page, who did not bounce and so belongs in the
            # denominator but not the numerator.
            bounce_rate=round(bounces / visitors * 100, 1) if visitors else 0.0,
            revenue_minor=revenue_minor,
        )


class TimeseriesPoint(BaseModel):
    bucket: str
    visitors: int
    pageviews: int


class BreakdownRow(BaseModel):
    value: str
    visitors: int
    pageviews: int
    # Minor units, not a decimal: JSON has no decimal type, and a float here
    # would undo the exactness the integer column exists for.
    revenue_minor: int = 0


class LiveVisitors(BaseModel):
    visitors: int
    window_minutes: int


class Change(BaseModel):
    """Movement between two periods, as a percentage.

    ``percent`` is None when the earlier period had nothing to compare against:
    a jump from zero is not a percentage increase, and rendering it as one
    (or as +100%) would be a lie the dashboard tells every time a site starts.
    """

    current: int
    previous: int
    percent: float | None

    @classmethod
    def between(cls, current: int, previous: int) -> "Change":
        return cls(
            current=current,
            previous=previous,
            percent=round((current - previous) / previous * 100, 1) if previous else None,
        )

    @property
    def direction(self) -> str:
        if self.percent is None or self.percent == 0:
            return "flat"
        return "up" if self.percent > 0 else "down"


class SummaryWithComparison(BaseModel):
    summary: StatsSummary
    visitors: Change
    pageviews: Change
