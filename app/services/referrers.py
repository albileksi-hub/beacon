"""Classify where a visit came from, without retaining the referring URL.

Only the referring host is kept. A full referrer can carry query strings with
personal data in them, exactly like the page URLs already stripped on ingest.
"""

from urllib.parse import urlparse

# Matched against the host and any of its subdomains, so m.facebook.com and
# old.reddit.com resolve correctly.
_SITES = {
    "t.co": "X (Twitter)",
    "twitter.com": "X (Twitter)",
    "x.com": "X (Twitter)",
    "facebook.com": "Facebook",
    "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn",
    "reddit.com": "Reddit",
    "news.ycombinator.com": "Hacker News",
    "youtube.com": "YouTube",
    "github.com": "GitHub",
    "producthunt.com": "Product Hunt",
    "medium.com": "Medium",
    "substack.com": "Substack",
}

# Search engines run dozens of country domains (google.de, google.co.uk), so
# these match on the first label of the host instead.
_SEARCH_ENGINES = {
    "google": "Google",
    "bing": "Bing",
    "duckduckgo": "DuckDuckGo",
    "yahoo": "Yahoo",
    "yandex": "Yandex",
    "ecosia": "Ecosia",
    "baidu": "Baidu",
    "startpage": "Startpage",
}

DIRECT = "Direct"


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _label_for(host: str) -> str:
    for known, label in _SITES.items():
        if host == known or host.endswith(f".{known}"):
            return label

    engine = _SEARCH_ENGINES.get(host.split(".")[0])
    if engine:
        return engine

    # Anything unrecognised is still useful reported under its own hostname.
    return host


def classify(referrer: str | None, current_url: str) -> tuple[str | None, str]:
    """Return ``(referrer_host, source_label)`` for a visit."""
    if not referrer:
        return None, DIRECT

    host = host_of(referrer)
    if not host:
        return None, DIRECT

    # Navigating within the site is not an acquisition source.
    if host == host_of(current_url):
        return None, DIRECT

    return host, _label_for(host)
