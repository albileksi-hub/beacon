from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Site, User
from app.services import accounts

# Annotated dependencies keep FastAPI's injection out of function defaults,
# which keeps both linters and type checkers happy.
DbSession = Annotated[Session, Depends(get_db)]


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

    A site owned by somebody else is a 404, not a 403. A 403 would confirm the
    domain exists on the platform, which is enough to enumerate customers.
    """
    site = accounts.owned_site(db, owner=user, domain=site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such site")
    return site


OwnedSite = Annotated[Site, Depends(require_owned_site)]


def require_readable_site(site_id: str, db: DbSession, user: CurrentUser) -> Site:
    """The site named in the path, for anyone entitled to read it.

    That is its owner, or anybody at all once the owner has published it. A
    site that exists but is neither is a 404, exactly like one that does not.
    """
    site = accounts.readable_site(db, viewer=user, domain=site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such site")
    return site


ReadableSite = Annotated[Site, Depends(require_readable_site)]
