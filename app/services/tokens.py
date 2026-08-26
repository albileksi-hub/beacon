"""Keys for reading the stats API without a browser session.

Plausible and Umami both offer one, and it is the difference between a
dashboard and a data source. Without a key these numbers can only be read by
somebody holding a session cookie, so nothing can be scripted, embedded in a
status page, or pulled into a warehouse -- the API exists but nothing except
the dashboard can reach it.

Hashed with SHA-256 rather than bcrypt, which is the opposite of what
app.services.passwords does, and deliberately so. bcrypt's cost factor buys
protection against guessing a low-entropy human password; a token here is 32
bytes from ``secrets``, so there is no dictionary to guess from and no cost
worth paying. What the cost would actually buy is a hundred milliseconds on
every API request and a lookup that cannot use an index -- bcrypt salts each
hash separately, so finding a token would mean loading every row and comparing
them one at a time. A SHA-256 digest is found by indexed equality, in constant
work, and the comparison happens in the database rather than byte by byte here.

Read-only by construction. A token resolves to an account for
require_readable_site and nowhere else, so it reaches exactly the endpoints a
published dashboard already exposes and none of the ones that change anything.

Not rate limited, unlike Plausible's, whose limit is there because it is a
shared multi-tenant service. Here every route a token reaches reads
pre-aggregated rows in about a millisecond, and the only account that can
exhaust the host is the one that owns it.
"""

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiToken, User
from app.services.visitors import utc_today

# A recognisable prefix so a leaked key is identifiable in a log or by a secret
# scanner, and so somebody pasting the wrong string is told rather than being
# handed a bare 401.
PREFIX = "beacon_"
ENTROPY_BYTES = 32
MAX_NAME = 64

# Enough for the machines a person actually has; a bound stops a script that
# mints one per run from filling the table.
MAX_PER_ACCOUNT = 10


class TooManyTokens(Exception):
    """The account is already holding as many keys as it may."""


class InvalidTokenName(Exception):
    """A key with no name cannot be told apart from the others."""


def generate() -> str:
    """A fresh token. Returned to its owner once and never stored."""
    return PREFIX + secrets.token_urlsafe(ENTROPY_BYTES)


def digest_of(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def for_owner(db: Session, owner: User) -> list[ApiToken]:
    return list(
        db.scalars(
            select(ApiToken)
            .where(ApiToken.owner_id == owner.id)
            .order_by(ApiToken.created_at.desc())
        )
    )


def create(db: Session, *, owner: User, name: str) -> tuple[ApiToken, str]:
    """Mint a key, returning it and the plaintext exactly once."""
    cleaned = name.strip()[:MAX_NAME]
    if not cleaned:
        raise InvalidTokenName("Give the key a name")

    if len(for_owner(db, owner)) >= MAX_PER_ACCOUNT:
        raise TooManyTokens(f"An account may hold {MAX_PER_ACCOUNT} keys; revoke one first")

    plaintext = generate()
    token = ApiToken(owner_id=owner.id, name=cleaned, digest=digest_of(plaintext))
    db.add(token)
    db.commit()
    return token, plaintext


def resolve(db: Session, presented: str) -> User | None:
    """The account a token belongs to, or None if it belongs to nobody.

    Records the day of use, and only when it changes, so a busy key does not
    write a row on every request.
    """
    if not presented.startswith(PREFIX):
        return None

    token = db.scalar(select(ApiToken).where(ApiToken.digest == digest_of(presented)))
    if token is None:
        return None

    today = utc_today()
    if token.last_used_on != today:
        token.last_used_on = today
        db.commit()

    return token.owner


def revoke(db: Session, *, owner: User, token_id: int) -> bool:
    """Destroy a key. Scoped to its owner, so an id cannot reach anyone else's."""
    token = db.scalar(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.owner_id == owner.id)
    )
    if token is None:
        return False

    db.delete(token)
    db.commit()
    return True
