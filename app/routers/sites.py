from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.dependencies import DbSession, OwnedSite, RequiredUser
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
    site: OwnedSite,
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
    site: OwnedSite,
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
