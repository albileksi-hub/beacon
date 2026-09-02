from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import CurrentUser, DbSession, RequiredUser, SettingsDep
from app.models import User
from app.services import accounts, erasure, mail, recovery, throttle
from app.services.client import client_ip
from app.services.passwords import InvalidPassword
from app.templating import templates

router = APIRouter(tags=["auth"], include_in_schema=False)

EmailField = Annotated[str, Form()]
PasswordField = Annotated[str, Form()]

# 303 rather than 302, so the browser follows up with a GET instead of
# replaying the POST.
SEE_OTHER = status.HTTP_303_SEE_OTHER


def _start_session(request: Request, user: "User") -> None:
    # Cleared first so a pre-existing session cannot be reused after login.
    request.session.clear()
    request.session[accounts.SESSION_KEY] = user.id
    request.session[accounts.EPOCH_KEY] = user.session_epoch


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

    _start_session(request, user)
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
    _start_session(request, user)
    return RedirectResponse("/sites", status_code=SEE_OTHER)


@router.post("/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/login", status_code=SEE_OTHER)


RESET_SENT = (
    "If that address has an account, a link to choose a new password is on its way. "
    "The link is good for one hour."
)


@router.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request, user: CurrentUser) -> Response:
    if user is not None:
        return RedirectResponse("/sites", status_code=SEE_OTHER)
    return templates.TemplateResponse(request, "forgot.html", {})


@router.post("/forgot")
def forgot(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    email: EmailField,
    background: BackgroundTasks,
) -> Response:
    """Ask for a reset link.

    Answers the same way whether or not the address is registered, and throttles
    on the requester rather than the address -- rate limiting per address would
    let somebody lock a known account out of its own recovery.

    Answering the same way is not enough on its own: the mail used to be sent
    before the response was written, so a registered address took as long as an
    SMTP conversation and an unregistered one returned immediately. Measured
    against an unreachable relay that was 10,008ms against roughly 1ms -- the
    identical page arriving ten seconds late says everything the page refused
    to. It is queued behind the response now, so both answers are written at
    the same point in the same work.
    """
    marker = throttle.fingerprint(db, client_ip(request), purpose="reset")
    if throttle.is_locked(db, marker):
        return templates.TemplateResponse(
            request,
            "forgot.html",
            {"error": "Too many requests. Try again in a few minutes."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    throttle.record_failure(db, marker)

    issued = recovery.begin(db, email=email)
    if issued is not None:
        account, token = issued
        link = f"{settings.base_url.rstrip('/')}/reset/{token}"
        background.add_task(
            mail.deliver,
            settings,
            to=account.email,
            subject="Choose a new Beacon password",
            body=(
                f"Someone asked to reset the password for {account.email}.\n\n"
                f"{link}\n\n"
                "The link works once and expires in an hour. If this was not "
                "you, nothing has changed and you can ignore this message."
            ),
        )

    return templates.TemplateResponse(request, "forgot.html", {"sent": RESET_SENT})


@router.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(request: Request, db: DbSession, token: str) -> Response:
    if not recovery.is_live(db, token):
        return templates.TemplateResponse(
            request,
            "reset.html",
            {"expired": True},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return templates.TemplateResponse(request, "reset.html", {"token": token})


@router.post("/reset/{token}")
def reset(
    request: Request, db: DbSession, token: str, password: PasswordField
) -> Response:
    try:
        user = recovery.redeem(db, presented=token, new_password=password)
    except InvalidPassword as error:
        return templates.TemplateResponse(
            request,
            "reset.html",
            {"token": token, "error": str(error)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if user is None:
        return templates.TemplateResponse(
            request,
            "reset.html",
            {"expired": True},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    # Signed straight in, on a session minted under the new epoch. Every older
    # cookie for this account stopped being accepted a moment ago.
    _start_session(request, user)
    return RedirectResponse("/sites", status_code=SEE_OTHER)


@router.post("/account/delete")
def delete_account(
    request: Request, db: DbSession, user: RequiredUser, password: PasswordField
) -> Response:
    """Close an account and erase everything it owns.

    Guarded by the current password rather than a typed phrase. A session left
    open on a shared machine is the realistic way this gets triggered by
    somebody who should not, and only the password stops that.
    """
    if accounts.authenticate(db, email=user.email, password=password) is None:
        return templates.TemplateResponse(
            request,
            "account.html",
            {"user": user, "error": "That password does not match."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    erasure.delete_account(db, user=user)
    request.session.clear()
    return RedirectResponse("/", status_code=SEE_OTHER)


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user: RequiredUser) -> Response:
    return templates.TemplateResponse(request, "account.html", {"user": user})
