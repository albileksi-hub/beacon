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
_cached_salts: WeakKeyDictionary[Any, dict[tuple[str, dt.date], bytes]] = WeakKeyDictionary()
_cache_lock = threading.Lock()


def forget_cached_salts() -> None:
    with _cache_lock:
        _cached_salts.clear()


def utc_today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def current_salt(db: Session, *, site_id: str, day: dt.date) -> bytes:
    """The salt for one site's day, created on first use.

    Keyed by site as well as day because a site's days start at its own
    midnight. A shared salt rotating at UTC midnight would let one local day
    contain two identities for the same person, and daily figures would stop
    summing to the truth.
    """
    bind = db.get_bind()
    key = (site_id, day)

    with _cache_lock:
        cached = _cached_salts.get(bind, {}).get(key)
    if cached is not None:
        return cached

    existing = db.scalar(
        select(DailySalt).where(DailySalt.site_id == site_id, DailySalt.day == day)
    )
    if existing is not None:
        return _remember(bind, key, existing.value)

    salt = DailySalt(site_id=site_id, day=day, value=secrets.token_bytes(SALT_BYTES))
    db.add(salt)
    try:
        db.commit()
    except IntegrityError:
        # Another worker created this salt between our read and our write.
        db.rollback()
        winner = db.scalars(
            select(DailySalt).where(DailySalt.site_id == site_id, DailySalt.day == day)
        ).one().value
        return _remember(bind, key, winner)

    # Creating a salt is also a natural moment to expire the old ones, though
    # it is no longer the only one -- see app.background.
    purge_expired_salts(db, today=day)
    return _remember(bind, key, salt.value)


def _remember(bind: Any, key: tuple[str, dt.date], value: bytes) -> bytes:
    with _cache_lock:
        for_engine = _cached_salts.setdefault(bind, {})
        # Only the current day is worth holding; yesterday's is never asked for
        # twice and would otherwise accumulate one entry per site per day.
        for stale in [existing for existing in for_engine if existing[1] != key[1]]:
            del for_engine[stale]
        for_engine[key] = value
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


def keyed_hash(*parts: str, salt: bytes) -> str:
    """The one construction every address-derived identifier here uses.

    A 16-byte BLAKE2b keyed with a salt that is deleted two days later. It was
    written out twice -- once for visitor IDs, once for the login throttle --
    and the entire privacy argument rests on both being the same construction
    with the same rotating key, so it is spelled out once.

    Parts are joined with a NUL, which cannot appear inside any of them, so no
    two different tuples can be made to produce the same message.
    """
    message = b"\x00".join(part.encode("utf-8") for part in parts)
    return hashlib.blake2b(message, key=salt, digest_size=16).hexdigest()


def visitor_id(*, salt: bytes, site_id: str, ip: str, user_agent: str) -> str:
    """Derive a visitor identifier stable for one of that site's days only.

    Scoping by site means the same person visiting two customers' sites gets
    unrelated IDs, so nothing can be correlated across the customer base.
    """
    return keyed_hash(site_id, ip, user_agent, salt=salt)
