"""Determine the originating address of a request."""

from fastapi import Request

from app.config import get_settings

UNKNOWN_ADDRESS = "0.0.0.0"


def client_ip(request: Request) -> str:
    """Best-effort client address.

    X-Forwarded-For is honoured only when explicitly trusted. Any client can set
    that header themselves, so believing it while directly exposed would let a
    visitor forge a new identity per request -- and inflate a customer's numbers.
    """
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Leftmost entry is the original client; the rest are proxy hops.
            return forwarded.split(",")[0].strip()

    return request.client.host if request.client else UNKNOWN_ADDRESS
