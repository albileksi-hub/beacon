"""Executable statement of the product's central promise.

If any of these fail, the privacy claim in the README is a lie.
"""

from sqlalchemy import select

from app.db import Base
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


def _dump_every_row(db_session) -> str:
    """Every value in every table, as one searchable blob."""
    values = []
    for table in Base.metadata.sorted_tables:
        for row in db_session.execute(select(table)).all():
            values.extend(str(value) for value in row)
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
    assert "1437" not in stored, "an exact viewport width reached the database"


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
