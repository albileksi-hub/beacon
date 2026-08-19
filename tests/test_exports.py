"""The CSV export.

Bulk data leaving the service, so the tests are about who may take it and
whether it survives values that break hand-rolled CSV.
"""

import csv
import datetime as dt
import io

from app.models import Event
from app.services import accounts
from tests.conftest import OWNER_PASSWORD, SITE_DOMAIN


def add_event(db, **overrides):
    defaults = {
        "site_id": SITE_DOMAIN,
        "visitor_id": "visitor-1",
        "pathname": "/",
        "timestamp": dt.datetime.now(dt.UTC),
        "name": "pageview",
        "source": "Direct",
        "browser": "Chrome",
        "os": "Windows",
        "device": "desktop",
        "country": "DE",
        "screen": "Laptop",
    }
    db.add(Event(**(defaults | overrides)))
    db.commit()


def _rows(body: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body)))


def test_the_export_has_a_header_and_the_aggregates(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a", pathname="/pricing")
    add_event(db_session, visitor_id="b", pathname="/pricing")
    rebuild_rollups()

    response = signed_in.get(f"/sites/{SITE_DOMAIN}/export.csv")
    rows = _rows(response.text)

    assert response.status_code == 200
    assert rows[0] == ["day", "dimension", "value", "visitors", "pageviews"]
    assert ["total", "", "2", "2"] in [row[1:] for row in rows[1:]]
    assert ["page", "/pricing", "2", "2"] in [row[1:] for row in rows[1:]]


def test_it_is_offered_as_a_download_named_for_the_site(
    signed_in, db_session, rebuild_rollups, site
):
    add_event(db_session, visitor_id="a")
    rebuild_rollups()

    response = signed_in.get(f"/sites/{SITE_DOMAIN}/export.csv")

    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    assert SITE_DOMAIN in response.headers["content-disposition"]


def test_values_that_would_break_naive_csv_survive(
    signed_in, db_session, rebuild_rollups, site
):
    """A path can contain a comma and a quote; joining with commas corrupts it."""
    awkward = '/search?q=a,b"c'.replace("?", "-")
    add_event(db_session, visitor_id="a", pathname=awkward)
    rebuild_rollups()

    rows = _rows(signed_in.get(f"/sites/{SITE_DOMAIN}/export.csv").text)

    assert any(awkward in row for row in rows)


def test_the_period_selects_what_is_exported(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="recent")
    rebuild_rollups()

    everything = _rows(signed_in.get(f"/sites/{SITE_DOMAIN}/export.csv?period=12mo").text)
    today_only = _rows(signed_in.get(f"/sites/{SITE_DOMAIN}/export.csv?period=today").text)

    assert len(everything) >= len(today_only) > 1


def test_a_stranger_cannot_export_a_private_site(client, site):
    assert client.get(f"/sites/{SITE_DOMAIN}/export.csv").status_code == 404


def test_anyone_may_export_a_published_site(client, db_session, site, rebuild_rollups):
    """The API already serves these numbers; refusing the file would be theatre."""
    add_event(db_session, visitor_id="a")
    accounts.set_visibility(db_session, site=site, public=True)
    rebuild_rollups()

    response = client.get(f"/sites/{SITE_DOMAIN}/export.csv")

    assert response.status_code == 200
    assert len(_rows(response.text)) > 1


def test_one_account_cannot_export_anothers_site(signed_in, db_session):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="theirs.example")

    assert signed_in.get("/sites/theirs.example/export.csv").status_code == 404


def test_an_empty_site_exports_just_the_header(signed_in, site):
    rows = _rows(signed_in.get(f"/sites/{SITE_DOMAIN}/export.csv").text)

    assert rows == [["day", "dimension", "value", "visitors", "pageviews"]]
