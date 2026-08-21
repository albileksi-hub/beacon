import pytest

from app.services.user_agent import profile
from tests.conftest import CHROME_MAC, SAFARI_IPHONE


def test_parses_a_desktop_browser():
    result = profile(CHROME_MAC)

    assert result.browser == "Chrome"
    assert result.os == "Mac OS X"
    assert result.device == "desktop"
    assert result.is_bot is False


def test_parses_a_mobile_browser():
    result = profile(SAFARI_IPHONE)

    assert result.browser == "Mobile Safari"
    assert result.device == "mobile"
    assert result.is_bot is False


@pytest.mark.parametrize(
    "user_agent",
    [
        "Googlebot/2.1 (+http://www.google.com/bot.html)",
        "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
        "python-requests/2.31.0",
        "curl/8.4.0",
        "Go-http-client/1.1",
        "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/120.0.0.0",
    ],
)
def test_detects_automated_clients(user_agent):
    assert profile(user_agent).is_bot is True


def test_missing_user_agent_is_treated_as_automated():
    assert profile(None).is_bot is True
    assert profile("").is_bot is True


def test_device_names_containing_bot_are_not_flagged():
    """Cubot is a phone manufacturer, not a crawler."""
    cubot = (
        "Mozilla/5.0 (Linux; Android 12; Cubot Note 20) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/108.0.0.0 Mobile Safari/537.36"
    )

    assert profile(cubot).is_bot is False


def test_parses_a_tablet():
    ipad = (
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    )

    assert profile(ipad).device == "tablet"


def test_unrecognised_clients_fall_back_to_unknown():
    result = profile("MysteryDevice/1.0")

    assert result.device == "unknown"
    assert result.is_bot is False


@pytest.mark.parametrize(
    "user_agent",
    [
        # The crawlers that grew up after a hand-written list was written, and
        # that a hand-written list therefore counted as people.
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
        "ChatGPT-User/1.0; +https://openai.com/bot",
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.1; "
        "+https://openai.com/gptbot)",
        "Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
        "Mozilla/5.0 (compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)",
        "Mozilla/5.0 (compatible; Bytespider; spider-feedback@bytedance.com)",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, "
        "like Gecko) Version/17.0 Safari/605.1.15 Applebot/0.1",
        "meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)",
        "CCBot/2.0 (https://commoncrawl.org/faq/)",
    ],
)
def test_modern_ai_crawlers_are_recognised(user_agent):
    assert profile(user_agent).is_bot is True


@pytest.mark.parametrize(
    "user_agent",
    [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
        "Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, "
        "like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
        "Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ],
)
def test_a_dataset_of_1500_patterns_does_not_flag_real_browsers(user_agent):
    """The risk of a large pattern list is that it starts eating real traffic."""
    assert profile(user_agent).is_bot is False


def test_the_vendored_dataset_is_present_and_substantial():
    """A generated file that silently emptied would disable bot filtering."""
    from app.services.bot_patterns import PATTERNS

    assert len(PATTERNS) > 1000
    assert all(isinstance(pattern, str) and pattern for pattern in PATTERNS)


def test_repeated_lookups_are_answered_from_the_cache():
    """Matching 1,500 patterns is expensive; the same strings recur endlessly."""
    agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TestOnly/1.0 Chrome/120.0.0.0"
    profile.cache_clear()

    first = profile(agent)
    second = profile(agent)

    assert first is second
    assert profile.cache_info().hits == 1
