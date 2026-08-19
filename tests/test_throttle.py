import datetime as dt

from sqlalchemy import select

from app.models import LoginAttempt
from app.services import throttle, visitors
from app.services.throttle import MAX_FAILURES, WINDOW

NOW = dt.datetime(2026, 8, 18, 12, 0, tzinfo=dt.UTC)
ADDRESS = "203.0.113.42"


def test_the_address_is_never_stored(db_session):
    marker = throttle.fingerprint(db_session, ADDRESS)
    throttle.record_failure(db_session, marker, now=NOW)

    stored = db_session.scalars(select(LoginAttempt)).one()
    assert ADDRESS not in stored.fingerprint
    assert len(stored.fingerprint) == 32


def test_the_same_address_resolves_to_the_same_marker(db_session):
    assert throttle.fingerprint(db_session, ADDRESS) == throttle.fingerprint(db_session, ADDRESS)


def test_different_addresses_are_throttled_separately(db_session):
    other = throttle.fingerprint(db_session, "198.51.100.9")

    assert throttle.fingerprint(db_session, ADDRESS) != other


def test_a_login_marker_cannot_collide_with_a_visitor_id(db_session):
    """Domain separation: the two tables must not be cross-referenceable."""
    salt = visitors.current_salt(db_session)

    as_visitor = visitors.visitor_id(salt=salt, site_id="login", ip=ADDRESS, user_agent="")
    as_login = throttle.fingerprint(db_session, ADDRESS)

    assert as_visitor != as_login


def test_a_few_failures_do_not_lock_anyone_out(db_session):
    marker = throttle.fingerprint(db_session, ADDRESS)
    for _ in range(MAX_FAILURES - 1):
        throttle.record_failure(db_session, marker, now=NOW)

    assert not throttle.is_locked(db_session, marker, now=NOW)


def test_enough_failures_lock_the_address_out(db_session):
    marker = throttle.fingerprint(db_session, ADDRESS)
    for _ in range(MAX_FAILURES):
        throttle.record_failure(db_session, marker, now=NOW)

    assert throttle.is_locked(db_session, marker, now=NOW)


def test_the_lockout_expires(db_session):
    marker = throttle.fingerprint(db_session, ADDRESS)
    for _ in range(MAX_FAILURES):
        throttle.record_failure(db_session, marker, now=NOW)

    later = NOW + WINDOW + dt.timedelta(minutes=1)
    assert not throttle.is_locked(db_session, marker, now=later)


def test_signing_in_successfully_forgets_the_failures(db_session):
    marker = throttle.fingerprint(db_session, ADDRESS)
    for _ in range(MAX_FAILURES):
        throttle.record_failure(db_session, marker, now=NOW)

    throttle.clear(db_session, marker)

    assert throttle.recent_failures(db_session, marker, now=NOW) == 0
    assert not throttle.is_locked(db_session, marker, now=NOW)


def test_one_address_cannot_lock_out_another(db_session):
    attacker = throttle.fingerprint(db_session, ADDRESS)
    bystander = throttle.fingerprint(db_session, "198.51.100.9")
    for _ in range(MAX_FAILURES):
        throttle.record_failure(db_session, attacker, now=NOW)

    assert throttle.is_locked(db_session, attacker, now=NOW)
    assert not throttle.is_locked(db_session, bystander, now=NOW)


def test_expired_attempts_are_swept_up(db_session):
    marker = throttle.fingerprint(db_session, ADDRESS)
    throttle.record_failure(db_session, marker, now=NOW - dt.timedelta(hours=2))
    throttle.record_failure(db_session, marker, now=NOW)

    # Recording the second failure already purged the first.
    assert len(db_session.scalars(select(LoginAttempt)).all()) == 1
