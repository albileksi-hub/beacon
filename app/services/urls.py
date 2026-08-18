from urllib.parse import urlparse


def pathname_of(url: str) -> str:
    """Reduce a full URL to just its path.

    Query strings routinely carry personal data (?email=, ?token=, session IDs),
    so they are discarded at the boundary rather than stored and filtered later.
    """
    return urlparse(url).path or "/"
