"""Request limits and response hardening."""

import pytest
from sqlalchemy import select

from app.middleware import _declared_length
from app.models import Event

BIG = "x" * (128 * 1024)


def test_an_oversized_body_is_refused(client, site):
    response = client.post(
        "/api/event",
        json={"site_id": "blue-mug.example", "url": "https://blue-mug.example/", "referrer": BIG},
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body too large"}


def test_an_ordinary_event_is_unaffected(client, site):
    response = client.post(
        "/api/event",
        json={
            "site_id": "blue-mug.example",
            "url": "https://blue-mug.example/",
            "referrer": None,
            "screen_width": 1280,
        },
    )

    assert response.status_code == 202


def test_a_body_that_hides_its_size_is_still_capped(client, site, db_session):
    """Chunked uploads declare no length, so they are counted as they arrive.

    Hanging up on one surfaces as a 400 rather than a 413: by then the request
    is already in flight and there is no status left to negotiate with.
    """

    def chunks():
        for _ in range(4):
            yield BIG.encode()

    response = client.post(
        "/api/event", content=chunks(), headers={"content-type": "application/json"}
    )

    assert response.status_code == 400
    assert db_session.scalars(select(Event)).all() == []


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "same-origin"),
    ],
)
def test_responses_carry_the_security_headers(client, header, expected):
    assert client.get("/").headers[header] == expected


def test_the_policy_forbids_framing_and_foreign_scripts(client):
    policy = client.get("/").headers["content-security-policy"]

    assert "frame-ancestors 'none'" in policy
    assert "script-src 'self'" in policy
    assert "form-action 'self'" in policy


def test_the_policy_still_allows_the_inline_bar_widths(client):
    """The breakdown bars carry a server-computed width as an inline style."""
    policy = client.get("/").headers["content-security-policy"]

    assert "style-src 'self' 'unsafe-inline'" in policy


def test_pages_carry_no_inline_script_for_the_policy_to_block(client):
    """The theme bootstrap was moved to a file so script-src could stay strict."""
    body = client.get("/").text

    assert "<script>" not in body
    assert "theme-init.js" in body


def test_the_headers_reach_error_responses_too(signed_in, site):
    response = signed_in.get("/sites/nobody-owns-this.example", headers={"accept": "text/html"})

    assert response.status_code == 404
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"content-length", b"512")], 512),
        # A header a client made up: treated as unknown, so the body gets
        # counted as it arrives instead of trusted or crashed on.
        ([(b"content-length", b"not-a-number")], None),
        ([], None),
    ],
)
def test_reading_the_declared_body_size(headers, expected):
    assert _declared_length({"headers": headers}) == expected
