"""bucket viewport width

Revision ID: 615a9888d52d
Revises: e3bfc5d3beeb
Create Date: 2026-08-19 12:44:51.226065

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '615a9888d52d'
down_revision: Union[str, Sequence[str], None] = 'e3bfc5d3beeb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Replace the exact viewport width with a bucket.

    Adding a NOT NULL column to a populated table needs a default to fill the
    existing rows, so one is supplied and then removed -- new rows get their
    value from the application, not the database.
    """
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "screen", sa.String(length=16), nullable=False, server_default="Unknown"
            )
        )

    # Derive the bucket for existing rows while the width is still there.
    op.execute(
        """
        UPDATE events SET screen = CASE
            WHEN screen_width IS NULL OR screen_width <= 0 THEN 'Unknown'
            WHEN screen_width < 480 THEN 'Phone'
            WHEN screen_width < 768 THEN 'Large phone'
            WHEN screen_width < 1024 THEN 'Tablet'
            WHEN screen_width < 1440 THEN 'Laptop'
            ELSE 'Desktop'
        END
        """
    )

    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_column("screen_width")
        batch_op.alter_column("screen", server_default=None)


def downgrade() -> None:
    """Restore the column, but not the values.

    The exact widths cannot come back: discarding them is the point of the
    upgrade, and a bucket does not carry enough information to invert.
    """
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("screen_width", sa.INTEGER(), nullable=True))
        batch_op.drop_column("screen")
