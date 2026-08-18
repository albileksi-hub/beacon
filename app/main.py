import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, get_settings
from app.routers import auth, dashboard, ingest, sites, stats

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Beacon",
        description="Privacy-first, cookieless web analytics.",
        version="0.1.0",
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

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
