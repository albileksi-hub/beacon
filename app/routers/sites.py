from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from app.dependencies import DbSession, RequiredUser
from app.services import accounts
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
            {"user": user, "sites": accounts.sites_for(db, user), "error": str(error)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return RedirectResponse(f"/sites/{site.domain}", status_code=status.HTTP_303_SEE_OTHER)
