import pytest

from app.services import accounts
from app.services.passwords import InvalidPassword
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, SITE_DOMAIN


def test_registering_stores_a_hash_not_the_password(db_session):
    user = accounts.register(db_session, email="Someone@Example.COM", password=OWNER_PASSWORD)

    assert user.email == "someone@example.com"
    assert OWNER_PASSWORD not in user.password_hash


def test_an_email_can_only_be_registered_once(db_session, account):
    with pytest.raises(accounts.EmailAlreadyRegistered):
        accounts.register(db_session, email=OWNER_EMAIL.upper(), password=OWNER_PASSWORD)


def test_registration_enforces_password_rules(db_session):
    with pytest.raises(InvalidPassword):
        accounts.register(db_session, email="new@example.com", password="tiny")


def test_authenticating_with_the_right_password(db_session, account):
    assert accounts.authenticate(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD) == account


def test_authenticating_with_the_wrong_password(db_session, account):
    assert accounts.authenticate(db_session, email=OWNER_EMAIL, password="wrong") is None


def test_authenticating_an_unknown_address(db_session):
    assert accounts.authenticate(db_session, email="ghost@example.com", password="x") is None


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("Example.COM", "example.com"),
        ("https://example.com", "example.com"),
        ("http://www.example.com/pricing", "example.com"),
        ("  example.com  ", "example.com"),
    ],
)
def test_domains_are_normalised(typed, expected):
    assert accounts.normalise_domain(typed) == expected


def test_a_domain_can_only_be_claimed_once(db_session, account, site):
    with pytest.raises(accounts.DomainAlreadyRegistered):
        accounts.add_site(db_session, owner=account, domain=f"https://www.{SITE_DOMAIN}")


def test_owned_site_finds_the_owners_site(db_session, account, site):
    assert accounts.owned_site(db_session, owner=account, domain=SITE_DOMAIN) == site


def test_owned_site_is_blind_to_other_peoples_sites(db_session, account, site):
    intruder = accounts.register(db_session, email="intruder@example.com", password=OWNER_PASSWORD)

    assert accounts.owned_site(db_session, owner=intruder, domain=SITE_DOMAIN) is None


def test_registered_sites_are_recognised(db_session, site):
    assert accounts.site_is_registered(db_session, SITE_DOMAIN)
    assert not accounts.site_is_registered(db_session, "never-registered.example")


def test_a_blank_domain_is_rejected(db_session, account):
    with pytest.raises(accounts.InvalidDomain, match="Enter a domain"):
        accounts.add_site(db_session, owner=account, domain="   https://   ")


def test_sites_start_private(site):
    assert site.public is False


def test_an_owner_can_read_their_own_private_site(db_session, account, site):
    assert accounts.readable_site(db_session, viewer=account, domain=SITE_DOMAIN) == site


def test_a_stranger_cannot_read_a_private_site(db_session, account, site):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)

    assert accounts.readable_site(db_session, viewer=stranger, domain=SITE_DOMAIN) is None
    assert accounts.readable_site(db_session, viewer=None, domain=SITE_DOMAIN) is None


def test_publishing_lets_anybody_read_it(db_session, account, site):
    accounts.set_visibility(db_session, site=site, public=True)

    assert accounts.readable_site(db_session, viewer=None, domain=SITE_DOMAIN) == site


def test_unpublishing_takes_it_back(db_session, account, site):
    accounts.set_visibility(db_session, site=site, public=True)
    accounts.set_visibility(db_session, site=site, public=False)

    assert accounts.readable_site(db_session, viewer=None, domain=SITE_DOMAIN) is None


def test_a_domain_nobody_registered_is_never_readable(db_session, account):
    assert accounts.readable_site(db_session, viewer=account, domain="ghost.example") is None
