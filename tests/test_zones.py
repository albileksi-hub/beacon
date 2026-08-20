"""The site's own clock."""

import datetime as dt

import pytest

from app.services import zones


def test_a_known_zone_is_kept():
    assert zones.validate("Europe/Berlin") == "Europe/Berlin"


def test_whitespace_is_trimmed():
    assert zones.validate("  Asia/Tokyo  ") == "Asia/Tokyo"


def test_nothing_at_all_means_utc():
    assert zones.validate("") == zones.DEFAULT
    assert zones.validate("   ") == zones.DEFAULT


@pytest.mark.parametrize("name", ["Mars/Olympus_Mons", "GMT+27", "'; DROP TABLE sites"])
def test_an_invented_zone_is_refused(name):
    with pytest.raises(zones.UnknownTimezone):
        zones.validate(name)


def test_the_offered_list_is_all_real_zones():
    """A picker offering a zone that fails validation would be absurd."""
    assert all(zones.validate(name) == name for name in zones.COMMON)


def test_an_instant_falls_on_different_days_in_different_places():
    """23:30 UTC is already tomorrow in Berlin and still yesterday in Denver."""
    moment = dt.datetime(2026, 8, 18, 23, 30, tzinfo=dt.UTC)

    assert zones.local_parts(moment, "UTC") == (dt.date(2026, 8, 18), 23)
    assert zones.local_parts(moment, "Europe/Berlin") == (dt.date(2026, 8, 19), 1)
    assert zones.local_parts(moment, "America/Denver") == (dt.date(2026, 8, 18), 17)
    assert zones.local_parts(moment, "Pacific/Auckland") == (dt.date(2026, 8, 19), 11)


def test_an_unknown_zone_falls_back_rather_than_failing():
    """A bad value in the database must not stop events being collected."""
    moment = dt.datetime(2026, 8, 18, 23, 30, tzinfo=dt.UTC)

    assert zones.local_parts(moment, "Nowhere/Special") == (dt.date(2026, 8, 18), 23)


def test_daylight_saving_is_the_zone_database_s_problem_not_ours():
    """Berlin is two hours ahead in August and one in January."""
    summer = dt.datetime(2026, 8, 18, 23, 30, tzinfo=dt.UTC)
    winter = dt.datetime(2026, 1, 18, 23, 30, tzinfo=dt.UTC)

    assert zones.local_parts(summer, "Europe/Berlin") == (dt.date(2026, 8, 19), 1)
    assert zones.local_parts(winter, "Europe/Berlin") == (dt.date(2026, 1, 19), 0)
