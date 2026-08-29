"""What an event was worth, and getting the arithmetic right.

Money is the one thing here that cannot be approximately correct, so it is
integers end to end: minor units in the column, a Decimal at both boundaries,
and no float anywhere in between.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import DailyStat, Event
from app.schemas import EventIn
from app.services import reports, rollups, stats
from app.services.stats import BreakdownProperty
from app.services.timeranges import Period, resolve
from app.templating import money
from tests.conftest import SITE_DOMAIN


def _payload(**overrides):
    return {
        "site_id": SITE_DOMAIN,
        "url": f"https://{SITE_DOMAIN}/checkout",
        "name": "purchase",
        "screen_width": 1280,
    } | overrides


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("49.90", 4990),
        ("10.29", 1029),
        ("0", 0),
        ("0.01", 1),
        # Half-up, like a till. Python rounds half to even by default, which
        # turns 0.005 into 0.00 and is wrong for money.
        ("0.005", 1),
        ("0.015", 2),
        ("1234567.89", 123456789),
    ],
)
def test_an_amount_survives_the_trip_exactly(sent, stored):
    payload = EventIn(site_id=SITE_DOMAIN, url="https://x.example/", revenue=Decimal(sent))

    assert payload.revenue_minor == stored


def test_the_float_that_is_never_used():
    """Why the amount travels as a string and is parsed as a Decimal.

    A JSON number is a double, and a double cannot hold most prices. 0.29
    becomes 28.999999999999996, so truncating it loses a penny. Of the first
    19,999 amounts -- every price up to 199.99 -- 1,145 of them, 5.7%, come out
    a penny short this way.

    The test is here so the reason survives somebody deciding a float would be
    simpler, and it names an amount rather than asserting a vague principle.
    """
    assert int(float("0.29") * 100) == 28, "a float loses the penny"
    assert EventIn(
        site_id=SITE_DOMAIN, url="https://x.example/", revenue=Decimal("0.29")
    ).revenue_minor == 29

    wrong = sum(
        int(float(f"{c // 100}.{c % 100:02d}") * 100) != c for c in range(1, 20_000)
    )
    assert wrong == 1145


def test_an_event_without_a_price_stores_nothing(client, site, db_session):
    client.post("/api/event", json=_payload(name="pageview"))

    assert db_session.scalar(select(Event.revenue_minor)) is None


def test_a_purchase_is_stored_in_minor_units(client, site, db_session):
    response = client.post("/api/event", json=_payload(revenue="49.90"))

    assert response.status_code == 202
    assert db_session.scalar(select(Event.revenue_minor)) == 4990


def test_a_negative_price_is_refused(client, site, db_session):
    response = client.post("/api/event", json=_payload(revenue="-5.00"))

    assert response.status_code == 422
    assert db_session.scalars(select(Event)).all() == []


def _purchase(db, *, visitor, amount, source="Google", path="/checkout"):
    from tests.test_rollups import add_event

    add_event(db, visitor_id=visitor, name="purchase", pathname=path, source=source,
              revenue_minor=amount)


def test_revenue_reaches_the_summary_and_every_dimension(db_session, site):
    from tests.test_rollups import DAY, add_event

    add_event(db_session, visitor_id="a", pathname="/checkout")
    _purchase(db_session, visitor="a", amount=4990, source="Google")
    _purchase(db_session, visitor="b", amount=1000, source="Direct")
    rollups.rebuild_day(db_session, site_id="blue-mug.example", day=DAY)

    period = resolve(Period.LAST_30_DAYS)
    summary = reports.summary(db_session, site_id="blue-mug.example", time_range=period)
    by_source = reports.breakdown(
        db_session, site_id="blue-mug.example", time_range=period,
        prop=BreakdownProperty.SOURCE,
    )

    assert summary.revenue_minor == 5990
    assert {row.value: row.revenue_minor for row in by_source} == {"Google": 4990, "Direct": 1000}


def test_the_rollups_agree_with_the_raw_events_about_money(db_session, site):
    """The same guarantee the rest of the aggregates carry.

    reports reads pre-aggregated rows and stats reads raw events; if they ever
    disagree the optimisation has started lying, and about money that is worse
    than about pageviews.
    """
    from tests.test_rollups import DAY, add_event

    add_event(db_session, visitor_id="a", pathname="/checkout")
    _purchase(db_session, visitor="a", amount=4990)
    _purchase(db_session, visitor="b", amount=333)
    rollups.rebuild_day(db_session, site_id="blue-mug.example", day=DAY)

    period = resolve(Period.LAST_30_DAYS)
    from_rollups = reports.summary(db_session, site_id="blue-mug.example", time_range=period)
    from_raw = stats.summary(db_session, site_id="blue-mug.example", time_range=period)

    assert from_rollups.revenue_minor == from_raw.revenue_minor == 5323
    assert from_rollups == from_raw


def test_days_of_revenue_add_up(db_session, site):
    """Summable across days for the same reason visitors are."""
    import datetime as dt

    from tests.test_rollups import DAY, NOON, add_event

    for offset, amount in ((0, 1000), (1, 2000), (2, 500)):
        moment = NOON - dt.timedelta(days=offset)
        add_event(db_session, visitor_id=f"v{offset}", timestamp=moment)
        add_event(db_session, visitor_id=f"v{offset}", timestamp=moment, name="purchase",
                  revenue_minor=amount)
    rollups.refresh(db_session, days_back=5, today=DAY)

    total = reports.summary(
        db_session, site_id="blue-mug.example",
        time_range=resolve(Period.LAST_30_DAYS, now=NOON),
    )

    assert total.revenue_minor == 3500
    stored = db_session.scalars(
        select(DailyStat.revenue_minor).where(DailyStat.dimension == rollups.TOTAL)
    ).all()
    assert sorted(stored) == [500, 1000, 2000]


@pytest.mark.parametrize(
    ("minor", "shown"),
    [(4990, "49.90"), (0, "0.00"), (5, "0.05"), (123456789, "1,234,567.89")],
)
def test_minor_units_read_back_as_money(minor, shown):
    assert money(minor) == shown
    assert money(minor, "EUR") == f"{shown} EUR"
