import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dependencies import AdministeredSite, DbSession, OwnedSite, RequiredUser
from app.models import Role, Site, User
from app.routers.dashboard import PERIOD_LABELS
from app.services import accounts, erasure, funnels, timeranges, tokens, zones
from app.services.timeranges import Period
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
    request: Request, db: Session, site: Site, user: User, error: str | None = None
) -> Response:
    # `user` is not decoration: base.html renders the signed-out header without
    # it, so the page offered a "Sign in" link to somebody already signed in.
    return templates.TemplateResponse(
        request,
        "people.html",
        {
            "user": user,
            "site_id": site.domain,
            "members": accounts.members_of(db, site),
            "roles": [Role.ADMIN, Role.VIEWER],
            "error": error,
        },
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


@router.get("/sites/{site_id}/people", response_class=HTMLResponse)
def people(
    request: Request, db: DbSession, site: OwnedSite, user: RequiredUser
) -> Response:
    """Who can see this site. Resolved through OwnedSite: an admin does the
    work on a site, but only its owner decides who else is let in."""
    return _people_page(request, db, site, user)


@router.post("/sites/{site_id}/people")
def add_person(
    request: Request,
    db: DbSession,
    site: OwnedSite,
    user: RequiredUser,
    email: Annotated[str, Form()],
    role: Annotated[str, Form()],
) -> Response:
    try:
        accounts.add_member(db, site=site, email=email, role=Role(role))
    except (accounts.MembershipError, ValueError) as error:
        return _people_page(request, db, site, user, str(error))

    return RedirectResponse(
        f"/sites/{site.domain}/people", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/sites/{site_id}/people/{user_id}/role")
def change_person_role(
    request: Request,
    db: DbSession,
    site: OwnedSite,
    user: RequiredUser,
    user_id: int,
    role: Annotated[str, Form()],
) -> Response:
    try:
        accounts.set_member_role(db, site=site, user_id=user_id, role=Role(role))
    except (accounts.MembershipError, ValueError) as error:
        return _people_page(request, db, site, user, str(error))

    return RedirectResponse(
        f"/sites/{site.domain}/people", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/sites/{site_id}/people/{user_id}/remove")
def remove_person(
    request: Request, db: DbSession, site: OwnedSite, user: RequiredUser, user_id: int
) -> Response:
    try:
        accounts.remove_member(db, site=site, user_id=user_id)
    except accounts.MembershipError as error:
        return _people_page(request, db, site, user, str(error))

    return RedirectResponse(
        f"/sites/{site.domain}/people", status_code=status.HTTP_303_SEE_OTHER
    )


def _funnels_page(
    request: Request,
    db: Session,
    site: Site,
    user: User,
    period: Period,
    start: dt.date | None,
    end: dt.date | None,
    error: str | None = None,
) -> Response:
    """Every funnel on this site, measured over the window on the page."""
    window = timeranges.resolve_window(period, start, end, timezone=site.timezone)
    first, last = window.days

    measured = [
        (funnel, funnels.measure(db, funnel=funnel, first_day=first, last_day=last))
        for funnel in funnels.for_site(db, site.id)
    ]

    return templates.TemplateResponse(
        request,
        "funnels.html",
        {
            "user": user,
            "site_id": site.domain,
            "funnels": measured,
            "period": period,
            "period_labels": PERIOD_LABELS,
            "retention_days": get_settings().raw_event_retention_days,
            "error": error,
        },
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


@router.get("/sites/{site_id}/funnels", response_class=HTMLResponse)
def funnels_page(
    request: Request,
    db: DbSession,
    site: AdministeredSite,
    user: RequiredUser,
    period: Period = Period.LAST_30_DAYS,
    start: Annotated[dt.date | None, Query(alias="from")] = None,
    end: Annotated[dt.date | None, Query(alias="to")] = None,
) -> Response:
    """Resolved through AdministeredSite: a funnel is a setting, not a number."""
    return _funnels_page(request, db, site, user, period, start, end)


@router.post("/sites/{site_id}/funnels")
def create_funnel(
    request: Request,
    db: DbSession,
    site: AdministeredSite,
    user: RequiredUser,
    name: Annotated[str, Form()],
    steps: Annotated[str, Form()],
) -> Response:
    try:
        funnels.create(db, site=site, name=name, raw_steps=steps)
    except funnels.FunnelError as error:
        return _funnels_page(
            request, db, site, user, Period.LAST_30_DAYS, None, None, str(error)
        )

    return RedirectResponse(
        f"/sites/{site.domain}/funnels", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/sites/{site_id}/funnels/{funnel_id}/delete")
def delete_funnel(
    request: Request,
    db: DbSession,
    site: AdministeredSite,
    user: RequiredUser,
    funnel_id: int,
) -> Response:
    try:
        funnels.delete(db, site=site, funnel_id=funnel_id)
    except funnels.FunnelError as error:
        return _funnels_page(
            request, db, site, user, Period.LAST_30_DAYS, None, None, str(error)
        )

    return RedirectResponse(
        f"/sites/{site.domain}/funnels", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/sites/{site_id}/settings", response_class=HTMLResponse)
def site_settings(request: Request, site: OwnedSite, user: RequiredUser) -> Response:
    """Where the irreversible things live, away from the daily controls."""
    return templates.TemplateResponse(request, "settings.html", {"user": user, "site": site})


@router.post("/sites/{site_id}/delete")
def delete_site(
    request: Request,
    db: DbSession,
    user: RequiredUser,
    site: OwnedSite,
    confirm: Annotated[str, Form()],
) -> Response:
    """Delete a site and everything recorded against its domain.

    Behind OwnedSite rather than AdministeredSite: an admin runs a site, but
    destroying it is the owner's decision. The typed confirmation is the
    domain itself, so this cannot be reached by a stray click on the row above
    the one intended.
    """
    if confirm.strip().lower() != site.domain.lower():
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "user": user,
                "site": site,
                "error": f"Type {site.domain} exactly to confirm.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    erasure.delete_site(db, site=site)
    return RedirectResponse("/sites", status_code=status.HTTP_303_SEE_OTHER)
