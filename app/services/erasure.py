"""Deleting a site or an account, and everything either one implies.

A product whose first line is "without collecting anything that identifies
them" has to be able to stop holding what it does collect. Until this existed
nothing here could ever be deleted: not a site, not an account.

The reason it needs its own module is a schema detail that makes the obvious
version wrong. Memberships and funnels reference sites.id, so the database
removes them on its own; but events, salts and both rollup tables key on the
domain as a plain string, with no foreign key to cascade along. Deleting the
Site row alone would leave every event and every aggregate behind, still
queryable by anybody who registered the same domain afterwards. A delete that
orphans the data is worse than no delete, because it is the same promise with
none of the effect.

So the tables are discovered from the schema rather than listed here. Adding a
table keyed by site_id and forgetting to clear it is exactly the mistake this
is written to survive, and a hand-maintained list is how that mistake would be
allowed through.
"""

from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Site, User

# The column that names a site by domain in the tables that have no foreign key
# to it. Site.domain itself is the name, not a reference, so it is excluded.
DOMAIN_COLUMN = "site_id"


def tables_keyed_by_domain() -> list[Any]:
    """Every table that refers to a site by its domain rather than by id.

    Read off the metadata, so a new one is covered the day it is added.
    """
    found = []
    for table in Base.metadata.sorted_tables:
        if table.name == Site.__tablename__:
            continue
        column = table.columns.get(DOMAIN_COLUMN)
        # An integer site_id is a foreign key to sites.id and cascades already;
        # only the string ones are orphaned by deleting the row.
        if column is not None and column.type.python_type is str:
            found.append(table)
    return found


def delete_site(db: Session, *, site: Site) -> dict[str, int]:
    """Remove a site and everything recorded against its domain.

    Returns what went, per table, so the caller can say so and a test can
    assert on it rather than on the absence of an error.
    """
    domain = site.domain
    removed: dict[str, int] = {}

    for table in tables_keyed_by_domain():
        result = cast(
            CursorResult[Any],
            db.execute(delete(table).where(table.c[DOMAIN_COLUMN] == domain)),
        )
        removed[table.name] = result.rowcount

    # Memberships and funnels go with this, by foreign key.
    db.delete(site)
    db.commit()
    removed[Site.__tablename__] = 1
    return removed


def delete_account(db: Session, *, user: User) -> dict[str, int]:
    """Remove an account, every site it owns, and all of their data.

    Sites go first and one at a time, because each one carries data the
    database cannot reach from the user row. Tokens, reset links and
    memberships do cascade from it.
    """
    removed: dict[str, int] = {}
    for site in list(db.scalars(select(Site).where(Site.owner_id == user.id))):
        for name, count in delete_site(db, site=site).items():
            removed[name] = removed.get(name, 0) + count

    db.delete(user)
    db.commit()
    removed[User.__tablename__] = 1
    return removed
