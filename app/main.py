import logging
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.background import lifespan
from app.config import Settings, get_settings
from app.dependencies import DbSession
from app.middleware import LimitRequestSize, SecurityHeaders
from app.observability import RequestLogging, configure_logging
from app.routers import auth, dashboard, exports, ingest, keys, sites, stats
from app.templating import STATIC_DIR, templates

logger = logging.getLogger(__name__)

# Statuses a person might actually reach in a browser, and what to tell them.
BROWSER_ERRORS = {
    401: ("You are not signed in", "Sign in to see this page."),
    403: ("Not allowed", "This account cannot see that."),
    404: ("Nothing here", "That page does not exist, or it belongs to another account."),
}


class CachedStatic(StaticFiles):
    """Static files, told how long they may be kept.

    Starlette sends an etag and a last-modified and no Cache-Control at all,
    so a browser revalidates every asset on every page load: a round trip per
    file to be told nothing changed. The templates already ask for these by a
    URL carrying a hash of the contents, which is exactly the condition under
    which a file can be kept forever -- the work of busting the cache was being
    done without taking the reward for it.

    A request without that hash is a different matter. beacon.js is served
    unhashed on purpose, because customers paste that URL into their own pages
    and it has to stay stable, which means a new version has to be able to
    reach them. Those get minutes rather than a year.
    """

    IMMUTABLE = "public, max-age=31536000, immutable"
    BRIEF = "public, max-age=300"

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        scope = args[2] if len(args) > 2 else kwargs["scope"]
        versioned = b"v=" in scope.get("query_string", b"")
        response.headers["cache-control"] = self.IMMUTABLE if versioned else self.BRIEF
        return response


def create_app() -> FastAPI:
    app = FastAPI(
        title="Beacon",
        description="Privacy-first, cookieless web analytics.",
        version="0.1.0",
        lifespan=lifespan,
    )

    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    if settings.session_secret == Settings.model_fields["session_secret"].default:
        # A warning was not enough. The default is a constant in a public
        # repository, so an instance running on it will sign session cookies
        # with a key the whole internet has -- anyone could mint a cookie for
        # any account on it. That is not a thing to mention in a log nobody
        # reads on the one morning it matters; it is a thing to refuse.
        if not settings.allow_insecure_sessions:
            raise RuntimeError(
                "BEACON_SESSION_SECRET is still the built-in default, which is a "
                "constant in a public repository -- anyone could forge a session "
                "for any account on this instance. Set it to something random:\n\n"
                '    BEACON_SESSION_SECRET="$(python -c '
                "\"import secrets; print(secrets.token_urlsafe(48))\")\"\n\n"
                "Or set BEACON_ALLOW_INSECURE_SESSIONS=true if this really is a "
                "throwaway instance nobody can reach."
            )

        logger.warning(
            "Running with the built-in session secret. Session cookies on this "
            "instance are forgeable by anyone with the source."
        )

    if not settings.session_https_only and not settings.allow_insecure_sessions:
        # The neighbouring insecure default, and it was going unguarded while
        # the one above it was refused. A session cookie without Secure is sent
        # over plain HTTP, so anyone between the browser and the server can
        # lift it and be signed in as that account -- and unlike a forged
        # cookie, this needs no secret at all. One flag already means "this is
        # a throwaway instance"; it may as well mean it for both.
        raise RuntimeError(
            "BEACON_SESSION_HTTPS_ONLY is false, so session cookies will be sent "
            "over plain HTTP and can be read off the wire. Set it to true once "
            "TLS is in front of this app:\n\n"
            "    BEACON_SESSION_HTTPS_ONLY=true\n\n"
            "Or set BEACON_ALLOW_INSECURE_SESSIONS=true if this really is a "
            "throwaway instance nobody can reach."
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

    # HSTS only where TLS is in front; see app.middleware for why sending it
    # from a plain-HTTP instance is worse than not sending it.
    app.add_middleware(SecurityHeaders, https=settings.session_https_only)

    # The stylesheet is 36 KB of text and was going over the wire uncompressed;
    # CSS is mostly repeated identifiers, so it packs down by roughly eight to
    # one. The floor keeps it off the collector's replies, which are a couple
    # of dozen bytes and would only grow.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # Added after RequestLogging so it runs before it: an oversized body is
    # refused without being read, and still gets logged on the way out.
    app.add_middleware(LimitRequestSize, max_bytes=settings.max_request_bytes)

    # Outermost, so the timing covers everything else and every response
    # carries a request id -- including ones the error handler produces.
    app.add_middleware(RequestLogging)

    app.include_router(auth.router)
    app.include_router(ingest.router)
    app.include_router(stats.router)
    app.include_router(sites.router)
    app.include_router(keys.router)
    app.include_router(exports.router)
    app.include_router(dashboard.router)
    app.mount("/static", CachedStatic(directory=STATIC_DIR), name="static")

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
    def health(request: Request, db: DbSession, response: Response) -> dict[str, str | int]:
        """Liveness, a database round-trip, and the ingest buffer.

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

        report: dict[str, str | int] = {"status": "ok", "database": "ok"}

        # Dropped events are the one failure the service survives silently, so
        # the count has to be visible somewhere a monitor can reach it.
        writer = getattr(request.app.state, "event_writer", None)
        if writer is not None:
            stats = writer.stats
            report["queued_events"] = stats.queued
            report["dropped_events"] = stats.dropped

        return report

    return app


app = create_app()
