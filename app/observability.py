"""Structured request logging.

An access log is the classic place a privacy-first service quietly undoes its
own promise. The default log line in most stacks carries the client address and
the full URL including its query string -- precisely the two things this project
strips out of everything it stores. Neither goes into the log either, and a test
asserts it.

Written as raw ASGI middleware rather than BaseHTTPMiddleware, which buffers
responses and would defeat the streaming export.
"""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

logger = logging.getLogger("beacon.request")

REQUEST_ID_HEADER = b"x-request-id"

# Paths whose traffic says nothing useful and would drown everything else.
QUIET_PREFIXES = ("/health", "/static")

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class JsonFormatter(logging.Formatter):
    """One JSON object per line, so a log shipper needs no parsing rules."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "context", {}))

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s")
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


class RequestLogging:
    """Times every request and logs it, carrying a correlation id through."""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex[:16]
        path = scope.get("path", "")
        started = time.perf_counter()
        status_seen = 0

        async def send_with_id(message: Message) -> None:
            nonlocal status_seen
            if message["type"] == "http.response.start":
                status_seen = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception(
                "request failed",
                extra={"context": _context(request_id, scope, path, 500, started)},
            )
            raise

        # Deliberately absent: the client address, the query string, and the
        # values filled into a path. All three are stripped from the data this
        # service stores, so logging them would put back exactly what the
        # product promises to discard -- see loggable_path.
        logger.log(
            logging.DEBUG if path.startswith(QUIET_PREFIXES) else logging.INFO,
            "request",
            extra={"context": _context(request_id, scope, path, status_seen, started)},
        )


def _incoming_request_id(scope: Scope) -> str | None:
    """Honour an id set upstream, so one request is traceable across services."""
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER:
            decoded: str = value.decode("latin-1")
            return decoded[:64]
    return None


def loggable_path(scope: Scope, path: str) -> str:
    """The route template when one matched, never the filled-in path.

    A path parameter is user data, and on two routes it is data this service
    has promised not to keep. `/reset/{token}` carries a live credential: an
    hour of the log is an hour of working links into any account that asked for
    one. `/sites/{site_id}` carries the customer's domain -- the same value
    `Referrer-Policy: same-origin` is set to stop leaking off the page, which
    it is not much use doing while writing it to every log line.

    Starlette puts the matched route on the scope during handling, and this
    runs afterwards, so the template is available by the time it is needed.

    A request that matched no route is logged as its first segment alone. It
    cannot be reduced to a template because there is none, and "it matched
    nothing, so it holds nothing" is false: `/reset/<token>/` with a trailing
    slash matches nothing and is answered with a redirect, which was enough to
    put a live token in the log while the matched route beside it was clean.
    The first segment still shows a scan for `/wp-admin` or a broken link into
    `/sites`, which is what these lines are read for.
    """
    template = getattr(scope.get("route"), "path", None)
    if isinstance(template, str):
        return template
    head = path.partition("?")[0].split("/")
    return f"/{head[1]}" if len(head) > 1 and head[1] else "/"


def _context(
    request_id: str, scope: Scope, path: str, status: int, started: float
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "method": scope.get("method", ""),
        "path": loggable_path(scope, path),
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
