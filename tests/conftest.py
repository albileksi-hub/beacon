import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import create_app
from app.services import accounts, rollups

CHROME_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

OWNER_EMAIL = "owner@example.com"
OWNER_PASSWORD = "a-perfectly-fine-password"
SITE_DOMAIN = "blue-mug.example"


@pytest.fixture
def db_session():
    """An isolated in-memory database per test.

    StaticPool keeps every connection pointed at the same in-memory database,
    which would otherwise be discarded when a connection is returned to the pool.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    """A test client that looks like a real browser unless a test says otherwise."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app, headers={"user-agent": CHROME_MAC}) as test_client:
        yield test_client


@pytest.fixture
def account(db_session):
    return accounts.register(db_session, email=OWNER_EMAIL, password=OWNER_PASSWORD)


@pytest.fixture
def site(db_session, account):
    """A registered domain. The collector ignores events for anything else."""
    return accounts.add_site(db_session, owner=account, domain=SITE_DOMAIN)


@pytest.fixture
def signed_in(client, account):
    response = client.post(
        "/login",
        data={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


@pytest.fixture
def rebuild_rollups(db_session):
    """Rebuild the aggregates the endpoints read.

    In production the background loop does this; in tests it has to happen
    after the events exist, so it is a callable rather than a plain fixture.
    """

    def rebuild(days_back: int = 3) -> None:
        rollups.refresh(db_session, days_back=days_back)

    return rebuild
