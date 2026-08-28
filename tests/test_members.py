"""Who can open a site, and what they can do once they have.

Access used to be sites.owner_id alone, so a dashboard was something exactly
one person could ever see. These tests are written around the boundaries that
replaced it, because a permission change is the kind that fails open quietly.
"""

import pytest
from sqlalchemy import select

from app.models import Membership, Role, Site
from app.services import accounts
from tests.conftest import OWNER_PASSWORD, SITE_DOMAIN

OTHER = "colleague@example.com"


@pytest.fixture
def colleague(db_session):
    return accounts.register(db_session, email=OTHER, password=OWNER_PASSWORD)


def test_registering_a_site_makes_its_creator_a_member(db_session, account):
    """The owner is a row like everyone else, so one query answers every check."""
    site = accounts.add_site(db_session, owner=account, domain="fresh.example")

    assert accounts.role_for(db_session, user=account, site=site) is Role.OWNER
    assert [role for _, role in accounts.members_of(db_session, site)] == [Role.OWNER]


def test_a_viewer_can_read_but_not_administer(db_session, site, account, colleague):
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    assert accounts.readable_site(db_session, viewer=colleague, domain=SITE_DOMAIN) is not None
    assert accounts.administered_site(db_session, user=colleague, domain=SITE_DOMAIN) is None
    assert accounts.owned_site(db_session, owner=colleague, domain=SITE_DOMAIN) is None


def test_an_admin_can_administer_but_not_decide_who_else_gets_in(
    db_session, site, colleague
):
    """The line between doing the work and handing out the keys."""
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.ADMIN)

    assert accounts.administered_site(db_session, user=colleague, domain=SITE_DOMAIN) is not None
    assert accounts.owned_site(db_session, owner=colleague, domain=SITE_DOMAIN) is None


def test_a_stranger_gets_nothing(db_session, site, colleague):
    """And a 404 rather than a 403, so the domain list cannot be enumerated."""
    assert accounts.readable_site(db_session, viewer=colleague, domain=SITE_DOMAIN) is None
    assert accounts.administered_site(db_session, user=colleague, domain=SITE_DOMAIN) is None


def test_a_published_site_is_still_readable_by_anyone(db_session, site, colleague):
    """Membership is another way in, not a replacement for publishing."""
    accounts.set_visibility(db_session, site=site, public=True)

    assert accounts.readable_site(db_session, viewer=colleague, domain=SITE_DOMAIN) is not None
    assert accounts.readable_site(db_session, viewer=None, domain=SITE_DOMAIN) is not None


def test_a_site_lists_for_everyone_who_can_see_it(db_session, site, account, colleague):
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    assert [s.domain for s in accounts.sites_for(db_session, account)] == [SITE_DOMAIN]
    assert [s.domain for s in accounts.sites_for(db_session, colleague)] == [SITE_DOMAIN]


def test_an_address_without_an_account_is_refused_plainly(db_session, site):
    """There is no invite mail yet, so this says so instead of doing nothing."""
    with pytest.raises(accounts.MembershipError, match="need to sign up"):
        accounts.add_member(db_session, site=site, email="nobody@example.com", role=Role.VIEWER)


def test_the_same_person_cannot_be_added_twice(db_session, site, colleague):
    """Two grants would make "their role" a question with two answers."""
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    with pytest.raises(accounts.MembershipError, match="already has access"):
        accounts.add_member(db_session, site=site, email=OTHER, role=Role.ADMIN)


def test_a_second_owner_cannot_be_added(db_session, site, colleague):
    with pytest.raises(accounts.MembershipError, match="one owner"):
        accounts.add_member(db_session, site=site, email=OTHER, role=Role.OWNER)


def test_the_owner_cannot_be_removed_or_demoted(db_session, site, account):
    """A site with no owner is one nobody can publish, rename or delete.

    Nothing else in the system would notice it had happened, which is what
    makes this worth refusing rather than trusting the interface not to offer.
    """
    with pytest.raises(accounts.MembershipError, match="cannot be removed"):
        accounts.remove_member(db_session, site=site, user_id=account.id)

    with pytest.raises(accounts.MembershipError, match="cannot be changed"):
        accounts.set_member_role(db_session, site=site, user_id=account.id, role=Role.VIEWER)

    assert accounts.role_for(db_session, user=account, site=site) is Role.OWNER


def test_a_role_can_be_changed_and_access_taken_away(db_session, site, colleague):
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    accounts.set_member_role(db_session, site=site, user_id=colleague.id, role=Role.ADMIN)
    assert accounts.role_for(db_session, user=colleague, site=site) is Role.ADMIN

    accounts.remove_member(db_session, site=site, user_id=colleague.id)
    assert accounts.role_for(db_session, user=colleague, site=site) is None
    assert accounts.readable_site(db_session, viewer=colleague, domain=SITE_DOMAIN) is None


def test_operating_on_someone_who_has_no_access_is_refused(db_session, site, colleague):
    with pytest.raises(accounts.MembershipError, match="does not have access"):
        accounts.set_member_role(db_session, site=site, user_id=colleague.id, role=Role.ADMIN)
    with pytest.raises(accounts.MembershipError, match="does not have access"):
        accounts.remove_member(db_session, site=site, user_id=colleague.id)


