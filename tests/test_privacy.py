"""Executable statement of the product's central promise.

If any of these fail, the privacy claim in the README is a lie.
"""

from sqlalchemy import select

from app.db import Base
from tests.conftest import CHROME_MAC

SENSITIVE_URL = "https://blue-mug.example/checkout?email=someone@example.com&token=s3cr3t-abc"
CLIENT_ADDRESS = "203.0.113.77"


def _dump_every_row(db_session) -> str:
    """Every value in every table, as one searchable blob."""
    values = []
    for table in Base.metadata.sorted_tables:
        for row in db_session.execute(select(table)).all():
            values.extend(str(value) for value in row)
    return " | ".join(values)


def test_no_identifying_request_data_is_ever_persisted(client, db_session):
    response = client.post(
        "/api/event",
        json={
            "site_id": "blue-mug.example",
            "url": SENSITIVE_URL,
            "referrer": "https://mail.example/inbox?user=someone@example.com",
            "screen_width": 1280,
        },
        headers={"user-agent": CHROME_MAC, "x-forwarded-for": CLIENT_ADDRESS},
    )
    assert response.status_code == 202

    stored = _dump_every_row(db_session)

    assert "someone@example.com" not in stored, "an email address reached the database"
    assert "s3cr3t-abc" not in stored, "a token reached the database"
    assert CLIENT_ADDRESS not in stored, "an IP address reached the database"
    assert CHROME_MAC not in stored, "a raw User-Agent reached the database"
    assert "checkout?" not in stored, "a query string reached the database"


def test_no_column_is_capable_of_holding_an_address_or_user_agent():
    """A schema-level guard: the columns simply do not exist."""
    columns = {
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
    }
    forbidden = {"ip", "ip_address", "remote_addr", "user_agent", "cookie", "session_id", "email"}

    offenders = {name for name in columns if name.split(".")[-1] in forbidden}
    assert offenders == set()
