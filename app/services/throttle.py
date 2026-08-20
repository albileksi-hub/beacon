"""Rate limiting for sign-in attempts.

Without it, nothing stops somebody trying passwords as fast as the network
allows, and the bcrypt work factor only makes that expensive rather than
impossible.
"""

import datetime as dt
import hashlib

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import LoginAttempt
from app.services.visitors import current_salt, utc_today

MAX_FAILURES = 5
WINDOW = dt.timedelta(minutes=15)

# Salts are per site; signing in belongs to no site. The tilde cannot appear
# in a hostname, so this can never collide with a real one.
SALT_SCOPE = "~login"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def fingerprint(db: Session, address: str) -> str:
    """A keyed hash of the address; the address itself is never stored.

    Domain-separated from visitor IDs by the prefix, so a login fingerprint can
    never equal one and the two tables cannot be cross-referenced to work out
    that a given visitor also tried to sign in.

    The salt rotates daily, so a lockout cannot outlive midnight. With a window
    of minutes that is immaterial, and it keeps a single rule about how long any
    address-derived value can be reproduced.
    """
    message = b"login\x00" + address.encode("utf-8")
    salt = current_salt(db, site_id=SALT_SCOPE, day=utc_today())
    return hashlib.blake2b(message, key=salt, digest_size=16).hexdigest()


def recent_failures(db: Session, marker: str, *, now: dt.datetime | None = None) -> int:
    since = (now or _now()) - WINDOW
    return (
        db.scalar(
            select(func.count(LoginAttempt.id)).where(
                LoginAttempt.fingerprint == marker, LoginAttempt.attempted_at >= since
            )
        )
        or 0
    )


def is_locked(db: Session, marker: str, *, now: dt.datetime | None = None) -> bool:
    return recent_failures(db, marker, now=now) >= MAX_FAILURES


def record_failure(db: Session, marker: str, *, now: dt.datetime | None = None) -> None:
    moment = now or _now()
    db.add(LoginAttempt(fingerprint=marker, attempted_at=moment))
    db.commit()
    purge_expired(db, now=moment)


def clear(db: Session, marker: str) -> None:
    """Forget an address's failures. Called on a successful sign-in."""
    db.execute(delete(LoginAttempt).where(LoginAttempt.fingerprint == marker))
    db.commit()


def purge_expired(db: Session, *, now: dt.datetime | None = None) -> None:
    """Drop attempts too old to count, so the table stays small on its own."""
    db.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < (now or _now()) - WINDOW))
    db.commit()
