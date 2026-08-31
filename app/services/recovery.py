"""Getting back into an account whose password is gone.

Before this existed a forgotten password was permanent: there was no reset, no
mail, and manage.py offered no way in either, so the account and every site
under it were simply lost.

Three properties do the work, and each closes a hole the obvious version
leaves open:

  - the token is hashed at rest, so a stolen database yields no working links;
  - it is single-use and short-lived, and redeeming one spends every other
    outstanding link for that account, because a reset issued while an attacker
    was in the mailbox must not survive the reset that was meant to lock them
    out;
  - changing the password bumps the account's session epoch, which invalidates
    every session opened before it. Without that, resetting a password you
    believe is compromised leaves whoever compromised it still signed in --
    which is the one thing the person clicking the link is trying to prevent.
"""

import datetime as dt
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PasswordReset, User
from app.services.accounts import normalise_email, set_password

# Long enough to find the mail, short enough that a link left in an inbox for a
# week is not a standing key to the account.
TTL = dt.timedelta(hours=1)

ENTROPY_BYTES = 32


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def digest_of(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def begin(db: Session, *, email: str, now: dt.datetime | None = None) -> tuple[User, str] | None:
    """Issue a reset link, or None when nobody holds that address.

    The caller must answer identically either way. Telling the difference is
    how a reset form becomes a way of enumerating who has an account.
    """
    moment = now or _now()
    user = db.scalar(select(User).where(User.email == normalise_email(email)))
    if user is None:
        return None

    plaintext = secrets.token_urlsafe(ENTROPY_BYTES)
    db.add(
        PasswordReset(
            user_id=user.id,
            digest=digest_of(plaintext),
            expires_at=moment + TTL,
        )
    )
    db.commit()
    return user, plaintext


def _live(db: Session, presented: str, moment: dt.datetime) -> PasswordReset | None:
    reset = db.scalar(select(PasswordReset).where(PasswordReset.digest == digest_of(presented)))
    if reset is None or reset.used_at is not None:
        return None
    # Stored naive on SQLite, aware on Postgres; compare on common ground.
    expires = reset.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=dt.UTC)
    return None if expires <= moment else reset


def is_live(db: Session, presented: str, *, now: dt.datetime | None = None) -> bool:
    """Whether a link is still worth showing a form for."""
    return _live(db, presented, now or _now()) is not None


def redeem(
    db: Session, *, presented: str, new_password: str, now: dt.datetime | None = None
) -> User | None:
    """Set the new password, or None if the link is spent, expired or unknown.

    Raises InvalidPassword for a password that fails the usual rules, so the
    caller can re-render the form rather than discard a still-valid link.
    """
    moment = now or _now()
    reset = _live(db, presented, moment)
    if reset is None:
        return None

    user = reset.user
    set_password(user=user, password=new_password)

    # Every outstanding link for this account, not just the one presented.
    for other in db.scalars(
        select(PasswordReset).where(
            PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None)
        )
    ):
        other.used_at = moment

    db.commit()
    return user


def purge_expired(db: Session, *, now: dt.datetime | None = None) -> int:
    """Drop rows that can no longer do anything. Returns how many went."""
    moment = now or _now()
    dead = list(
        db.scalars(
            select(PasswordReset).where(
                (PasswordReset.expires_at <= moment) | (PasswordReset.used_at.is_not(None))
            )
        )
    )
    for reset in dead:
        db.delete(reset)
    db.commit()
    return len(dead)
