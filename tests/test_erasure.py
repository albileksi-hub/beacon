"""Deleting a site or an account, and leaving nothing behind.

The interesting assertion in this file is not that a row disappears. It is
that no row survives: events, salts and both rollup tables key on the domain
as a plain string with no foreign key to cascade along, so deleting the Site
row alone would leave all of them queryable by whoever registered the domain
next. A delete that orphans the data is the same promise with none of the
effect, which is worse than not offering one.
"""

import datetime as dt

import pytest
from sqlalchemy import func, select

from app.models import DailySalt, DailyStat, Event, HourlyStat, Membership, Role, Site, User
from app.services import accounts, erasure, rollups
from app.services.visitors import current_salt
from tests.conftest import OWNER_PASSWORD, SITE_DOMAIN


def _populate(db, domain: str, *, days: int = 3) -> None:
    """Give a site something in every table that keys on its domain."""
    today = dt.datetime.now(dt.UTC).date()
    for offset in range(days):
        day = today - dt.timedelta(days=offset)
        salt = current_salt(db, site_id=domain, day=day)
        for index in range(4):
            db.add(
                Event(
                    site_id=domain,
                    visitor_id=f"visitor-{index}",
                    timestamp=dt.datetime.now(dt.UTC) - dt.timedelta(days=offset),
                    day=day,
                    hour=9,
                    name="pageview",
                    pathname=f"/page-{index}",
                    source="Direct",
                    browser="Chrome",
                    os="Windows",
                    device="desktop",
                    country="DE",
                    screen="Laptop",
                )
            )
        assert salt
    db.commit()
    for offset in range(days):
        day = today - dt.timedelta(days=offset)
        rollups.rebuild_day(db, site_id=domain, day=day)
        rollups.rebuild_hours(db, site_id=domain, day=day)


def _rows_for(db, domain: str) -> dict[str, int]:
    """How many rows every domain-keyed table still holds for this site."""
    counts = {}
    for table in erasure.tables_keyed_by_domain():
        counts[table.name] = db.scalar(
            select(func.count()).select_from(table).where(table.c.site_id == domain)
        )
    return counts


# --- what the schema forces this module to do ------------------------------


def test_the_domain_keyed_tables_are_found_from_the_schema() -> None:
    """A hand-maintained list is how the next such table gets forgotten."""
    names = {table.name for table in erasure.tables_keyed_by_domain()}

    assert names == {"events", "daily_salts", "daily_stats", "hourly_stats"}


def test_sites_itself_is_not_treated_as_one_of_them() -> None:
    """sites.domain names the site; it is not a reference to one."""
    assert "sites" not in {table.name for table in erasure.tables_keyed_by_domain()}


# --- deleting a site -------------------------------------------------------


def test_deleting_a_site_leaves_nothing_keyed_to_its_domain(db_session, site):
    _populate(db_session, SITE_DOMAIN)
    before = _rows_for(db_session, SITE_DOMAIN)
    assert all(count > 0 for count in before.values()), before

    erasure.delete_site(db_session, site=site)

    assert _rows_for(db_session, SITE_DOMAIN) == dict.fromkeys(before, 0)
    assert db_session.scalar(select(Site).where(Site.domain == SITE_DOMAIN)) is None


def test_it_reports_what_it_removed(db_session, site):
    _populate(db_session, SITE_DOMAIN)

    removed = erasure.delete_site(db_session, site=site)

    assert removed["events"] == 12
    assert removed["sites"] == 1
    assert removed["daily_salts"] > 0


def test_another_sites_data_is_untouched(db_session, account, site):
    """The delete is scoped by domain, and the test says so rather than assuming."""
    other = accounts.add_site(db_session, owner=account, domain="other.example")
    _populate(db_session, SITE_DOMAIN)
    _populate(db_session, "other.example")

    erasure.delete_site(db_session, site=site)

    assert all(count > 0 for count in _rows_for(db_session, "other.example").values())
    assert db_session.get(Site, other.id) is not None


def test_memberships_go_with_the_site(db_session, account, site):
    """These do cascade, by foreign key. Asserted anyway: the point of the
    module is that everything goes, not that most of it does."""
    accounts.register(db_session, email="guest@example.com", password=OWNER_PASSWORD)
    accounts.add_member(db_session, site=site, email="guest@example.com", role=Role.VIEWER)

    erasure.delete_site(db_session, site=site)

    assert db_session.scalars(select(Membership)).all() == []


# --- deleting an account ---------------------------------------------------


