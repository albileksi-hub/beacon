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

        # Deliberately absent: the client address and the query string. Both are
        # stripped from the data this service stores, so logging them would put
        # back exactly what the product promises to discard.
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


def _context(
    request_id: str, scope: Scope, path: str, status: int, started: float
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "method": scope.get("method", ""),
        "path": path,
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
