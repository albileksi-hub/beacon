from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import CurrentUser, DbSession
from app.services import accounts, throttle
from app.services.client import client_ip
from app.services.passwords import InvalidPassword
from app.templating import templates

router = APIRouter(tags=["auth"], include_in_schema=False)

EmailField = Annotated[str, Form()]
PasswordField = Annotated[str, Form()]

# 303 rather than 302, so the browser follows up with a GET instead of
# replaying the POST.
SEE_OTHER = status.HTTP_303_SEE_OTHER


def _start_session(request: Request, user_id: int) -> None:
    # Cleared first so a pre-existing session cannot be reused after login.
    request.session.clear()
    request.session[accounts.SESSION_KEY] = user_id


@router.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return RedirectResponse("/sites", status_code=SEE_OTHER)
    return templates.TemplateResponse(request, "signup.html", {})


@router.post("/signup")
def signup(
    request: Request, db: DbSession, email: EmailField, password: PasswordField
) -> Response:
    try:
        user = accounts.register(db, email=email, password=password)
    except (accounts.EmailAlreadyRegistered, InvalidPassword) as error:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": str(error), "email": email},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    _start_session(request, user.id)
    return RedirectResponse("/sites", status_code=SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return RedirectResponse("/sites", status_code=SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login(
    request: Request, db: DbSession, email: EmailField, password: PasswordField
) -> Response:
    marker = throttle.fingerprint(db, client_ip(request))
    if throttle.is_locked(db, marker):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Too many sign-in attempts. Try again in a few minutes.",
                "email": email,
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user = accounts.authenticate(db, email=email, password=password)
    if user is None:
        throttle.record_failure(db, marker)
        # One message for both causes: naming which half was wrong would
        # confirm whether an address is registered.
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "That email and password do not match.", "email": email},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    throttle.clear(db, marker)
    _start_session(request, user.id)
    return RedirectResponse("/sites", status_code=SEE_OTHER)


@router.post("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=SEE_OTHER)
