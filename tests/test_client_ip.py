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


def test_a_trusted_proxy_that_sends_no_header_falls_back_to_the_peer(monkeypatch):
    """Trusting the header is not the same as receiving one.

    A request can reach a proxy-fronted instance without X-Forwarded-For --
    a health check on the internal address, a misconfigured proxy, or anything
    that bypasses it. Reading the peer is right there; the danger would be
    returning nothing and collapsing every such caller into one identity.
    """
    from app.config import Settings
    from app.services import client as client_module

    monkeypatch.setattr(
        client_module, "get_settings", lambda: Settings(trust_proxy_headers=True)
    )
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.9"))

    assert client_ip(request) == "203.0.113.9"
