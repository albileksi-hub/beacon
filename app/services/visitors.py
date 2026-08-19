"""Anonymous visitor identity.

A visitor ID is a keyed hash of the address, User-Agent and site, keyed with a
random salt that is generated fresh every day and deleted two days later.

That deletion is the whole point. While today's salt exists, repeat visits
within the day can be recognised. Once it is gone, nobody can reproduce the
mapping from an address to an ID -- not an attacker with a stolen database
backup, and not us. The identifiers become anonymous rather than merely
pseudonymous, which is the distinction GDPR actually turns on.

Consequences accepted by this design:
  - visitors cannot be tracked across days, so there is no "returning visitor
    over time" metric. That is the trade, and it is the point.
  - the same person on two devices counts twice.
"""

import datetime as dt
import hashlib
import secrets
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import DailySalt

SALT_BYTES = 32
SALT_RETENTION_DAYS = 2


def utc_today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def current_salt(db: Session, *, today: dt.date | None = None) -> bytes:
    """Fetch today's salt, creating it on first use."""
    day = today or utc_today()

    existing = db.scalar(select(DailySalt).where(DailySalt.day == day))
    if existing is not None:
        return existing.value

    salt = DailySalt(day=day, value=secrets.token_bytes(SALT_BYTES))
    db.add(salt)
    try:
        db.commit()
    except IntegrityError:
        # Another worker created today's salt between our read and our write.
        db.rollback()
        return db.scalars(select(DailySalt).where(DailySalt.day == day)).one().value

    # First write of the day is also the natural moment to expire old salts.
    purge_expired_salts(db, today=day)
    return salt.value


def purge_expired_salts(db: Session, *, today: dt.date | None = None) -> int:
    """Delete salts old enough that their visitor IDs can no longer be re-derived."""
    cutoff = (today or utc_today()) - dt.timedelta(days=SALT_RETENTION_DAYS)
    # rowcount lives on CursorResult; Session.execute is typed as returning the
    # narrower Result.
    result = cast(CursorResult[Any], db.execute(delete(DailySalt).where(DailySalt.day < cutoff)))
    deleted = result.rowcount
    db.commit()
    return deleted


def visitor_id(*, salt: bytes, site_id: str, ip: str, user_agent: str) -> str:
    """Derive a visitor identifier that is stable for one day and one site only.

    Scoping by site means the same person visiting two customers' sites gets
    unrelated IDs, so nothing can be correlated across the customer base.
    """
    message = b"\x00".join(part.encode("utf-8") for part in (site_id, ip, user_agent))
    return hashlib.blake2b(message, key=salt, digest_size=16).hexdigest()
