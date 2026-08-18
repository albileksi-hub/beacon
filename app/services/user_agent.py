"""Reduce a User-Agent header to coarse, non-identifying dimensions.

The header itself is never stored: it is high-entropy enough to contribute to
browser fingerprinting, so only the broad browser/OS/device buckets survive.
"""

from dataclasses import dataclass

from user_agents import parse as _parse


@dataclass(frozen=True, slots=True)
class ClientProfile:
    browser: str
    os: str
    device: str
    is_bot: bool


# The user_agents library already recognises crawlers that identify themselves
# honestly. These cover the scripted traffic that does not.
#
# Deliberately no bare "bot" entry: real device names contain it (Cubot phones,
# for one), and the library's own regexes catch the honest crawlers anyway.
_SCRIPTED_CLIENT_MARKERS = (
    "python-requests",
    "python-urllib",
    "curl/",
    "wget/",
    "go-http-client",
    "axios/",
    "okhttp",
    "java/",
    "libwww-perl",
    "headlesschrome",
    "phantomjs",
    "crawler",
    "spider",
    "scraper",
    "uptime",
    "monitoring",
)


def _device_of(parsed) -> str:
    if parsed.is_tablet:
        return "tablet"
    if parsed.is_mobile:
        return "mobile"
    if parsed.is_pc:
        return "desktop"
    return "unknown"


def profile(user_agent: str | None) -> ClientProfile:
    if not user_agent:
        # A request with no User-Agent at all is essentially never a real browser.
        return ClientProfile(browser="Unknown", os="Unknown", device="unknown", is_bot=True)

    parsed = _parse(user_agent)
    lowered = user_agent.lower()
    is_bot = parsed.is_bot or any(marker in lowered for marker in _SCRIPTED_CLIENT_MARKERS)

    return ClientProfile(
        browser=parsed.browser.family or "Unknown",
        os=parsed.os.family or "Unknown",
        device="bot" if is_bot else _device_of(parsed),
        is_bot=is_bot,
    )
