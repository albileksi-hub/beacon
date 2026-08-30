import datetime as dt
from enum import StrEnum

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
    text,
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

    # The site's local day and hour, decided at ingest. Storing them means no
    # query ever truncates a timestamp, which is what kept the reporting SQL
    # dialect-specific. See app.services.zones.
    day: Mapped[dt.date] = mapped_column(Date)
    hour: Mapped[int] = mapped_column(Integer)

    # Stable for one of this site's days. See app.services.visitors.
    visitor_id: Mapped[str] = mapped_column(String(32))

    referrer_host: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(255), default="Direct")

    # Campaign tags, when the link carried them. Null for ordinary traffic,
    # which is why the campaign breakdowns filter nulls out rather than
    # reporting a vast "(none)" row on every site.
    medium: Mapped[str | None] = mapped_column(String(128))
    campaign: Mapped[str | None] = mapped_column(String(128))

    browser: Mapped[str] = mapped_column(String(64), default="Unknown")
    os: Mapped[str] = mapped_column(String(64), default="Unknown")
    device: Mapped[str] = mapped_column(String(16), default="unknown")
    country: Mapped[str | None] = mapped_column(String(2))
    # A bucket, never the exact pixel width; see app.services.screens.
    screen: Mapped[str] = mapped_column(String(16), default="Unknown")

    # What this event was worth, in minor units -- 4990 for 49.90. Integers
    # rather than a float or a Numeric: floats cannot hold 0.10 exactly and
    # summing thousands of them drifts, and SQLite has no decimal type at all,
    # so a Numeric column is a float there and a real decimal on Postgres. An
    # integer count of cents is exact on both and needs no dialect to agree.
    #
    # Null for the overwhelming majority of events, which are page reads and
    # are not worth anything in particular.
    revenue_minor: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        # The live counter is the only query left that works in real instants,
        # and it counts distinct visitors in a window -- so one index covers
        # both, and SQLite reports it as covering.
        #
        # There is deliberately no shorter (site_id, timestamp) index. It would
        # be a strict prefix of this one and so could never be preferred to it,
        # while still costing a write on every event: removing it measured 40%
        # more throughput on the collector with identical query plans.
        Index("ix_events_site_visitor", "site_id", "timestamp", "visitor_id"),
        # The rollup builder reads a site's day at a time.
        Index("ix_events_site_day", "site_id", "day"),
    )


class DailySalt(Base):
    """The rotating key behind visitor IDs.

    One per site per local day, rather than one per UTC day. A site's daily
    figures are summable into weeks and months precisely because a visitor
    cannot be recognised across a rotation, so the rotation has to happen at
    that site's midnight -- otherwise somebody browsing either side of UTC
    midnight would count twice inside one local day.

    Rows are deleted after SALT_RETENTION_DAYS, at which point the visitor IDs
    derived from them can no longer be reproduced by anyone.
    """

    __tablename__ = "daily_salts"

    site_id: Mapped[str] = mapped_column(String(253), primary_key=True)
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
    tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )


class Role(StrEnum):
    """What a person may do with a site.

    Stored as a string rather than a database enum: adding a role to a
    Postgres enum needs its own migration, and SQLite has no enum at all.
    """

    OWNER = "owner"
    ADMIN = "admin"
    VIEWER = "viewer"


class Membership(Base):
    """One person's access to one site.

    Access used to be `sites.owner_id` alone, which made a dashboard something
    exactly one person could ever open -- fine for a single author, useless the
    moment a second person needs to look at the numbers.

    The owner has a row here too, so every permission check asks the same
    question of the same table instead of special-casing the creator. The
    column stays on sites as the record of who registered the domain, which is
    also what keeps one domain to one account.
    """

    __tablename__ = "site_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    user: Mapped["User"] = relationship()
    site: Mapped["Site"] = relationship(back_populates="members")

    __table_args__ = (
        # One row per person per site: a second grant would make "their role"
        # a question with two answers.
        UniqueConstraint("site_id", "user_id", name="uq_site_members_grain"),
        # Listing a person's sites reads this way round.
        Index("ix_site_members_user", "user_id"),
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

    # The last day retention has taken raw events from, or NULL if it never
    # has. Days at or before it can no longer be rebuilt: the aggregates are
    # the only surviving copy, and a rebuild would delete them to recompute
    # from rows that are gone. Recorded rather than inferred from what
    # survives, because events deleted for any other reason should still clear
    # their stale aggregates.
    raw_events_purged_through: Mapped[dt.date | None] = mapped_column(Date)

    # What this site counts money in. One currency per site and no conversion:
    # a rate needs a network call on the ingest path, or a stale table, and
    # either one is worse than reporting the number the site actually sent.
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", server_default=text("'USD'")
    )

    # The zone this site's days are reckoned in. Everything downstream -- the
    # aggregates, the chart buckets, and the visitor salt rotation -- follows
    # from it. See app.services.zones.
    timezone: Mapped[str] = mapped_column(
        String(64), default="UTC", server_default=text("'UTC'")
    )

    owner: Mapped[User] = relationship(back_populates="sites")
    members: Mapped[list[Membership]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )


