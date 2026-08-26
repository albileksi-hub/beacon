from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse

from app.dependencies import DbSession, RequiredUser
from app.services import accounts, tokens
from app.templating import templates

router = APIRouter(tags=["keys"], include_in_schema=False)


def _account_page(
    request: Request,
    db: DbSession,
    user: RequiredUser,
    *,
    new_token: str | None = None,
    error: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    """Re-render the account page with whatever just happened on it.

    Rendered rather than redirected when there is a token to show, because the
    plaintext exists only in this response -- putting it in a query string
    would write it into the browser's history and every log in between.
    """
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user": user,
            "sites": accounts.sites_for(db, user),
            "tokens": tokens.for_owner(db, user),
            "new_token": new_token,
            "error": error,
        },
        status_code=status_code,
    )


@router.post("/keys")
def create_key(
    request: Request,
    db: DbSession,
    user: RequiredUser,
    name: Annotated[str, Form()],
) -> Response:
    try:
        _, plaintext = tokens.create(db, owner=user, name=name)
    except (tokens.TooManyTokens, tokens.InvalidTokenName) as error:
        return _account_page(
            request, db, user, error=str(error), status_code=status.HTTP_400_BAD_REQUEST
        )

    return _account_page(request, db, user, new_token=plaintext)


@router.post("/keys/{token_id}/revoke")
def revoke_key(db: DbSession, user: RequiredUser, token_id: int) -> Response:
    """Destroy a key.

    Answers the same way whether or not it existed: an id belonging to another
    account is indistinguishable from one that was already revoked, so this
    cannot be used to find out how many keys anybody else holds.
    """
    tokens.revoke(db, owner=user, token_id=token_id)
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
