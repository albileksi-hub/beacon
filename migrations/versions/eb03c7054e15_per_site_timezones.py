"""per site timezones

Revision ID: eb03c7054e15
Revises: 4115845a588c
Create Date: 2026-08-20 17:15:20.260002

Events gain the site's local day and hour, so no query has to truncate a
timestamp any more. Salts gain a site, so they can rotate at that site's
midnight rather than at UTC's.

Everything that exists before this migration was reckoned in UTC, and every
existing site keeps UTC as its zone -- so the backfill is exact rather than a
best guess.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eb03c7054e15"
down_revision: str | Sequence[str] | None = "4115845a588c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_local_parts() -> None:
    """Derive day and hour from the timestamps already stored.

    The one place in this project that needs dialect-specific date SQL, and it
    runs once. Both branches read UTC, which is what every existing row is in.
    """
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "UPDATE events SET day = date(timestamp),"
            " hour = CAST(strftime('%H', timestamp) AS INTEGER)"
        )
    else:
        op.execute(
            "UPDATE events SET day = (timestamp AT TIME ZONE 'UTC')::date,"
            " hour = EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC')::int"
        )


def upgrade() -> None:
    op.add_column(
        "sites",
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="UTC"
        ),
    )

    # Added nullable so existing rows survive, filled in, then tightened.
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("day", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("hour", sa.Integer(), nullable=True))

    _backfill_local_parts()

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.alter_column("day", existing_type=sa.Date(), nullable=False)
        batch_op.alter_column("hour", existing_type=sa.Integer(), nullable=False)
        batch_op.create_index("ix_events_site_day", ["site_id", "day"], unique=False)

    # Salts are ephemeral by design -- nothing keeps them beyond two days -- and
    # the old ones belong to no site, so there is nothing to migrate them to.
    # Dropping them means today's visitors are counted under a fresh identity
    # from here on, which is the same thing that happens every midnight.
    op.execute("DELETE FROM daily_salts")
    with op.batch_alter_table("daily_salts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("site_id", sa.String(length=253), nullable=False))
        batch_op.create_primary_key("pk_daily_salts", ["site_id", "day"])

    # Hourly aggregates are derived, so they are rebuilt rather than converted.
    op.execute("DELETE FROM hourly_stats")
    with op.batch_alter_table("hourly_stats", schema=None) as batch_op:
        batch_op.drop_index("ix_hourly_stats_lookup")
        batch_op.drop_constraint("uq_hourly_stats_grain", type_="unique")
        batch_op.add_column(sa.Column("day", sa.Date(), nullable=False))
        batch_op.alter_column(
            "hour", existing_type=sa.DateTime(timezone=True), type_=sa.Integer(),
            postgresql_using="0", nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_hourly_stats_grain", ["site_id", "day", "hour"]
        )
        batch_op.create_index(
            "ix_hourly_stats_lookup", ["site_id", "day", "hour"], unique=False
        )


def downgrade() -> None:
    op.execute("DELETE FROM hourly_stats")
    with op.batch_alter_table("hourly_stats", schema=None) as batch_op:
        batch_op.drop_index("ix_hourly_stats_lookup")
        batch_op.drop_constraint("uq_hourly_stats_grain", type_="unique")
        batch_op.alter_column(
            "hour", existing_type=sa.Integer(), type_=sa.DateTime(timezone=True),
            postgresql_using="NULL", nullable=False,
        )
        batch_op.drop_column("day")
        batch_op.create_unique_constraint("uq_hourly_stats_grain", ["site_id", "hour"])
        batch_op.create_index("ix_hourly_stats_lookup", ["site_id", "hour"], unique=False)

    op.execute("DELETE FROM daily_salts")
    with op.batch_alter_table("daily_salts", schema=None) as batch_op:
        batch_op.drop_column("site_id")
        batch_op.create_primary_key("pk_daily_salts", ["day"])

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_site_day")
        batch_op.drop_column("hour")
        batch_op.drop_column("day")

    op.drop_column("sites", "timezone")
