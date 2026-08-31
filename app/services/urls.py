from urllib.parse import urlparse


def pathname_of(url: str) -> str:
    """Reduce a full URL to just its path.

    Query strings routinely carry personal data (?email=, ?token=, session IDs),
    so they are discarded at the boundary rather than stored and filtered later.
    """
    return urlparse(url).path or "/"


def host_of(url: str) -> str | None:
    """The bare hostname a URL points at, or None if it names no host.

    Normalised the same way a registered domain is: lowercased, `www.` removed,
    the trailing dot of a fully-qualified name removed. `urlparse` drops the
    port for us, so `example.com:3000` and `example.com` agree.
    """
    host = urlparse(url).hostname
    if not host:
        return None
    return host.lower().rstrip(".").removeprefix("www.") or None


def belongs_to(url: str, domain: str) -> bool:
    """Whether `url` is a page on `domain`.

    Subdomains count. Someone who registers `example.com` and tracks
    `blog.example.com` is tracking their own site, and anyone able to serve a
    page from a subdomain is already inside the domain -- so there is nothing
    left to protect there.

    The leading dot in the suffix test is load-bearing: without it
    `notexample.com` ends with `example.com` and passes.
    """
    host = host_of(url)
    if host is None:
        # A relative path names no site, so it cannot be shown to belong to
        # this one. The tracking script always sends `location.href`.
        return False
    return host == domain or host.endswith(f".{domain}")
