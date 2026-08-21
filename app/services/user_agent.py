"""Reduce a User-Agent header to coarse, non-identifying dimensions.

The header itself is never stored: it is high-entropy enough to contribute to
browser fingerprinting, so only the broad browser/OS/device buckets survive.

Crawlers are recognised against a vendored dataset rather than a list written
from memory. The hand-written one this replaced identified 65.5% of real
crawler user-agent strings; the third it missed included ChatGPT-User,
Applebot and Meta's fetchers, all of which would otherwise be counted as
people. See refresh_bots.py.
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from user_agents import parse as _parse

from app.services.bot_patterns import PATTERNS


@dataclass(frozen=True, slots=True)
class ClientProfile:
    browser: str
    os: str
    device: str
    is_bot: bool


# Most of the dataset is plain literals, and a substring scan over those is
# roughly twice as quick as making the regex engine carry them: 1,500
# alternatives in one pattern measured 625us against a non-matching browser
# string, versus 296us split this way. Both halves are still needed -- the
# remainder carries anchors and character classes.
_META = set(r".^$*+?{}[]\|()")
_LITERALS = tuple(p.lower() for p in PATTERNS if not set(p) & _META)
_EXPRESSIONS = re.compile("|".join(p for p in PATTERNS if set(p) & _META))

# Scripted clients that announce themselves plainly and are not in the dataset,
# which catalogues crawlers rather than every HTTP library.
#
# Deliberately no bare "bot" entry: real device names contain it (Cubot phones,
# for one), and everything honest is covered above.
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


def _device_of(parsed: Any) -> str:
    if parsed.is_tablet:
        return "tablet"
    if parsed.is_mobile:
        return "mobile"
    if parsed.is_pc:
        return "desktop"
    return "unknown"


def _looks_like_a_crawler(user_agent: str, lowered: str) -> bool:
    return (
        any(literal in lowered for literal in _LITERALS)
        or _EXPRESSIONS.search(user_agent) is not None
    )


# A few thousand distinct strings account for almost all real traffic, so the
# same handful of answers is wanted over and over. Bounded, because the header
# is supplied by the caller and an unbounded cache would be a way to grow the
# process without limit.
@lru_cache(maxsize=8192)
def profile(user_agent: str | None) -> ClientProfile:
    if not user_agent:
        # A request with no User-Agent at all is essentially never a real browser.
        return ClientProfile(browser="Unknown", os="Unknown", device="unknown", is_bot=True)

    parsed = _parse(user_agent)
    lowered = user_agent.lower()
    is_bot = (
        _looks_like_a_crawler(user_agent, lowered)
        or parsed.is_bot
        or any(marker in lowered for marker in _SCRIPTED_CLIENT_MARKERS)
    )

    return ClientProfile(
        browser=parsed.browser.family or "Unknown",
        os=parsed.os.family or "Unknown",
        device="bot" if is_bot else _device_of(parsed),
        is_bot=is_bot,
    )
