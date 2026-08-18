from types import SimpleNamespace

from app.config import Settings
from app.services.client import UNKNOWN_ADDRESS, client_ip


def _request(headers=None, host="198.51.100.4"):
    return SimpleNamespace(
        headers=headers or {},
        client=SimpleNamespace(host=host) if host else None,
    )


def _trusting_proxies(monkeypatch, trusted: bool):
    monkeypatch.setattr(
        "app.services.client.get_settings",
        lambda: Settings(trust_proxy_headers=trusted),
    )


def test_uses_the_socket_address_by_default(monkeypatch):
    _trusting_proxies(monkeypatch, False)

    assert client_ip(_request()) == "198.51.100.4"


def test_forwarded_header_is_ignored_when_proxies_are_not_trusted(monkeypatch):
    """Otherwise any visitor could forge a new identity on every request."""
    _trusting_proxies(monkeypatch, False)
    request = _request({"x-forwarded-for": "203.0.113.9"})

    assert client_ip(request) == "198.51.100.4"


def test_forwarded_header_is_used_when_proxies_are_trusted(monkeypatch):
    _trusting_proxies(monkeypatch, True)
    request = _request({"x-forwarded-for": "203.0.113.9, 10.0.0.1"})

    assert client_ip(request) == "203.0.113.9"


def test_falls_back_when_there_is_no_client(monkeypatch):
    _trusting_proxies(monkeypatch, False)

    assert client_ip(_request(host=None)) == UNKNOWN_ADDRESS
