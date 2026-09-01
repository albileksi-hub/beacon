"""Executable statement of the product's central promise.

If any of these fail, the privacy claim in the README is a lie.
"""

import datetime as dt

from sqlalchemy import select

from app.db import Base
from app.models import Event
from tests.conftest import CHROME_MAC

SENSITIVE_URL = "https://blue-mug.example/checkout?email=someone@example.com&token=s3cr3t-abc"
CLIENT_ADDRESS = "203.0.113.77"

# Every table derived from somebody's network address. The account tables are
# out of scope on purpose: a customer's own email is something they typed into a
# signup form, not something collected from a person browsing their site.
#
# login_attempts belongs here even though it is the operator's own security
# machinery -- rate limiting normally keeps a list of addresses, and the promise
# not to store one should not have an exception carved into it for our benefit.
ADDRESS_DERIVED_TABLES = {"events", "daily_salts", "login_attempts"}


def _dump_every_row(db_session, *, skip_temporal: bool = False) -> str:
    """Every value in every table, as one searchable blob.

    ``skip_temporal`` leaves out dates and timestamps. A substring search is the
    right shape for the secrets here -- they are long and distinctive, and the
    point is that they appear nowhere at all -- but it is the wrong shape for a
    short run of digits, because a timestamp is full of those.

    The viewport check found that out on its own. Width 1437 and a row written
    at 23:07:53.143749 fail `"1437" not in ...` on the microseconds, and the
    test that guards this project's central privacy promise goes red for
    reasons that have nothing to do with privacy. Roughly three in ten thousand
    per timestamp, which is rare enough to look like a real failure and common
    enough to arrive eventually -- it turned up on a routine dependency bump.
    """
    import datetime as dt

    values = []
    for table in Base.metadata.sorted_tables:
        for row in db_session.execute(select(table)).all():
            for value in row:
                if skip_temporal and isinstance(value, dt.date | dt.datetime | dt.time):
                    continue
                values.append(str(value))
    return " | ".join(values)


def test_no_identifying_request_data_is_ever_persisted(client, db_session, site):
    response = client.post(
        "/api/event",
        json={
            "site_id": "blue-mug.example",
            "url": SENSITIVE_URL,
            "referrer": "https://mail.example/inbox?user=someone@example.com",
            "screen_width": 1437,
        },
        headers={"user-agent": CHROME_MAC, "x-forwarded-for": CLIENT_ADDRESS},
    )
    assert response.status_code == 202

    # The event must actually have been stored, or this proves nothing.
    stored = _dump_every_row(db_session)
    assert "/checkout" in stored

    assert "someone@example.com" not in stored, "an email address reached the database"
    assert "s3cr3t-abc" not in stored, "a token reached the database"
    assert CLIENT_ADDRESS not in stored, "an IP address reached the database"
    assert CHROME_MAC not in stored, "a raw User-Agent reached the database"
    assert "checkout?" not in stored, "a query string reached the database"
    # Against the non-temporal columns: 1437 is four digits, and a timestamp is
    # a haystack of digits. See _dump_every_row.
    assert "1437" not in _dump_every_row(db_session, skip_temporal=True), (
        "an exact viewport width reached the database"
    )


def test_no_address_derived_table_can_hold_an_address_or_user_agent():
    """A schema-level guard: the columns simply do not exist."""
    columns = {
        column.name
        for table in Base.metadata.sorted_tables
        if table.name in ADDRESS_DERIVED_TABLES
        for column in table.columns
    }
    forbidden = {"ip", "ip_address", "remote_addr", "user_agent", "cookie", "session_id", "email"}

    assert columns & forbidden == set()


def test_a_timestamp_cannot_masquerade_as_a_leaked_viewport_width(client, db_session, site):
    """The guard above went red on a dependency bump, for no privacy reason.

    Width 1437, row written at 23:07:53.143749, and `"1437" not in dump` finds
    it in the microseconds. A short run of digits searched against a blob full
    of timestamps collides eventually -- roughly three in ten thousand per
    timestamp, which is rare enough to read as a real failure and common enough
    to arrive. The test that guards this project's central promise is the worst
    possible place for a false alarm: the first instinct on seeing it fail is
    that something leaked.

    Both directions are asserted, because narrowing the haystack is only
    correct while the guard still fires on an actual leak.
    """
    client.post(
        "/api/event",
        json={
            "site_id": "blue-mug.example",
            "url": SENSITIVE_URL,
            "screen_width": 1437,
        },
        headers={"user-agent": CHROME_MAC},
    )
    event = db_session.scalars(select(Event)).one()
    event.timestamp = dt.datetime(2026, 8, 31, 23, 7, 53, 143749, tzinfo=dt.UTC)
    db_session.commit()

    assert "1437" in _dump_every_row(db_session), "the collision this test is about is gone"
    assert "1437" not in _dump_every_row(db_session, skip_temporal=True)

    event.screen = "1437"
    db_session.commit()

    assert "1437" in _dump_every_row(db_session, skip_temporal=True), (
        "a width that really was stored must still be caught"
    )
