"""API keys: what they open, and what they must not.

The whole point of the design is that a key is strictly weaker than a session.
Most of these tests are about the "not".
"""

import datetime as dt

import pytest
from sqlalchemy import event, select

from app.models import ApiToken, User
from app.services import accounts, tokens
from tests.conftest import OWNER_EMAIL, SITE_DOMAIN


@pytest.fixture
def key(db_session, account):
    _, plaintext = tokens.create(db_session, owner=account, name="status page")
    return plaintext


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_a_key_reads_the_stats_api_without_a_session(client, site, key):
    """The reason this exists: numbers reachable by something that is not a browser."""
    response = client.get(f"/api/stats/{SITE_DOMAIN}/summary", headers=bearer(key))

    assert response.status_code == 200
    assert "visitors" in response.json()


def test_without_a_key_the_same_request_is_refused(client, site):
    assert client.get(f"/api/stats/{SITE_DOMAIN}/summary").status_code == 404


def test_a_key_cannot_change_anything(client, site, key):
    """The boundary the design rests on.

    Reads resolve through require_readable_site, which accepts a key. Every
    route that changes something resolves through require_owned_site, which
    goes via RequiredUser and only ever looks at the session -- so a leaked key
    cannot publish a dashboard, move a timezone, or add a site.
    """
    for path, payload in (
        (f"/sites/{SITE_DOMAIN}/visibility", {"public": "true"}),
        (f"/sites/{SITE_DOMAIN}/timezone", {"timezone": "Europe/Berlin"}),
        ("/sites", {"domain": "elsewhere.example"}),
        ("/keys", {"name": "another"}),
    ):
        response = client.post(path, data=payload, headers=bearer(key), follow_redirects=False)
        assert response.status_code == 401, path


@pytest.mark.parametrize(
    "presented",
    [
        "beacon_completelymadeup",
        "not-even-the-right-shape",
        "",
        "Bearer",
    ],
)
def test_a_key_that_belongs_to_nobody_opens_nothing(client, site, presented):
    response = client.get(f"/api/stats/{SITE_DOMAIN}/summary", headers=bearer(presented))

    assert response.status_code == 404


def test_a_wrong_scheme_is_not_mistaken_for_a_key(client, site, key):
    response = client.get(
        f"/api/stats/{SITE_DOMAIN}/summary", headers={"authorization": f"Basic {key}"}
    )

    assert response.status_code == 404


def test_the_plaintext_is_never_stored(db_session, account):
    """What is kept is a digest, so the key cannot be recovered from a backup."""
    _, plaintext = tokens.create(db_session, owner=account, name="ci")

    stored = db_session.scalars(select(ApiToken)).all()
    assert len(stored) == 1
    assert plaintext not in stored[0].digest
    assert stored[0].digest == tokens.digest_of(plaintext)
    assert plaintext.startswith(tokens.PREFIX)


def test_the_key_is_shown_once_and_then_never_again(signed_in, db_session, account):
    created = signed_in.post("/keys", data={"name": "laptop"})
    assert created.status_code == 200

    shown = [line for line in created.text.splitlines() if tokens.PREFIX in line]
    assert shown, "the plaintext should appear in the response that created it"

    later = signed_in.get("/")
    assert tokens.PREFIX not in later.text
    assert "laptop" in later.text


def test_revoking_a_key_stops_it_working(client, site, db_session, account):
    """Deliberately without the signed_in fixture.

    signed_in returns the same TestClient, so asking for both would leave a
    session cookie on every request -- and the reads would pass with the key
    revoked, or with no key at all, while appearing to prove the opposite.
    """
    _, plaintext = tokens.create(db_session, owner=account, name="doomed")
    token_id = db_session.scalars(select(ApiToken)).one().id
    summary = f"/api/stats/{SITE_DOMAIN}/summary"

    assert client.get(summary, headers=bearer(plaintext)).status_code == 200

    assert tokens.revoke(db_session, owner=account, token_id=token_id) is True

    assert client.get(summary, headers=bearer(plaintext)).status_code == 404


def test_one_account_cannot_revoke_anothers_key(db_session, account):
    stranger = accounts.register(
        db_session, email="someone@else.example", password="a-fine-password"
    )
    _, plaintext = tokens.create(db_session, owner=account, name="mine")
    token_id = db_session.scalars(select(ApiToken)).one().id

    assert tokens.revoke(db_session, owner=stranger, token_id=token_id) is False
    assert tokens.resolve(db_session, plaintext) is not None


def test_revoking_something_that_is_not_there_is_not_an_error(signed_in, db_session):
    response = signed_in.post("/keys/4242/revoke", follow_redirects=False)

    assert response.status_code == 303


def test_an_account_may_not_hoard_keys(db_session, account):
    for number in range(tokens.MAX_PER_ACCOUNT):
        tokens.create(db_session, owner=account, name=f"key {number}")

    with pytest.raises(tokens.TooManyTokens):
        tokens.create(db_session, owner=account, name="one too many")


