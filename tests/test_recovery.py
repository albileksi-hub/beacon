"""Password recovery: what a reset link opens, and what it must not.

Before this existed a forgotten password was permanent -- no reset, no mail,
and no way in through manage.py either. Most of what follows is about the
edges, because a recovery flow is a second front door and every one of these
is a way of leaving it ajar.
"""

import datetime as dt

import pytest
from sqlalchemy import select

from app.config import Settings
from app.models import PasswordReset, User
from app.services import mail, recovery
from app.services.passwords import InvalidPassword, verify_password
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD

NEW_PASSWORD = "a-brand-new-passphrase"


@pytest.fixture
def link(db_session, account):
    """A live reset token for the seeded account."""
    issued = recovery.begin(db_session, email=OWNER_EMAIL)
    assert issued is not None
    return issued[1]


# --- issuing ---------------------------------------------------------------


def test_an_unknown_address_yields_no_link(db_session, account):
    assert recovery.begin(db_session, email="nobody@example.com") is None


def test_the_form_answers_the_same_either_way(client, account):
    """The refusal above must not be visible from outside.

    A reset form that says "no such account" is a way of asking which addresses
    are registered, one guess at a time.
    """
    known = client.post("/forgot", data={"email": OWNER_EMAIL})
    unknown = client.post("/forgot", data={"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 200
    # Same page, same words, whichever address was typed.
    assert known.text == unknown.text


def test_the_plaintext_token_is_never_stored(db_session, account, link):
    stored = db_session.scalars(select(PasswordReset)).all()

    assert len(stored) == 1
    assert link not in stored[0].digest
    assert stored[0].digest == recovery.digest_of(link)


# --- redeeming -------------------------------------------------------------


def test_a_link_sets_the_password(db_session, account, link):
    user = recovery.redeem(db_session, presented=link, new_password=NEW_PASSWORD)

    assert user is not None
    assert verify_password(NEW_PASSWORD, user.password_hash)


def test_a_link_works_once(db_session, account, link):
    recovery.redeem(db_session, presented=link, new_password=NEW_PASSWORD)

    assert recovery.redeem(db_session, presented=link, new_password="another-one-entirely") is None


def test_an_expired_link_is_refused(db_session, account, link):
    later = dt.datetime.now(dt.UTC) + recovery.TTL + dt.timedelta(seconds=1)

    assert recovery.redeem(db_session, presented=link, new_password=NEW_PASSWORD, now=later) is None
    assert not recovery.is_live(db_session, link, now=later)


def test_a_token_nobody_issued_is_refused(db_session, account):
    assert recovery.redeem(db_session, presented="invented", new_password=NEW_PASSWORD) is None
    assert not recovery.is_live(db_session, "invented")


def test_redeeming_one_link_spends_every_other(db_session, account):
    """The case that matters when the mailbox itself was the problem.

    Somebody who can read the mail can request as many links as they like. If
    only the redeemed one were spent, the reset meant to lock them out would
    leave the rest of their collection working.
    """
    first = recovery.begin(db_session, email=OWNER_EMAIL)
    second = recovery.begin(db_session, email=OWNER_EMAIL)
    assert first is not None and second is not None

    recovery.redeem(db_session, presented=first[1], new_password=NEW_PASSWORD)

    assert not recovery.is_live(db_session, second[1])


def test_a_rejected_password_leaves_the_link_usable(db_session, account, link):
    """A password that fails the rules is a typo, not a spent link."""
    with pytest.raises(InvalidPassword):
        recovery.redeem(db_session, presented=link, new_password="short")

    assert recovery.is_live(db_session, link)


# --- sessions --------------------------------------------------------------


def test_resetting_a_password_ejects_existing_sessions(client, db_session, account):
    """The half that is easy to leave out.

    Resetting a password you believe is compromised has to sign out whoever
    compromised it. Without the epoch check, the old cookie keeps working and
    the reset achieves nothing.
    """
    client.post("/login", data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert client.get("/sites").status_code == 200

    issued = recovery.begin(db_session, email=OWNER_EMAIL)
    assert issued is not None
    recovery.redeem(db_session, presented=issued[1], new_password=NEW_PASSWORD)

    # Same cookie jar, now refused.
    assert client.get("/sites").status_code in (302, 303, 401)


def test_the_new_password_signs_in(client, db_session, account, link):
    client.post(f"/reset/{link}", data={"password": NEW_PASSWORD})
    client.post("/logout")

    signed_in = client.post(
        "/login", data={"email": OWNER_EMAIL, "password": NEW_PASSWORD}, follow_redirects=False
    )

    assert signed_in.status_code == 303


def test_the_old_password_stops_working(client, db_session, account, link):
    recovery.redeem(db_session, presented=link, new_password=NEW_PASSWORD)

    refused = client.post("/login", data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})

    assert refused.status_code == 401


# --- the pages -------------------------------------------------------------


def test_the_form_is_offered_for_a_live_link(client, account, link):
    page = client.get(f"/reset/{link}")

    assert page.status_code == 200
    assert f"/reset/{link}" in page.text


def test_a_dead_link_offers_no_form(client, account, link):
    client.post(f"/reset/{link}", data={"password": NEW_PASSWORD})

    page = client.get(f"/reset/{link}")

    assert page.status_code == 400
    assert "expired" in page.text.lower()


def test_posting_to_a_dead_link_is_refused(client, account, link):
    client.post(f"/reset/{link}", data={"password": NEW_PASSWORD})

    again = client.post(f"/reset/{link}", data={"password": "yet-another-passphrase"})

    assert again.status_code == 400


def test_a_short_password_is_reported_rather_than_crashed(client, account, link):
    response = client.post(f"/reset/{link}", data={"password": "short"})

    assert response.status_code == 400
    assert "at least" in response.text


def test_someone_already_signed_in_is_sent_onwards(signed_in):
    assert signed_in.get("/forgot", follow_redirects=False).status_code == 303


def test_asking_too_often_is_throttled(client, account):
    """Throttled on the requester, not the address.

    Rate limiting per address would let anybody lock a known account out of its
    own recovery by asking for links on its behalf.
    """
    for _ in range(6):
        last = client.post("/forgot", data={"email": OWNER_EMAIL})

    assert last.status_code == 429


def test_the_reset_throttle_does_not_lock_sign_in(client, account):
    """Separate counters, which is the point of the purpose argument."""
    for _ in range(6):
        client.post("/forgot", data={"email": OWNER_EMAIL})

    signed_in = client.post(
        "/login", data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert signed_in.status_code == 200


# --- housekeeping ----------------------------------------------------------


def test_spent_and_expired_rows_are_purgeable(db_session, account):
    live = recovery.begin(db_session, email=OWNER_EMAIL)
    spent = recovery.begin(db_session, email=OWNER_EMAIL)
    assert live is not None and spent is not None
    recovery.redeem(db_session, presented=spent[1], new_password=NEW_PASSWORD)

    # Redeeming spends both, so re-issue one that is still live.
    fresh = recovery.begin(db_session, email=OWNER_EMAIL)
    assert fresh is not None

    removed = recovery.purge_expired(db_session)

    assert removed == 2
    assert recovery.is_live(db_session, fresh[1])


def test_deleting_an_account_takes_its_links_with_it(db_session, account, link):
    db_session.delete(db_session.get(User, account.id))
    db_session.commit()

    assert db_session.scalars(select(PasswordReset)).all() == []


# --- delivery --------------------------------------------------------------


def test_without_a_relay_the_link_goes_to_the_log(caplog):
    """Deliberate: a self-hosted box often has no relay, and refusing to issue
    resets at all would lock the operator out of their own install."""
    settings = Settings(smtp_host=None)

    sent = mail.deliver(settings, to="someone@example.com", subject="Hello", body="Body")

    assert sent is False
    assert any("not sent" in record.message for record in caplog.records)


def test_a_relay_is_used_when_there_is_one(monkeypatch):
    captured: dict[str, object] = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            captured["host"] = host
            captured["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self):
            captured["starttls"] = True

        def login(self, username, password):
            captured["login"] = username

        def send_message(self, message):
            captured["to"] = message["To"]

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    settings = Settings(
        smtp_host="mail.example", smtp_username="u", smtp_password="p", smtp_starttls=True
    )

    assert mail.deliver(settings, to="someone@example.com", subject="S", body="B") is True
    assert captured["host"] == "mail.example"
    assert captured["starttls"] is True
    assert captured["login"] == "u"
    assert captured["to"] == "someone@example.com"


def test_a_relay_that_fails_does_not_raise(monkeypatch):
    """A mail outage must not become a way of asking which addresses exist."""

    def explode(*_args, **_kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(mail.smtplib, "SMTP", explode)

    settings = Settings(smtp_host="mail.example")
    assert mail.deliver(settings, to="a@b.c", subject="S", body="B") is False


def test_the_link_that_is_sent_actually_works(client, db_session, account, monkeypatch):
    """End to end: whatever lands in the message is a token that opens the form."""
    sent: dict[str, str] = {}
    monkeypatch.setattr(
        mail, "deliver", lambda settings, *, to, subject, body: sent.update(body=body) or True
    )

    client.post("/forgot", data={"email": OWNER_EMAIL})

    token = sent["body"].split("/reset/", 1)[1].split()[0]
    assert client.get(f"/reset/{token}").status_code == 200


def test_a_cookie_for_a_deleted_account_opens_nothing(client, db_session, account):
    """The row can go while the signed cookie naming it is still in a browser."""
    client.post("/login", data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert client.get("/sites").status_code == 200

    db_session.delete(db_session.get(User, account.id))
    db_session.commit()

    assert client.get("/sites").status_code in (302, 303, 401)


def test_the_form_is_offered_to_someone_signed_out(client):
    page = client.get("/forgot")

    assert page.status_code == 200
    assert 'action="/forgot"' in page.text
