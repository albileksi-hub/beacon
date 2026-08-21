"""Campaign tags, pulled out of the query string before it is discarded.

This is the one part of a URL's query that a site owner put there on purpose,
about their own marketing rather than about the person reading. Every other
analytics tool reports it, and until now this one threw it away with the rest
of the query.

Keeping it does not soften the rule the rest of the project follows. Three
named parameters are read and everything else in the query is still dropped
unread, which is a stricter position than storing the query and filtering it
afterwards -- the same argument as `urls.pathname_of`.

A campaign tag is also not personal: `utm_campaign=spring-sale` describes the
link, not whoever clicked it. The cap on length is there because a query
parameter is attacker-controlled, so a caller could otherwise post a megabyte
of it.
"""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# Exactly these, by name. Anything else in the query is never even read.
SOURCE = "utm_source"
MEDIUM = "utm_medium"
CAMPAIGN = "utm_campaign"

MAX_LENGTH = 128


@dataclass(frozen=True, slots=True)
class Campaign:
    source: str | None = None
    medium: str | None = None
    campaign: str | None = None


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None

    value = values[0].strip()[:MAX_LENGTH]
    return value or None


def from_url(url: str) -> Campaign:
    """Read the campaign tags out of a URL, if it carries any."""
    query = urlparse(url).query
    if not query:
        return Campaign()

    # keep_blank_values is off, so `?utm_source=` behaves as if absent.
    parsed = parse_qs(query)
    return Campaign(
        source=_first(parsed, SOURCE),
        medium=_first(parsed, MEDIUM),
        campaign=_first(parsed, CAMPAIGN),
    )
