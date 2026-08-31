"""How long the browser may keep a file, and how big it is on the wire.

Starlette serves static files with an etag and no Cache-Control, so a browser
revalidates every asset on every page load. The templates already request them
by a URL carrying a hash of the contents, which is the condition under which a
file can be kept forever -- the work of busting the cache was being done
without taking the reward.
"""

import gzip
from pathlib import Path

from app.main import CachedStatic

STATIC = Path(__file__).resolve().parent.parent / "static"


def test_a_hashed_asset_can_be_kept_forever(client):
    response = client.get("/static/dashboard.css?v=abc1234567")

    assert response.status_code == 200
    assert response.headers["cache-control"] == CachedStatic.IMMUTABLE
    assert "immutable" in response.headers["cache-control"]


def test_the_tracking_script_is_not_kept_forever(client):
    """It is served unhashed on purpose, so a new version has to be able to land.

    Customers paste this URL into their own pages, so it cannot carry a hash
    that changes -- which means it also cannot be cached for a year, or a fix
    would never reach the sites running it.
    """
    response = client.get("/static/beacon.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == CachedStatic.BRIEF
    assert "immutable" not in response.headers["cache-control"]


def test_the_stylesheet_is_compressed(client):
    """36 KB of mostly repeated identifiers, going over the wire in full."""
    response = client.get(
        "/static/dashboard.css?v=abc1234567", headers={"accept-encoding": "gzip"}
    )

    assert response.headers.get("content-encoding") == "gzip"


def test_a_collector_reply_is_left_alone(client, site):
    """Compressing twenty bytes makes them longer, and it is the hot path."""
    response = client.post(
        "/api/event",
        json={
            "site_id": "blue-mug.example",
            "url": "https://blue-mug.example/",
            "screen_width": 800,
        },
        headers={"accept-encoding": "gzip"},
    )

    assert response.status_code == 202
    assert "content-encoding" not in response.headers


def test_the_script_a_customer_ships_stays_small():
    """It runs on every page of somebody else's site, so its size is their cost.

    Not a limit anybody is near -- it is a tripwire for a change that adds a
    library or a polyfill without noticing what that costs the sites running
    it.
    """
    packed = len(gzip.compress((STATIC / "beacon.js").read_bytes(), 9))

    assert packed < 4096, f"the tracker gzips to {packed} bytes"