class StepKind(StrEnum):
    """What a funnel step is waiting for."""

    PAGE = "page"
    GOAL = "goal"


class Funnel(Base):
    """A path through a site, in the order somebody is meant to take it.

    Measured within one visit and no further. A visitor ID here cannot outlive
    the day it was minted in, so "signed up a week after reading the pricing
    page" is not a question this schema can answer and never will be -- see
    app.services.funnels for what that rules out.
    """

    __tablename__ = "funnels"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    site: Mapped["Site"] = relationship()
    steps: Mapped[list["FunnelStep"]] = relationship(
        back_populates="funnel",
        cascade="all, delete-orphan",
        order_by="FunnelStep.position",
    )

    __table_args__ = (
        UniqueConstraint("site_id", "name", name="uq_funnels_name"),
    )


class FunnelStep(Base):
    """One rung of a funnel: a page that was read, or a goal that fired."""

    __tablename__ = "funnel_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    funnel_id: Mapped[int] = mapped_column(ForeignKey("funnels.id", ondelete="CASCADE"))
    # Zero-based, and unique per funnel: two steps in the same place is an
    # order with no answer.
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(8))
    # A pathname, or the name of a goal. Sized to whichever column it is
    # matched against, so a step can always name something real.
    value: Mapped[str] = mapped_column(String(1024))

    funnel: Mapped[Funnel] = relationship(back_populates="steps")

    __table_args__ = (
        UniqueConstraint("funnel_id", "position", name="uq_funnel_steps_order"),
    )


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

    # Visits that read one page and went no further. Only ever set on the total
    # row: a bounce is a property of a visit, not of a dimension value, so
    # counting one against every breakdown a visit touches would sum to several
    # times the truth. Additive across days, like everything else here, because
    # a visit cannot straddle midnight.
    bounces: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    # Revenue in minor units, summed the same way visitors are. Because the
    # rollup builder already groups by every dimension, this arrives as
    # revenue per source, per campaign, per landing page and so on without a
    # single extra query.
    revenue_minor: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )

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
    day: Mapped[dt.date] = mapped_column(Date)
    hour: Mapped[int] = mapped_column(Integer)
    visitors: Mapped[int] = mapped_column(Integer)
    pageviews: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("site_id", "day", "hour", name="uq_hourly_stats_grain"),
        Index("ix_hourly_stats_lookup", "site_id", "day", "hour"),
    )


class ApiToken(Base):
    """A key for reading the stats API without a browser session.

    Read-only by construction rather than by a permission flag: a token
    resolves to an account for require_readable_site and for nothing else, so
    a leaked key can pull numbers and cannot publish a dashboard, change a
    site's timezone, or delete anything.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # So a person with several can tell which is which before revoking one.
    name: Mapped[str] = mapped_column(String(64))
    # SHA-256 of the token, hex. Deliberately not bcrypt; app.services.tokens
    # explains why the reasoning that applies to passwords does not apply here.
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    # The day it was last used, not the moment. "Is this key still live?" is
    # answerable from a date, whereas a timestamp per call accumulates into a
    # log of when its owner works -- which is the kind of record this project
    # exists in order not to keep.
    last_used_on: Mapped[dt.date | None] = mapped_column(Date)

    owner: Mapped[User] = relationship(back_populates="tokens")


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
