import os

import pytest

from app import templating
from app.templating import UNKNOWN_FLAG, asset_url, country_flag, tick_label


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("DE", "\U0001F1E9\U0001F1EA"),
        ("gb", "\U0001F1EC\U0001F1E7"),
        ("US", "\U0001F1FA\U0001F1F8"),
    ],
)
def test_country_codes_become_flags(code, expected):
    assert country_flag(code) == expected


@pytest.mark.parametrize("value", ["Unknown", "", None, "DEU", "1"])
def test_anything_else_falls_back_to_a_globe(value):
    """Every row keeps its leading glyph, so the column stays aligned."""
    assert country_flag(value) == UNKNOWN_FLAG


def test_asset_urls_carry_a_content_hash():
    """A browser holding the old stylesheet would render new markup against it."""
    url = asset_url("dashboard.css")

    assert url.startswith("/static/dashboard.css?v=")
    assert len(url.split("?v=")[1]) == 10


def test_the_hash_follows_the_contents(tmp_path, monkeypatch):
    """And without the test having to reach in and clear a cache first.

    It used to. The whole URL was cached against the filename, so within one
    process the answer never changed however much the file did -- which meant
    editing a stylesheet did nothing until a restart, and a browser holding the
    old one had no reason to ask again. A test that has to clear the cache to
    show the behaviour is describing something callers cannot rely on.
    """
    monkeypatch.setattr("app.templating.STATIC_DIR", tmp_path)
    asset = tmp_path / "thing.css"

    asset.write_text("a{}", encoding="utf-8")
    first = asset_url("thing.css")

    asset.write_text("b{}", encoding="utf-8")
    # Set explicitly rather than trusting the clock: some filesystems keep
    # mtimes to the second, and the two writes are microseconds apart.
    os.utime(asset, ns=(0, 1_000_000_000))
    second = asset_url("thing.css")

    assert first != second


def test_an_unchanged_file_is_not_rehashed(tmp_path, monkeypatch):
    """The stat decides; the hash is only recomputed when it has to be."""
    monkeypatch.setattr("app.templating.STATIC_DIR", tmp_path)
    (tmp_path / "thing.css").write_text("a{}", encoding="utf-8")

    before = templating._digest.cache_info()
    asset_url("thing.css")
    asset_url("thing.css")
    asset_url("thing.css")
    after = templating._digest.cache_info()

    assert after.hits - before.hits == 2, "repeated renders should reuse the hash"


def test_a_missing_asset_still_produces_a_usable_url():
    assert asset_url("not-here.css") == "/static/not-here.css"


@pytest.mark.parametrize(
    ("bucket", "interval", "expected"),
    [
        ("2026-08-21T14:00:00", "hour", "14:00"),
        ("2026-08-21T00:00:00", "hour", "00:00"),
        ("2026-08-21", "day", "21 Aug"),
        ("2026-08-01", "day", "1 Aug"),
        ("2026-07-01", "month", "Jul"),
        ("2026-12-01", "month", "Dec"),
    ],
)
def test_axis_ticks_are_shortened_to_their_grain(bucket, interval, expected):
    """Seven full ISO buckets collide at any width this chart is drawn at.

    The hover title on each point still carries the unshortened label, so
    nothing is lost by shortening the tick.
    """
    assert tick_label(bucket, interval) == expected


def test_the_day_tick_does_not_pad_the_day_number():
    """"%-d" is a GNU extension and this project is developed on Windows."""
    assert tick_label("2026-08-05", "day") == "5 Aug"