def test_deleting_a_site_takes_its_memberships_with_it(db_session, site, colleague):
    """The cascade is real only because PRAGMA foreign_keys is on."""
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    db_session.delete(db_session.get(Site, site.id))
    db_session.commit()

    assert db_session.scalars(select(Membership)).all() == []


@pytest.fixture
def signed_in_colleague(client, colleague):
    response = client.post(
        "/login", data={"email": OTHER, "password": OWNER_PASSWORD}, follow_redirects=False
    )
    assert response.status_code == 303
    return client


def test_a_viewer_reaches_the_dashboard_but_not_the_people_page(
    db_session, site, signed_in_colleague
):
    """The whole point of the feature, and the line it must not cross."""
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    assert signed_in_colleague.get(f"/sites/{SITE_DOMAIN}").status_code == 200
    assert signed_in_colleague.get(f"/sites/{SITE_DOMAIN}/people").status_code == 404


def test_a_viewer_cannot_publish_a_dashboard(db_session, site, signed_in_colleague):
    """Reading the numbers is not permission to show them to the internet."""
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    response = signed_in_colleague.post(
        f"/sites/{SITE_DOMAIN}/visibility", data={"public": "true"}, follow_redirects=False
    )

    assert response.status_code == 404
    assert db_session.get(Site, site.id).public is False


def test_an_admin_can_publish(db_session, site, signed_in_colleague):
    """The guard has to refuse a viewer without refusing everybody."""
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.ADMIN)

    response = signed_in_colleague.post(
        f"/sites/{SITE_DOMAIN}/visibility", data={"public": "true"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert db_session.get(Site, site.id).public is True


def test_an_admin_cannot_hand_out_access(db_session, site, signed_in_colleague):
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.ADMIN)

    added = signed_in_colleague.post(
        f"/sites/{SITE_DOMAIN}/people",
        data={"email": "someone@example.com", "role": "viewer"},
        follow_redirects=False,
    )

    assert added.status_code == 404


def test_a_stranger_sees_the_same_404_as_a_missing_site(client, site, colleague):
    """A 403 would confirm the domain is tracked here."""
    client.post("/login", data={"email": OTHER, "password": OWNER_PASSWORD})

    real = client.get(f"/sites/{SITE_DOMAIN}/people")
    invented = client.get("/sites/not-a-site.example/people")

    assert real.status_code == invented.status_code == 404


def test_the_owner_can_add_and_remove_through_the_page(signed_in, db_session, site, colleague):
    added = signed_in.post(
        f"/sites/{SITE_DOMAIN}/people", data={"email": OTHER, "role": "viewer"},
        follow_redirects=False,
    )
    assert added.status_code == 303
    assert accounts.role_for(db_session, user=colleague, site=site) is Role.VIEWER

    listed = signed_in.get(f"/sites/{SITE_DOMAIN}/people")
    assert OTHER in listed.text

    removed = signed_in.post(
        f"/sites/{SITE_DOMAIN}/people/{colleague.id}/remove", follow_redirects=False
    )
    assert removed.status_code == 303
    assert accounts.role_for(db_session, user=colleague, site=site) is None


def test_a_bad_address_is_reported_on_the_page(signed_in, site):
    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/people",
        data={"email": "nobody@example.com", "role": "viewer"},
    )

    assert response.status_code == 400
    assert "need to sign up first" in response.text


def test_an_invented_role_is_refused(signed_in, site, colleague):
    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/people", data={"email": OTHER, "role": "superuser"}
    )

    assert response.status_code == 400


def test_a_blank_address_is_refused(db_session, site):
    with pytest.raises(accounts.MembershipError, match="Enter an email"):
        accounts.add_member(db_session, site=site, email="   ", role=Role.VIEWER)


def test_nobody_can_be_promoted_to_owner(db_session, site, colleague):
    """Ownership moves by transfer, not by editing a dropdown."""
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    with pytest.raises(accounts.MembershipError, match="one owner"):
        accounts.set_member_role(db_session, site=site, user_id=colleague.id, role=Role.OWNER)

    assert accounts.role_for(db_session, user=colleague, site=site) is Role.VIEWER


def test_the_owner_can_change_a_role_through_the_page(signed_in, db_session, site, colleague):
    accounts.add_member(db_session, site=site, email=OTHER, role=Role.VIEWER)

    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/people/{colleague.id}/role",
        data={"role": "admin"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert accounts.role_for(db_session, user=colleague, site=site) is Role.ADMIN


def test_a_refused_role_change_is_reported_on_the_page(signed_in, site, colleague):
    """Changing the role of somebody who has no access says so."""
    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/people/{colleague.id}/role", data={"role": "admin"}
    )

    assert response.status_code == 400
    assert "does not have access" in response.text


def test_a_refused_removal_is_reported_on_the_page(signed_in, db_session, site, account):
    """The owner removing themselves is the one worth showing plainly."""
    response = signed_in.post(f"/sites/{SITE_DOMAIN}/people/{account.id}/remove")

    assert response.status_code == 400
    assert "owner cannot be removed" in response.text
    assert accounts.role_for(db_session, user=account, site=site) is Role.OWNER
