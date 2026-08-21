"""Campaign tags.

The one part of a query string this project reads, so the tests are mostly
about what it still refuses to read.
"""

import pytest

from app.services import campaigns
from app.services.campaigns import MAX_LENGTH, Campaign


def test_a_plain_url_carries_no_campaign():
    assert campaigns.from_url("https://shop.example/pricing") == Campaign()


def test_the_three_tags_are_read():
    tags = campaigns.from_url(
        "https://shop.example/?utm_source=newsletter&utm_medium=email"
        "&utm_campaign=spring-sale"
    )

    assert tags == Campaign(
        source="newsletter", medium="email", campaign="spring-sale"
    )


def test_a_partial_set_is_fine():
    tags = campaigns.from_url("https://shop.example/?utm_campaign=launch")

    assert tags.campaign == "launch"
    assert tags.source is None
    assert tags.medium is None


def test_nothing_else_in_the_query_is_read():
    """The rest of the query is still discarded unread, as it always was."""
    tags = campaigns.from_url(
        "https://shop.example/?utm_source=hn&email=someone@example.com&token=s3cr3t"
    )

    assert tags.source == "hn"
    assert "someone@example.com" not in str(tags)
    assert "s3cr3t" not in str(tags)


def test_an_empty_tag_counts_as_absent():
    assert campaigns.from_url("https://shop.example/?utm_source=").source is None
    assert campaigns.from_url("https://shop.example/?utm_source=%20").source is None


def test_a_repeated_tag_takes_the_first():
    tags = campaigns.from_url("https://shop.example/?utm_source=a&utm_source=b")

    assert tags.source == "a"


def test_an_enormous_tag_is_capped():
    """A query parameter is attacker-controlled; this one has a column to fit."""
    tags = campaigns.from_url("https://shop.example/?utm_campaign=" + "x" * 5000)

    assert len(tags.campaign) == MAX_LENGTH


@pytest.mark.parametrize(
    "url",
    ["https://shop.example/", "https://shop.example/?", "not a url at all"],
)
def test_unusual_urls_do_not_raise(url):
    assert campaigns.from_url(url) == Campaign()