def test_deleting_an_account_takes_every_site_it_owns(db_session, account, site):
    accounts.add_site(db_session, owner=account, domain="second.example")
    _populate(db_session, SITE_DOMAIN)
    _populate(db_session, "second.example")

    erasure.delete_account(db_session, user=account)

    assert db_session.scalars(select(Site)).all() == []
    assert _rows_for(db_session, SITE_DOMAIN) == _rows_for(db_session, "second.example")
    assert all(count == 0 for count in _rows_for(db_session, SITE_DOMAIN).values())
    assert db_session.scalars(select(User)).all() == []


def test_somebody_elses_site_survives_your_account(db_session, account, site):
    """Being a member of a site is not owning it."""
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    theirs = accounts.add_site(db_session, owner=stranger, domain="theirs.example")
    accounts.add_member(db_session, site=theirs, email=account.email, role=Role.VIEWER)
    _populate(db_session, "theirs.example")

    erasure.delete_account(db_session, user=account)

    assert db_session.get(Site, theirs.id) is not None
    assert all(count > 0 for count in _rows_for(db_session, "theirs.example").values())


# --- through the app -------------------------------------------------------


def test_the_form_needs_the_domain_typed_exactly(signed_in, db_session, site):
    refused = signed_in.post(f"/sites/{SITE_DOMAIN}/delete", data={"confirm": "not-the-domain"})

    assert refused.status_code == 400
    assert db_session.scalar(select(Site).where(Site.domain == SITE_DOMAIN)) is not None


@pytest.mark.parametrize("typed", [SITE_DOMAIN, SITE_DOMAIN.upper(), f"  {SITE_DOMAIN} "])
def test_the_confirmation_is_forgiving_about_case_and_space(
    signed_in, db_session, account, typed
):
    accounts.add_site(db_session, owner=account, domain=SITE_DOMAIN)

    signed_in.post(f"/sites/{SITE_DOMAIN}/delete", data={"confirm": typed})

    assert db_session.scalar(select(Site).where(Site.domain == SITE_DOMAIN)) is None


def test_a_stranger_cannot_delete_your_site(client, db_session, site):
    """OwnedSite answers 404, the same as a domain that does not exist."""
    accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    client.post("/login", data={"email": "stranger@example.com", "password": OWNER_PASSWORD})

    refused = client.post(f"/sites/{SITE_DOMAIN}/delete", data={"confirm": SITE_DOMAIN})

    assert refused.status_code == 404
    assert db_session.scalar(select(Site).where(Site.domain == SITE_DOMAIN)) is not None


def test_closing_an_account_needs_the_password(signed_in, db_session, account):
    refused = signed_in.post("/account/delete", data={"password": "not-the-password"})

    assert refused.status_code == 401
    assert db_session.get(User, account.id) is not None


def test_the_right_password_closes_it(signed_in, db_session, account, site):
    signed_in.post("/account/delete", data={"password": OWNER_PASSWORD})

    assert db_session.scalars(select(User)).all() == []
    assert db_session.scalars(select(Site)).all() == []


def test_the_session_does_not_outlive_the_account(signed_in, db_session, account):
    signed_in.post("/account/delete", data={"password": OWNER_PASSWORD})

    assert signed_in.get("/sites", follow_redirects=False).status_code in (302, 303, 401)


def test_the_settings_page_is_owner_only(client, db_session, site):
    accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    client.post("/login", data={"email": "stranger@example.com", "password": OWNER_PASSWORD})

    assert client.get(f"/sites/{SITE_DOMAIN}/settings").status_code == 404


def test_the_settings_page_offers_the_delete(signed_in, site):
    page = signed_in.get(f"/sites/{SITE_DOMAIN}/settings")

    assert page.status_code == 200
    assert f'action="/sites/{SITE_DOMAIN}/delete"' in page.text


def test_the_account_page_offers_the_delete(signed_in):
    page = signed_in.get("/account")

    assert page.status_code == 200
    assert 'action="/account/delete"' in page.text


def test_signing_out_is_not_required_to_reach_it(signed_in):
    assert signed_in.get("/account").status_code == 200


def test_the_salts_go_too(db_session, site):
    """The one table whose survival would be a privacy failure rather than
    merely untidy: a surviving salt makes that day's visitor IDs re-derivable."""
    _populate(db_session, SITE_DOMAIN)
    assert db_session.scalars(select(DailySalt).where(DailySalt.site_id == SITE_DOMAIN)).all()

    erasure.delete_site(db_session, site=site)

    assert db_session.scalars(select(DailySalt).where(DailySalt.site_id == SITE_DOMAIN)).all() == []


def test_the_aggregates_go_too(db_session, site):
    """Rollups outlive raw events by design under retention, so they have to be
    removed explicitly rather than assumed gone with the events."""
    _populate(db_session, SITE_DOMAIN)

    erasure.delete_site(db_session, site=site)

    for model in (DailyStat, HourlyStat):
        assert db_session.scalars(select(model).where(model.site_id == SITE_DOMAIN)).all() == []
