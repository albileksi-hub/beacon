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
import threading
from typing import Any, cast
from weakref import WeakKeyDictionary

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import DailySalt

SALT_BYTES = 32
SALT_RETENTION_DAYS = 2

# Today's salt never changes, so re-reading it for every single event is a
# query on the hottest path in the system for a value that is constant.
# Keyed weakly by engine: a disposed engine takes its entry with it, which
# matters because tests build a fresh database per test.
_cached_salts: WeakKeyDictionary[Any, tuple[dt.date, bytes]] = WeakKeyDictionary()
_cache_lock = threading.Lock()


def forget_cached_salts() -> None:
    with _cache_lock:
        _cached_salts.clear()


def utc_today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def current_salt(db: Session, *, today: dt.date | None = None) -> bytes:
    """Fetch today's salt, creating it on first use."""
    day = today or utc_today()
    bind = db.get_bind()

    with _cache_lock:
        cached = _cached_salts.get(bind)
    if cached is not None and cached[0] == day:
        return cached[1]

    existing = db.scalar(select(DailySalt).where(DailySalt.day == day))
    if existing is not None:
        return _remember(bind, day, existing.value)

    salt = DailySalt(day=day, value=secrets.token_bytes(SALT_BYTES))
    db.add(salt)
    try:
        db.commit()
    except IntegrityError:
        # Another worker created today's salt between our read and our write.
        db.rollback()
        winner = db.scalars(select(DailySalt).where(DailySalt.day == day)).one().value
        return _remember(bind, day, winner)

    # Creating a salt is also a natural moment to expire the old ones, though
    # it is no longer the only one -- see app.background.
    purge_expired_salts(db, today=day)
    return _remember(bind, day, salt.value)


def _remember(bind: Any, day: dt.date, value: bytes) -> bytes:
    with _cache_lock:
        _cached_salts[bind] = (day, value)
    return value


def purge_expired_salts(db: Session, *, today: dt.date | None = None) -> int:
    """Delete salts old enough that their visitor IDs can no longer be re-derived.

    Must run on a timer rather than only when a salt is created. A site with no
    traffic for a week creates no salt for a week, and the old ones would sit
    there re-derivable the whole time -- which is precisely the promise this
    module exists to keep.
    """
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
