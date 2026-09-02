"""Request limits and response hardening.

Both are ASGI middleware rather than BaseHTTPMiddleware, which buffers whole
responses -- the opposite of what a size limit is for, and fatal to the
streaming export.
"""

from collections.abc import Awaitable, Callable

from app.observability import Message, Receive, Scope, Send

# Generous for an analytics event, and small enough that a flood of oversized
# bodies cannot push the process over on memory.
DEFAULT_MAX_REQUEST_BYTES = 64 * 1024

CONTENT_LENGTH = b"content-length"

SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    # Stop a browser second-guessing the type of a CSV or JSON response.
    (b"x-content-type-options", b"nosniff"),
    # Nothing here should ever be framed. Both headers, because the older one
    # is still what some browsers and scanners look for.
    (b"x-frame-options", b"DENY"),
    # A dashboard URL contains the customer's domain. Without this, following a
    # link off the page hands that domain to whoever is on the other end --
    # which on this project of all projects would be embarrassing.
    (b"referrer-policy", b"same-origin"),
    (
        b"content-security-policy",
        b"default-src 'self'; "
        b"frame-ancestors 'none'; "
        b"base-uri 'self'; "
        b"form-action 'self'; "
        b"object-src 'none'; "
        b"img-src 'self' data:; "
        # Scripts are all same-origin files: the theme bootstrap was moved out
        # of the page precisely so this could stay strict.
        b"script-src 'self'; "
        # Styles cannot be, because the breakdown bars carry their width as an
        # inline style. That is a percentage of a number the server computed,
        # never anything a visitor supplied.
        b"style-src 'self' 'unsafe-inline'",
    ),
)

# Sent only where TLS is actually in front. On a plain-HTTP instance it is at
# best ignored and at worst a foot-gun: a browser that sees it once will refuse
# to reach that host over HTTP again for the whole max-age, which on localhost
# means breaking every other project on that port. The flag that says the
# cookie may carry Secure is the same signal, so it decides this too.
STRICT_TRANSPORT = (b"strict-transport-security", b"max-age=31536000; includeSubDomains")


class SecurityHeaders:
    """Adds the headers every response should carry."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        https: bool = False,
    ) -> None:
        self.app = app
        self.headers = SECURITY_HEADERS + ((STRICT_TRANSPORT,) if https else ())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_hardened(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(
                    (name, value) for name, value in self.headers if name not in present
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_hardened)


class LimitRequestSize:
    """Refuse a body larger than the limit.

    Without it the whole body is read and parsed before validation rejects it,
    so a 5MB request costs 5MB of memory per concurrent connection -- an
    inexpensive way to take the process down from the outside.
    """

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        max_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            # Answered without reading a byte of it.
            await _payload_too_large(send)
            return

        await self.app(scope, self._counted(receive), send)

    def _counted(self, receive: Receive) -> Receive:
        """Guards a body that arrives without declaring its length."""
        seen = 0

        async def counted() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    # Hanging up is the only way to stop a chunked upload that
                    # is already in flight.
                    return {"type": "http.disconnect"}
            return message

        return counted


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name.lower() == CONTENT_LENGTH:
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _payload_too_large(send: Send) -> None:
    body = b'{"detail":"Request body too large"}'
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
