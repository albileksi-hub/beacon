import datetime as dt
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session, sessionmaker

from app.db import SessionLocal, get_db
from app.models import Site, User
from app.services import accounts, timeranges, tokens
from app.services.timeranges import Period, TimeRange

# Annotated dependencies keep FastAPI's injection out of function defaults,
# which keeps both linters and type checkers happy.
DbSession = Annotated[Session, Depends(get_db)]


def get_session_factory() -> sessionmaker[Session]:
    """A session factory, for work that outlives the request that started it.

    The streaming export produces its body after the handler has returned, so
    it cannot borrow the request's session -- that one is closed by then. It
    still comes through a dependency rather than being imported directly, so it
    can be pointed somewhere else in a test.
    """
    return SessionLocal


SessionFactory = Annotated[sessionmaker[Session], Depends(get_session_factory)]


def get_current_user(request: Request, db: DbSession) -> User | None:
    user_id = request.session.get(accounts.SESSION_KEY)
    return db.get(User, user_id) if user_id is not None else None


CurrentUser = Annotated[User | None, Depends(get_current_user)]


def require_user(user: CurrentUser) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    return user


RequiredUser = Annotated[User, Depends(require_user)]


def require_owned_site(site_id: str, db: DbSession, user: RequiredUser) -> Site:
    """The site named in the path, but only for the account that owns it.

    Ownership rather than administration: deciding who else may see a site is
    the owner's alone. A site owned by somebody else is a 404, not a 403 -- a
    403 would confirm the domain exists on the platform, which is enough to
    enumerate customers.
    """
    site = accounts.owned_site(db, owner=user, domain=site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such site")
    return site


OwnedSite = Annotated[Site, Depends(require_owned_site)]


def require_administered_site(site_id: str, db: DbSession, user: RequiredUser) -> Site:
    """The site named in the path, for anyone who may change its settings.

    Publishing and the timezone are administration, not ownership: an admin
    does the work and the owner decides who is an admin. Same 404 for the same
    reason.
    """
    site = accounts.administered_site(db, user=user, domain=site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such site")
    return site


AdministeredSite = Annotated[Site, Depends(require_administered_site)]


def get_api_account(request: Request, db: DbSession) -> User | None:
    """The account behind a bearer token, when the request carried one.

    Separate from the session because it grants strictly less: this feeds
    require_readable_site and nothing else, so a key reads numbers and cannot
    reach require_owned_site, which is what guards every route that changes
    something.
    """
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return tokens.resolve(db, value.strip())


ApiAccount = Annotated[User | None, Depends(get_api_account)]


def require_readable_site(
    site_id: str, db: DbSession, user: CurrentUser, api_account: ApiAccount
) -> Site:
    """The site named in the path, for anyone entitled to read it.

    That is its owner -- signed in, or holding one of their API keys -- or
    anybody at all once the owner has published it. A site that exists but is
    neither is a 404, exactly like one that does not.
    """
    site = accounts.readable_site(db, viewer=user or api_account, domain=site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such site")
    return site


ReadableSite = Annotated[Site, Depends(require_readable_site)]


def require_time_range(
    site: ReadableSite,
    period: Period = Period.LAST_30_DAYS,
    start: Annotated[dt.date | None, Query(alias="from")] = None,
    end: Annotated[dt.date | None, Query(alias="to")] = None,
) -> TimeRange:
    """The window a request is asking about: a named period, or two dates.

    A dependency rather than three lines in each of six handlers, so every
    endpoint that reports on a window accepts the same parameters and reads the
    site's own zone the same way. FastAPI resolves ReadableSite once per
    request, so asking for it here costs nothing.

    ``from`` and ``to`` are spelled that way in the URL because that is what a
    person types; ``from`` is a keyword, hence the aliases.
    """
    try:
        return timeranges.resolve_window(period, start, end, timezone=site.timezone)
    except timeranges.InvalidRange as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


Window = Annotated[TimeRange, Depends(require_time_range)]
