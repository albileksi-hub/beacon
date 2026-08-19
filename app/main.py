import logging

from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.background import lifespan
from app.config import Settings, get_settings
from app.dependencies import DbSession
from app.routers import auth, dashboard, ingest, sites, stats
from app.templating import STATIC_DIR, templates

logger = logging.getLogger(__name__)

# Statuses a person might actually reach in a browser, and what to tell them.
BROWSER_ERRORS = {
    401: ("You are not signed in", "Sign in to see this page."),
    403: ("Not allowed", "This account cannot see that."),
    404: ("Nothing here", "That page does not exist, or it belongs to another account."),
}


def create_app() -> FastAPI:
    app = FastAPI(
        title="Beacon",
        description="Privacy-first, cookieless web analytics.",
        version="0.1.0",
        lifespan=lifespan,
    )

    settings = get_settings()
    if settings.session_secret == Settings.model_fields["session_secret"].default:
        logger.warning(
            "BEACON_SESSION_SECRET is at its default value; session cookies are forgeable."
        )

    # samesite="lax" means the cookie is not sent on cross-site POSTs, which is
    # what stands in for CSRF tokens on these forms.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.session_https_only,
    )

    # The tracking script runs on other people's domains, so ingestion is
    # necessarily cross-origin. Only the collector is exposed this way.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    app.include_router(auth.router)
    app.include_router(ingest.router)
    app.include_router(stats.router)
    app.include_router(sites.router)
    app.include_router(dashboard.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(StarletteHTTPException)
    async def render_errors_for_browsers(
        request: Request, exc: StarletteHTTPException
    ) -> Response:
        """Give people a page and machines JSON.

        Without this, mistyping a dashboard URL answers with a raw JSON blob,
        which is a confusing thing for a browser to show.
        """
        wants_html = "text/html" in request.headers.get("accept", "")
        if (
            wants_html
            and not request.url.path.startswith("/api/")
            and exc.status_code in BROWSER_ERRORS
        ):
            heading, detail = BROWSER_ERRORS[exc.status_code]
            return templates.TemplateResponse(
                request,
                "error.html",
                {"status": exc.status_code, "heading": heading, "detail": detail},
                status_code=exc.status_code,
            )

        return await http_exception_handler(request, exc)

    @app.get("/health", tags=["ops"])
    def health(db: DbSession, response: Response) -> dict[str, str]:
        """Liveness plus a database round-trip.

        A health check that only proves the process is running will report a
        container as healthy while its database is unreachable, which is the
        one situation the check exists to catch.
        """
        try:
            db.execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception("health check could not reach the database")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "degraded", "database": "unreachable"}

        return {"status": "ok", "database": "ok"}

    return app


app = create_app()
