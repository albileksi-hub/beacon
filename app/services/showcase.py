"""A playable version of the one idea the product rests on.

The landing page can assert that a daily salt makes an identifier expire, and
nobody has to believe it. This lets a visitor move the three things that go
into the hash -- who, which site, which day -- and watch the answer change,
beside a cookie that changes for none of them.

It calls the real visitor_id, not a reimplementation. A demo that mocked the
hash would be a drawing of the argument rather than the argument, and the
first time the two drifted apart the page would be lying.

What it does not use is the real salt table. Salts there are random and
deleted after two days, which is exactly what makes them unsuitable here: the
page has to show the same answer to everyone, including on a day the salt
would already be gone. These are derived from a fixed seed instead, so the
demo is reproducible and no part of it can reveal a real one.
"""

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass

from app.services.visitors import visitor_id

# Not a secret and not used for anything real. Named so that it cannot be
# mistaken for one of the rotating salts in daily_salts.
DEMO_SEED = b"beacon-landing-page-demonstration-only"

# Stand-ins for an address and a User-Agent, which are the real inputs. Asking
# a visitor to type their own would collect the two things this page exists to
# say are not collected.
PEOPLE: dict[str, tuple[str, str]] = {
    "ada": ("203.0.113.7", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15"),
    "grace": ("198.51.100.42", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"),
}

SITES = ("blue-mug.example", "red-bowl.example")

# Far enough to cross the two-day salt retention, which is the point being made.
DAYS = (0, 1, 2)


@dataclass(frozen=True, slots=True)
class Identity:
    """One row of the demo: what each approach would store for these inputs."""

    person: str
    site: str
    day_offset: int
    day: dt.date
    beacon_id: str
    cookie_id: str
    salt_still_exists: bool


def demo_salt(day: dt.date) -> bytes:
    """A reproducible stand-in for that day's random salt."""
    return hashlib.blake2b(day.isoformat().encode("utf-8"), key=DEMO_SEED, digest_size=32).digest()


def cookie_id(person: str) -> str:
    """What a cookie would hold: one value, assigned once, never derived.

    A UUID from the person alone -- no site, no day -- because that is the
    whole of the difference. It cannot expire, because nothing about it is
    computed from anything that expires.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"beacon-demo-cookie:{person}"))


def identity(*, person: str, site: str, day_offset: int, today: dt.date | None = None) -> Identity:
    """What each approach stores for one person, on one site, on one day."""
    if person not in PEOPLE:
        person = next(iter(PEOPLE))
    if site not in SITES:
        site = SITES[0]
    if day_offset not in DAYS:
        day_offset = 0

    day = (today or dt.datetime.now(dt.UTC).date()) + dt.timedelta(days=day_offset)
    ip, user_agent = PEOPLE[person]

    return Identity(
        person=person,
        site=site,
        day_offset=day_offset,
        day=day,
        beacon_id=visitor_id(salt=demo_salt(day), site_id=site, ip=ip, user_agent=user_agent),
        cookie_id=cookie_id(person),
        # Two days of retention: a salt for a day beyond that has been deleted,
        # so nothing can recompute the identifier that used it.
        salt_still_exists=day_offset < 2,
    )
