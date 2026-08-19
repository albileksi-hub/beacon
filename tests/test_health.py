from types import SimpleNamespace

from sqlalchemy.exc import OperationalError

from app.db import get_db


def test_health_reports_ok_when_the_database_answers(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_degraded_when_the_database_does_not(client):
    """A check that only proves the process is up reports a broken app as fine."""

    class Unreachable:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    client.app.dependency_overrides[get_db] = lambda: Unreachable()

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}


def test_health_reports_the_ingest_buffer_when_there_is_one(client):
    """A dropped event is the one failure the service survives in silence."""
    client.app.state.event_writer = SimpleNamespace(
        stats=SimpleNamespace(queued=7, dropped=3)
    )

    body = client.get("/health").json()

    assert body["queued_events"] == 7
    assert body["dropped_events"] == 3


def test_health_says_nothing_about_a_buffer_that_does_not_exist(client):
    body = client.get("/health").json()

    assert body == {"status": "ok", "database": "ok"}