def test_the_cap_is_reported_rather_than_crashed(signed_in, db_session, account):
    for number in range(tokens.MAX_PER_ACCOUNT):
        tokens.create(db_session, owner=account, name=f"key {number}")

    response = signed_in.post("/keys", data={"name": "one too many"})

    assert response.status_code == 400
    assert "revoke one" in response.text


@pytest.mark.parametrize("name", ["", "   "])
def test_a_nameless_key_is_refused(db_session, account, name):
    """Several keys with no name cannot be told apart when one has to go."""
    with pytest.raises(tokens.InvalidTokenName):
        tokens.create(db_session, owner=account, name=name)


def test_a_nameless_key_is_reported_rather_than_crashed(signed_in):
    response = signed_in.post("/keys", data={"name": "   "})

    assert response.status_code == 400
    assert "name" in response.text.lower()


def test_a_long_name_is_trimmed_rather_than_refused(db_session, account):
    token, _ = tokens.create(db_session, owner=account, name="x" * 200)

    assert len(token.name) == tokens.MAX_NAME


def test_use_is_recorded_as_a_day_not_a_moment(db_session, account, key):
    """A timestamp per call would accumulate into a log of when someone works."""
    assert db_session.scalars(select(ApiToken)).one().last_used_on is None

    tokens.resolve(db_session, key)

    recorded = db_session.scalars(select(ApiToken)).one().last_used_on
    assert recorded == dt.datetime.now(dt.UTC).date()
    assert isinstance(recorded, dt.date) and not isinstance(recorded, dt.datetime)


def test_a_second_use_on_the_same_day_writes_nothing(db_session, account, key):
    """Otherwise a busy key writes a row on every single request.

    Asserted by watching the statements rather than the value, because the
    value is the same either way -- which is exactly what makes an accidental
    write on every request invisible.
    """
    tokens.resolve(db_session, key)

    updates: list[str] = []
    engine = db_session.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE"):
            updates.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert tokens.resolve(db_session, key) is not None
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert updates == [], "a repeat use on the same day should not write"


def test_a_use_on_a_later_day_does_record_it(db_session, account, key):
    tokens.resolve(db_session, key)
    stored = db_session.scalars(select(ApiToken)).one()
    stored.last_used_on = dt.date(2020, 1, 1)
    db_session.commit()

    tokens.resolve(db_session, key)

    assert db_session.scalars(select(ApiToken)).one().last_used_on == dt.datetime.now(
        dt.UTC
    ).date()


def test_keys_are_listed_newest_first(db_session, account):
    for name in ("first", "second", "third"):
        tokens.create(db_session, owner=account, name=name)

    # Stamped by hand, so this tests the ordering rather than the clock's
    # resolution across three commits in the same millisecond.
    for offset, token in enumerate(db_session.scalars(select(ApiToken).order_by(ApiToken.id))):
        token.created_at = dt.datetime(2026, 8, 20 + offset, tzinfo=dt.UTC)
    db_session.commit()

    listed = [token.name for token in tokens.for_owner(db_session, account)]
    assert listed == ["third", "second", "first"]


def test_deleting_an_account_takes_its_keys_with_it(db_session, account, key):
    db_session.delete(db_session.scalars(select(User).where(User.email == OWNER_EMAIL)).one())
    db_session.commit()

    assert db_session.scalars(select(ApiToken)).all() == []


@pytest.fixture
def stranger(db_session):
    """A second account, with a private site of its own."""
    other = accounts.register(
        db_session, email="stranger@example.com", password="s3cret-pass-phrase"
    )
    accounts.add_site(db_session, owner=other, domain="red-bowl.example")
    return other


def test_a_key_cannot_read_another_accounts_private_site(client, site, key, stranger):
    """The tenancy boundary, on the read side.

    There was a test that one account cannot revoke another's key, which is a
    write. Nothing covered the read -- and reading is the only thing a key is
    for, so this is the one boundary the whole feature rests on. A regression
    here would hand every key holder everybody else's numbers while every
    other test in this file still passed.
    """
    for path in (
        "/api/stats/red-bowl.example/summary",
        "/api/stats/red-bowl.example/timeseries",
        "/api/stats/red-bowl.example/breakdown/page",
        "/api/stats/red-bowl.example/live",
    ):
        response = client.get(path, headers=bearer(key))
        assert response.status_code == 404, path


def test_a_key_cannot_export_another_accounts_private_site(client, site, key, stranger):
    """The CSV route resolves through the same guard, and hands over everything.

    A summary leak is a number; this one is the whole aggregate table, so it
    is worth pinning separately rather than trusting that both routes keep
    using the same dependency.
    """
    response = client.get("/sites/red-bowl.example/export.csv", headers=bearer(key))

    assert response.status_code == 404


def test_a_key_does_read_another_accounts_site_once_it_is_published(
    client, site, key, stranger, db_session
):
    """The other half of the rule, so the 404s above are not just a broken route.

    Without this, a guard that refused everything would pass the two tests
    above and nobody would notice the API had stopped working.
    """
    published = accounts.owned_site(db_session, owner=stranger, domain="red-bowl.example")
    accounts.set_visibility(db_session, site=published, public=True)

    response = client.get("/api/stats/red-bowl.example/summary", headers=bearer(key))

    assert response.status_code == 200
