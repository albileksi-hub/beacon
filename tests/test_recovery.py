"""Password recovery: what a reset link opens, and what it must not.

Before this existed a forgotten password was permanent -- no reset, no mail,
and no way in through manage.py either. Most of what follows is about the
edges, because a recovery flow is a second front door and every one of these
is a way of leaving it ajar.
"""

import datetime as dt

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import event, select

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


def test_the_reset_mail_is_sent_after_the_response_not_before_it(client, account, monkeypatch):
    """Answering identically is not enough if one answer takes ten seconds.

    The mail used to go out inline, so a registered address paid for a whole
    SMTP conversation and an unregistered one returned immediately. Measured
    against an unreachable relay that was 10,008ms against roughly 1ms: the
    same page, arriving late enough to say what the page would not. A throttle
    of five attempts does nothing about it -- five probes is plenty to tell ten
    seconds from one millisecond.

    Asserted through BackgroundTasks rather than by timing, because the test
    client runs background work inside the request and would show no
    difference. What must not come back is `mail.deliver` on the request path.
    """
    queued: list = []
    original = BackgroundTasks.add_task

    def spy(self, func, *args, **kwargs):
        queued.append(func)
        return original(self, func, *args, **kwargs)

    monkeypatch.setattr(BackgroundTasks, "add_task", spy)

    response = client.post("/forgot", data={"email": account.email})

    assert response.status_code == 200
    # The real function, not a stand-in: patching mail.deliver out would make
    # this pass while proving only that the patch was queued.
    assert mail.deliver in queued, f"the mail did not go through add_task: {queued}"


def test_purging_spent_links_loads_nothing_into_memory(db_session, account):
    """Every other purge here is one set-based delete; this one was a loop.

    Asserting "one DELETE statement" would not have caught the old code, and I
    only found that out by running the old body under the same listener:
    SQLAlchemy batches per-row deletes into a single executemany, so the
    statement count was already 1. The difference that is real is the SELECT in
    front of it -- the old version built an ORM object for every spent link
    before deleting any of them, which is the part that hurts on a backlog.

    So this asserts what actually separates them: nothing is read back, and the
    delete carries one set of parameters rather than one per row.
    """
    now = dt.datetime.now(dt.UTC)
    for i in range(12):
        db_session.add(
            PasswordReset(
                user_id=account.id,
                digest=f"{i:064d}",
                expires_at=now - dt.timedelta(hours=1),
            )
        )
    db_session.commit()

    seen: list[tuple[str, bool]] = []
    engine = db_session.get_bind()

    def record(conn, cursor, statement, parameters, context, executemany):
        head = statement.lstrip().split()[0].upper()
        if head in ("SELECT", "DELETE") and "password_resets" in statement:
            seen.append((head, executemany))

    event.listen(engine, "before_cursor_execute", record)
    try:
        removed = recovery.purge_expired(db_session, now=now)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert removed == 12, "the returned count must still be the number actually removed"
    assert seen == [("DELETE", False)], f"expected one set-based delete, saw {seen}"


def test_a_relay_that_wants_neither_tls_nor_a_login(monkeypatch):
    """The common self-hosted case: a relay on the same box, no auth, no TLS.

    Both conditions in _send_over_smtp had only ever been exercised true, so
    the configuration most likely to be sitting behind a docker-compose stack
    was the one nothing ran. Nothing should be attempted that was not asked
    for -- calling starttls on a relay that does not offer it fails the send.
    """
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self):
            calls.append("starttls")

        def login(self, username, password):
            calls.append("login")

        def send_message(self, message):
            calls.append("send")

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    settings = Settings(smtp_host="localhost", smtp_starttls=False)

    assert mail.deliver(settings, to="a@b.example", subject="S", body="B") is True
    assert calls == ["send"], f"attempted more than asked: {calls}"


def test_a_relay_with_a_username_but_no_password_does_not_half_log_in(monkeypatch):
    """The `and` in that condition, from the side that has never been taken."""
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def starttls(self):
            calls.append("starttls")

        def login(self, username, password):
            calls.append("login")

        def send_message(self, message):
            calls.append("send")

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    settings = Settings(smtp_host="localhost", smtp_starttls=True, smtp_username="u")

    assert mail.deliver(settings, to="a@b.example", subject="S", body="B") is True
    assert calls == ["starttls", "send"], f"logged in without a password: {calls}"


def test_an_already_aware_expiry_is_not_stamped_twice(db_session, account):
    """Postgres returns an aware datetime; SQLite returns a naive one.

    The coverage gate runs on SQLite, so only the naive side of that branch was
    ever taken locally -- the production database exercises the other one.
    """
    now = dt.datetime.now(dt.UTC)
    _user, token = recovery.begin(db_session, email=OWNER_EMAIL)
    stored = db_session.scalars(select(PasswordReset)).one()
    stored.expires_at = now + dt.timedelta(hours=1)  # already aware
    db_session.commit()

    assert recovery.is_live(db_session, token, now=now) is True
    assert recovery.is_live(db_session, token, now=now + dt.timedelta(hours=2)) is False
