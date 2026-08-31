"""Request logging.

Chiefly: that the log line does not put back the two things the rest of the
service goes out of its way to discard.
"""

import asyncio
import json
import logging
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import JsonFormatter, RequestLogging, configure_logging

SENSITIVE_PATH = "/sites/blue-mug.example"


def test_a_request_is_logged_with_its_timing(client, caplog):
    with caplog.at_level(logging.INFO, logger="beacon.request"):
        client.get("/")

    record = next(r for r in caplog.records if r.name == "beacon.request")
    context = record.context

    assert context["method"] == "GET"
    assert context["path"] == "/"
    assert context["status"] == 200
    assert context["duration_ms"] >= 0


def test_the_log_never_carries_a_query_string(client, caplog):
    """A URL's query is stripped from stored data; logging it would undo that."""
    with caplog.at_level(logging.INFO, logger="beacon.request"):
        client.get("/login", params={"email": "someone@example.com", "token": "s3cr3t"})

    logged = " ".join(json.dumps(r.context) for r in caplog.records if r.name == "beacon.request")

    assert "someone@example.com" not in logged
    assert "s3cr3t" not in logged
    assert "/login" in logged


def test_the_log_never_carries_the_client_address(client, caplog):
    with caplog.at_level(logging.INFO, logger="beacon.request"):
        client.get("/", headers={"x-forwarded-for": "203.0.113.9"})

    record = next(r for r in caplog.records if r.name == "beacon.request")

    assert "203.0.113.9" not in json.dumps(record.context)
    assert set(record.context) == {"request_id", "method", "path", "status", "duration_ms"}


def test_every_response_carries_a_request_id(client):
    response = client.get("/")

    assert len(response.headers["x-request-id"]) == 16


def test_an_upstream_request_id_is_honoured(client):
    """So one request stays traceable across whatever sits in front of this."""
    response = client.get("/", headers={"x-request-id": "from-the-proxy"})

    assert response.headers["x-request-id"] == "from-the-proxy"


def test_health_checks_are_logged_quietly(client, caplog):
    """Otherwise an orchestrator's probes drown out the real traffic."""
    with caplog.at_level(logging.DEBUG, logger="beacon.request"):
        client.get("/health")

    record = next(r for r in caplog.records if r.name == "beacon.request")
    assert record.levelno == logging.DEBUG


def test_the_json_formatter_emits_one_object_per_line():
    record = logging.LogRecord(
        "beacon.request", logging.INFO, __file__, 1, "request", None, None
    )
    record.context = {"status": 200, "path": "/"}

    parsed = json.loads(JsonFormatter().format(record))

    assert parsed["message"] == "request"
    assert parsed["level"] == "info"
    assert parsed["status"] == 200
    assert parsed["path"] == "/"


def test_the_json_formatter_includes_a_traceback():
    try:
        raise ValueError("something broke")
    except ValueError:
        record = logging.LogRecord(
            "beacon.request", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )

    parsed = json.loads(JsonFormatter().format(record))

    assert "ValueError: something broke" in parsed["exception"]


def test_configure_logging_installs_a_single_handler():
    configure_logging(level="WARNING", json_output=True)
    root = logging.getLogger()

    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.WARNING

    configure_logging()  # leave the root logger as the other tests expect


def test_a_failing_request_is_logged_and_still_raised(caplog):
    """A 500 is exactly when the log line matters most."""
    app = FastAPI()

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    app.add_middleware(RequestLogging)

    with (
        TestClient(app, raise_server_exceptions=False) as client,
        caplog.at_level(logging.ERROR, logger="beacon.request"),
    ):
        response = client.get("/boom")

    assert response.status_code == 500
    record = next(r for r in caplog.records if r.name == "beacon.request")
    assert record.levelno == logging.ERROR
    assert record.context["status"] == 500
    assert record.context["path"] == "/boom"


def test_non_http_traffic_passes_straight_through():
    """Lifespan and websocket scopes have no request to time."""
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    async def exercise():
        await RequestLogging(inner)({"type": "lifespan"}, None, None)

    asyncio.run(exercise())

    assert seen == ["lifespan"]


@pytest.mark.parametrize(
    ("path", "logged", "why"),
    [
        ("/reset/a-live-reset-token", "/reset/{token}", "a token is a credential"),
        ("/reset/a-live-reset-token/", "/reset", "a near miss is answered with a redirect"),
        ("/sites/blue-mug.example", "/sites/{site_id}", "a domain identifies the customer"),
        ("/wp-admin/setup-config.php", "/wp-admin", "a scan stays visible"),
        ("/definitely-not-a-route", "/definitely-not-a-route", "one segment is already safe"),
        ("/", "/", "the root survives the split"),
    ],
)
def test_no_path_parameter_ever_reaches_the_log(client, caplog, path, logged, why):
    """The values filled into a path are user data, and on two routes they are
    data this service has promised not to keep.

    `/reset/{token}` carries a live credential: an hour of log is an hour of
    working links into any account that asked for one. `/sites/{site_id}`
    carries the customer's domain -- the value `Referrer-Policy: same-origin`
    exists to stop leaking off the page, which is not much use while writing it
    into every log line beside it.

    The trailing-slash case is the one that matters most here. It matched no
    route, was answered with a redirect, and put the whole token in the log
    while the route next to it was clean -- so "it matched nothing, therefore
    it holds nothing" is not a safe rule to log by.
    """
    with caplog.at_level(logging.DEBUG, logger="beacon.request"):
        client.get(path)

    paths = [c["path"] for r in caplog.records if (c := getattr(r, "context", None))]
    assert logged in paths, f"{why}: expected {logged!r}, logged {paths}"
    assert not [p for p in paths if "a-live-reset-token" in p or "blue-mug" in p]
