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
