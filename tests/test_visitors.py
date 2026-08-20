import datetime as dt

from sqlalchemy import delete, select

from app.models import DailySalt
from app.services.visitors import (
    SALT_BYTES,
    current_salt,
    forget_cached_salts,
    purge_expired_salts,
    visitor_id,
)

MONDAY = dt.date(2026, 8, 17)
TUESDAY = dt.date(2026, 8, 18)

FIXED_SALT = b"\x01" * SALT_BYTES
OTHER_SALT = b"\x02" * SALT_BYTES


SITE = "blue-mug.example"
OTHER_SITE = "other.example"


def salt_for(db, *, site=SITE, day=TUESDAY):
    return current_salt(db, site_id=site, day=day)


def test_one_salt_per_site_per_day_reused_within_the_day(db_session):
    first = salt_for(db_session)
    second = salt_for(db_session)

    assert first == second
    assert len(first) == SALT_BYTES
    assert len(db_session.scalars(select(DailySalt)).all()) == 1


def test_each_day_gets_its_own_random_salt(db_session):
    assert salt_for(db_session, day=MONDAY) != salt_for(db_session, day=TUESDAY)


def test_each_site_gets_its_own_salt_for_the_same_day(db_session):
    """Sites keep their own clocks, so they must keep their own rotations.

    A salt shared across sites would have to rotate at one instant, and that
    instant cannot be midnight in two different zones.
    """
    assert salt_for(db_session, site=SITE) != salt_for(db_session, site=OTHER_SITE)


def test_expired_salts_are_deleted(db_session):
    salt_for(db_session, day=MONDAY - dt.timedelta(days=5))
    salt_for(db_session, day=TUESDAY)

    purge_expired_salts(db_session, today=TUESDAY)

    assert db_session.scalars(select(DailySalt.day)).all() == [TUESDAY]


def test_same_visitor_within_a_day_resolves_to_one_id():
    args = {"site_id": "blue-mug", "ip": "203.0.113.7", "user_agent": "Chrome"}

    assert visitor_id(salt=FIXED_SALT, **args) == visitor_id(salt=FIXED_SALT, **args)


def test_different_people_resolve_to_different_ids():
    first = visitor_id(salt=FIXED_SALT, site_id="s", ip="203.0.113.7", user_agent="Chrome")
    second = visitor_id(salt=FIXED_SALT, site_id="s", ip="203.0.113.8", user_agent="Chrome")

    assert first != second


def test_ids_are_scoped_to_a_single_site():
    """The same person on two customers' sites must not be correlatable."""
    on_site_a = visitor_id(salt=FIXED_SALT, site_id="site-a", ip="203.0.113.7", user_agent="C")
    on_site_b = visitor_id(salt=FIXED_SALT, site_id="site-b", ip="203.0.113.7", user_agent="C")

    assert on_site_a != on_site_b


def test_salt_rotation_breaks_linkability_across_days():
    args = {"site_id": "blue-mug", "ip": "203.0.113.7", "user_agent": "Chrome"}

    assert visitor_id(salt=FIXED_SALT, **args) != visitor_id(salt=OTHER_SALT, **args)


def test_visitor_ids_cannot_be_re_derived_once_the_salt_expires(db_session):
    """The core anonymity claim, made executable.

    After the salt is gone, an attacker holding the events table *and* a
    suspected IP address still cannot reproduce the visitor ID.
    """
    old_day = TUESDAY - dt.timedelta(days=5)
    salt = salt_for(db_session, day=old_day)
    recorded = visitor_id(salt=salt, site_id="blue-mug", ip="203.0.113.7", user_agent="Chrome")

    purge_expired_salts(db_session, today=TUESDAY)

    assert db_session.scalar(select(DailySalt).where(DailySalt.day == old_day)) is None

    # All that is left to guess with is a fresh, unrelated salt.
    replacement = salt_for(db_session, day=TUESDAY)
    attempt = visitor_id(
        salt=replacement, site_id="blue-mug", ip="203.0.113.7", user_agent="Chrome"
    )
    assert attempt != recorded


def test_losing_the_creation_race_falls_back_to_the_winning_salt(db_session, monkeypatch):
    """Two workers can reach for today's salt at once; only one row may win."""
    winner = salt_for(db_session)
    forget_cached_salts()

    # Simulate our read happening before the other worker's insert landed.
    monkeypatch.setattr(type(db_session), "scalar", lambda self, *args, **kwargs: None)

    assert salt_for(db_session) == winner
    assert len(db_session.scalars(select(DailySalt)).all()) == 1


def test_todays_salt_is_only_fetched_once(db_session):
    """It cannot change within the day, and ingest reads it on every event."""
    first = salt_for(db_session)

    # Remove it from the database entirely; the cached value must still win.
    db_session.execute(delete(DailySalt))
    db_session.commit()

    assert salt_for(db_session) == first


def test_the_cache_does_not_carry_a_salt_across_midnight(db_session):
    monday = salt_for(db_session, day=MONDAY)
    tuesday = salt_for(db_session, day=TUESDAY)

    assert monday != tuesday
    assert salt_for(db_session, day=MONDAY) == monday
