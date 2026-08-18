import datetime as dt

from sqlalchemy import Date, DateTime, Index, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Event(Base):
    """A single recorded interaction.

    Every column is either non-identifying on its own or already reduced to a
    coarse bucket. There is no IP address, no cookie ID, no raw User-Agent, no
    query string, and no full referring URL.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    name: Mapped[str] = mapped_column(String(64), default="pageview")
    pathname: Mapped[str] = mapped_column(String(1024))

    # Stable for one day, one site. See app.services.visitors.
    visitor_id: Mapped[str] = mapped_column(String(32))

    referrer_host: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(255), default="Direct")

    browser: Mapped[str] = mapped_column(String(64), default="Unknown")
    os: Mapped[str] = mapped_column(String(64), default="Unknown")
    device: Mapped[str] = mapped_column(String(16), default="unknown")
    country: Mapped[str | None] = mapped_column(String(2))
    screen_width: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        # Every dashboard query filters by site and slices by time.
        Index("ix_events_site_timestamp", "site_id", "timestamp"),
        # Unique-visitor counts group by visitor within that same slice.
        Index("ix_events_site_visitor", "site_id", "timestamp", "visitor_id"),
    )


class DailySalt(Base):
    """The rotating key behind visitor IDs.

    Rows are deleted after SALT_RETENTION_DAYS, at which point the visitor IDs
    derived from them can no longer be reproduced by anyone.
    """

    __tablename__ = "daily_salts"

    day: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    value: Mapped[bytes] = mapped_column(LargeBinary(32))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
