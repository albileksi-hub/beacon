from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class EventIn(BaseModel):
    """The payload sent by the tracking script on every recorded interaction."""

    site_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="pageview", max_length=64)
    url: str = Field(max_length=2048)
    referrer: str | None = Field(default=None, max_length=2048)
    screen_width: int | None = Field(default=None, ge=0, le=20000)

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


class TimeseriesPoint(BaseModel):
    bucket: str
    visitors: int
    pageviews: int


class BreakdownRow(BaseModel):
    value: str
    visitors: int
    pageviews: int


class LiveVisitors(BaseModel):
    visitors: int
    window_minutes: int
