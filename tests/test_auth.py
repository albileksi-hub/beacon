from sqlalchemy import select

from app.models import Site, User
from app.services import accounts
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD


def test_signing_up_creates_an_account_and_signs_you_in(client, db_session):
    response = client.post(
        "/signup",
        data={"email": "new@example.com", "password": OWNER_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert db_session.scalar(select(User).where(User.email == "new@example.com")) is not None
    assert client.get("/").status_code == 200


def test_signing_up_with_a_taken_email_is_refused(client, account):
    response = client.post(
        "/signup",
        data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "already registered" in response.text


def test_signing_up_with_a_weak_password_is_refused(client, db_session):
    response = client.post(
        "/signup", data={"email": "new@example.com", "password": "short"}, follow_redirects=False
    )

    assert response.status_code == 400
    assert "at least 8 characters" in response.text
    assert db_session.scalars(select(User)).all() == []


def test_signing_in_with_the_wrong_password_is_refused(client, account):
    response = client.post(
        "/login",
        data={"email": OWNER_EMAIL, "password": "not-the-password"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    # One message for both causes, so the form cannot confirm which emails exist.
    assert "do not match" in response.text


def test_signing_in_with_an_unknown_email_gives_the_same_message(client):
    response = client.post(
        "/login",
        data={"email": "ghost@example.com", "password": OWNER_PASSWORD},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "do not match" in response.text


def test_signing_out_ends_the_session(signed_in):
    assert "Your sites" in signed_in.get("/").text

    response = signed_in.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    # The same URL now shows the public page instead of the account.
    assert "Your sites" not in signed_in.get("/").text


def test_login_and_signup_pages_render_for_visitors(client):
    assert "Sign in" in client.get("/login").text
    assert "Create an account" in client.get("/signup").text


def test_signed_in_users_are_sent_home_from_the_auth_pages(signed_in):
    for path in ("/login", "/signup"):
        response = signed_in.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"


def test_adding_a_site(signed_in, db_session, account):
    response = signed_in.post(
        "/sites", data={"domain": "https://www.NewSite.example/pricing"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/sites/newsite.example"

    site = db_session.scalar(select(Site).where(Site.domain == "newsite.example"))
    assert site is not None
    assert site.owner_id == account.id


def test_adding_a_domain_somebody_else_tracks_is_refused(signed_in, db_session):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="taken.example")

    response = signed_in.post("/sites", data={"domain": "taken.example"}, follow_redirects=False)

    assert response.status_code == 400
    assert "already being tracked" in response.text


def test_adding_a_site_requires_signing_in(client):
    assert client.post("/sites", data={"domain": "x.example"}).status_code == 401


def test_adding_a_blank_domain_is_refused(signed_in):
    response = signed_in.post("/sites", data={"domain": "https://"}, follow_redirects=False)

    assert response.status_code == 400
    assert "Enter a domain" in response.text
