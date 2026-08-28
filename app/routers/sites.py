from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.dependencies import AdministeredSite, DbSession, OwnedSite, RequiredUser
from app.models import Role, Site
from app.services import accounts, tokens, zones
from app.templating import templates

router = APIRouter(tags=["sites"], include_in_schema=False)


@router.post("/sites")
def create_site(
    request: Request,
    db: DbSession,
    user: RequiredUser,
    domain: Annotated[str, Form()],
) -> Response:
    try:
        site = accounts.add_site(db, owner=user, domain=domain)
    except (accounts.DomainAlreadyRegistered, accounts.InvalidDomain) as error:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "user": user,
                "sites": accounts.sites_for(db, user),
                "tokens": tokens.for_owner(db, user),
                "error": str(error),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return RedirectResponse(f"/sites/{site.domain}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sites/{site_id}/visibility")
def change_visibility(
    site: AdministeredSite,
    db: DbSession,
    public: Annotated[bool, Form()],
) -> Response:
    """Publish a dashboard, or take it back.

    Resolved through OwnedSite rather than ReadableSite: a public dashboard is
    readable by anyone, but only its owner decides that.
    """
    accounts.set_visibility(db, site=site, public=public)
    return RedirectResponse(f"/sites/{site.domain}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sites/{site_id}/timezone")
def change_timezone(
    site: AdministeredSite,
    db: DbSession,
    timezone: Annotated[str, Form()],
) -> Response:
    """Set the zone this site's days are reckoned in.

    Only affects events from here on. Days already aggregated keep the
    boundaries they were built with, because the raw events behind them may
    well have been deleted by retention.
    """
    try:
        accounts.set_timezone(db, site=site, timezone=timezone)
    except zones.UnknownTimezone as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error

    return RedirectResponse(f"/sites/{site.domain}", status_code=status.HTTP_303_SEE_OTHER)


def _people_page(
    request: Request, db: Session, site: Site, error: str | None = None
) -> Response:
    return templates.TemplateResponse(
        request,
        "people.html",
        {
            "site_id": site.domain,
            "members": accounts.members_of(db, site),
            "roles": [Role.ADMIN, Role.VIEWER],
            "error": error,
        },
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


@router.get("/sites/{site_id}/people", response_class=HTMLResponse)
def people(request: Request, db: DbSession, site: OwnedSite) -> Response:
    """Who can see this site. Resolved through OwnedSite: an admin does the
    work on a site, but only its owner decides who else is let in."""
    return _people_page(request, db, site)


@router.post("/sites/{site_id}/people")
def add_person(
    request: Request,
    db: DbSession,
    site: OwnedSite,
    email: Annotated[str, Form()],
    role: Annotated[str, Form()],
) -> Response:
    try:
        accounts.add_member(db, site=site, email=email, role=Role(role))
    except (accounts.MembershipError, ValueError) as error:
        return _people_page(request, db, site, str(error))

    return RedirectResponse(
        f"/sites/{site.domain}/people", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/sites/{site_id}/people/{user_id}/role")
def change_person_role(
    request: Request,
    db: DbSession,
    site: OwnedSite,
    user_id: int,
    role: Annotated[str, Form()],
) -> Response:
    try:
        accounts.set_member_role(db, site=site, user_id=user_id, role=Role(role))
    except (accounts.MembershipError, ValueError) as error:
        return _people_page(request, db, site, str(error))

    return RedirectResponse(
        f"/sites/{site.domain}/people", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/sites/{site_id}/people/{user_id}/remove")
def remove_person(request: Request, db: DbSession, site: OwnedSite, user_id: int) -> Response:
    try:
        accounts.remove_member(db, site=site, user_id=user_id)
    except accounts.MembershipError as error:
        return _people_page(request, db, site, str(error))

    return RedirectResponse(
        f"/sites/{site.domain}/people", status_code=status.HTTP_303_SEE_OTHER
    )
