import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Event(Base):
    """A single recorded interaction.

    Every column is either non-identifying on its own or already reduced to a
    coarse bucket. There is no IP address, no cookie ID, no raw User-Agent, no
    query string, no full referring URL, and no exact viewport width.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The domain, denormalised rather than a foreign key. Reporting queries
    # filter on it constantly and none of them need anything else from the
    # sites table, so the join would buy nothing.
    site_id: Mapped[str] = mapped_column(String(253))
    # Defaulted in Python, not by the database: one timestamp format across
    # every dialect, and tests can supply their own.
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now
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
    # A bucket, never the exact pixel width; see app.services.screens.
    screen: Mapped[str] = mapped_column(String(16), default="Unknown")

    __table_args__ = (
        # Every dashboard query filters by site, slices by time, and counts
        # distinct visitors within that slice -- so one index covers all three,
        # and SQLite reports it as covering.
        #
        # There is deliberately no shorter (site_id, timestamp) index. It would
        # be a strict prefix of this one and so could never be preferred to it,
        # while still costing a write on every event: removing it measured 40%
        # more throughput on the collector with identical query plans.
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
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class User(Base):
    """Someone with a login."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    sites: Mapped[list["Site"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Site(Base):
    """A tracked domain, belonging to exactly one account."""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Also the identifier the tracking script sends, so a customer never has to
    # copy a random token around.
    domain: Mapped[str] = mapped_column(String(253), unique=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    # A public site's dashboard is readable without an account. Off by default:
    # sharing has to be a decision somebody makes, never the fallback.
    public: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    owner: Mapped[User] = relationship(back_populates="sites")


class DailyStat(Base):
    """Pre-aggregated counts for one site, one day, one dimension value.

    Unique visitors are famously not additive -- you cannot sum hourly uniques
    into a daily figure, because one person appears in several hours. Here they
    *are* additive across days, and only across days, because the visitor salt
    rotates at midnight: the same person browsing on Monday and Tuesday has two
    unrelated IDs by construction. The day is therefore the atomic unit of
    visitor identity, and this table is built on exactly that grain.
    """

    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(253))
    day: Mapped[dt.date] = mapped_column(Date)
    # "total" for the headline row, otherwise the breakdown dimension.
    dimension: Mapped[str] = mapped_column(String(16))
    # Empty string for the total row. Kept short enough that the unique index
    # stays inside Postgres's btree tuple limit.
    value: Mapped[str] = mapped_column(String(512))
    visitors: Mapped[int] = mapped_column(Integer)
    pageviews: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("site_id", "day", "dimension", "value", name="uq_daily_stats_grain"),
        Index("ix_daily_stats_lookup", "site_id", "dimension", "day"),
    )


class HourlyStat(Base):
    """Totals per site per hour, for the single-day view.

    Only totals: breakdowns for today come from that day's DailyStat rows,
    which are rebuilt as the day goes on.
    """

    __tablename__ = "hourly_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(253))
    hour: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    visitors: Mapped[int] = mapped_column(Integer)
    pageviews: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("site_id", "hour", name="uq_hourly_stats_grain"),
        Index("ix_hourly_stats_lookup", "site_id", "hour"),
    )


class LoginAttempt(Base):
    """A failed sign-in, recorded against a hash of the address.

    Rate limiting normally means keeping a list of addresses. Beacon promises
    never to store one, and that promise should not have an exception carved
    into it for the operator's own convenience -- so the address is keyed-hashed
    with the same rotating salt the visitor IDs use, and then discarded.
    """

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(32))
    attempted_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    __table_args__ = (Index("ix_login_attempts_lookup", "fingerprint", "attempted_at"),)
