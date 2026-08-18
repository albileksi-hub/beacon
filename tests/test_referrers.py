import pytest

from app.services.referrers import DIRECT, classify

SITE_URL = "https://blue-mug.example/products"


@pytest.mark.parametrize("referrer", [None, "", "not-a-url"])
def test_absent_or_unusable_referrers_are_direct(referrer):
    assert classify(referrer, SITE_URL) == (None, DIRECT)


def test_navigation_within_the_site_is_not_a_source():
    host, source = classify("https://blue-mug.example/", SITE_URL)

    assert (host, source) == (None, DIRECT)


@pytest.mark.parametrize(
    ("referrer", "expected"),
    [
        ("https://www.google.com/search?q=blue+mugs", "Google"),
        ("https://google.de/", "Google"),
        ("https://www.google.co.uk/", "Google"),
        ("https://duckduckgo.com/", "DuckDuckGo"),
        ("https://t.co/abc123", "X (Twitter)"),
        ("https://old.reddit.com/r/coffee", "Reddit"),
        ("https://m.facebook.com/", "Facebook"),
        ("https://news.ycombinator.com/item?id=1", "Hacker News"),
    ],
)
def test_recognises_known_sources(referrer, expected):
    _, source = classify(referrer, SITE_URL)

    assert source == expected


def test_unknown_referrers_are_reported_under_their_host():
    host, source = classify("https://someblog.example/post/1", SITE_URL)

    assert (host, source) == ("someblog.example", "someblog.example")


def test_referrer_path_and_query_are_discarded():
    """The referring URL can carry personal data just like our own URLs."""
    host, _ = classify("https://mail.example/inbox?user=someone@example.com", SITE_URL)

    assert host == "mail.example"
