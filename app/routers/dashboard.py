from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import CurrentUser, DbSession
from app.services import accounts, charts, reports
from app.services.stats import BreakdownProperty
from app.services.timeranges import Period, resolve
from app.templating import templates

# Rendered pages, not part of the public API surface.
router = APIRouter(tags=["dashboard"], include_in_schema=False)

# Tab key, heading, and the dimension behind it.
PANELS = (
    ("page", "Pages", BreakdownProperty.PAGE),
    ("source", "Sources", BreakdownProperty.SOURCE),
    ("country", "Countries", BreakdownProperty.COUNTRY),
    ("device", "Devices", BreakdownProperty.DEVICE),
    ("browser", "Browsers", BreakdownProperty.BROWSER),
    ("os", "Systems", BreakdownProperty.OS),
    ("screen", "Screens", BreakdownProperty.SCREEN),
)

PERIOD_LABELS = {
    Period.TODAY: "Today",
    Period.LAST_7_DAYS: "7 days",
    Period.LAST_30_DAYS: "30 days",
    Period.LAST_6_MONTHS: "6 months",
    Period.LAST_12_MONTHS: "12 months",
}


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: DbSession, user: CurrentUser) -> Response:
    # Signed-out visitors get an explanation of what this is, rather than being
    # dropped straight onto a login form with no context.
    if user is None:
        return templates.TemplateResponse(request, "landing.html", {})

    return templates.TemplateResponse(
        request, "index.html", {"user": user, "sites": accounts.sites_for(db, user)}
    )


@router.get("/sites/{site_id}", response_class=HTMLResponse)
def site_dashboard(
    request: Request,
    site_id: str,
    db: DbSession,
    user: CurrentUser,
    period: Period = Period.LAST_30_DAYS,
) -> Response:
    # Someone asking for a particular dashboard wants that page, so send them
    # to the login form rather than to the marketing copy.
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    site = accounts.owned_site(db, owner=user, domain=site_id)
    if site is None:
        # See dependencies.require_owned_site: 404, never 403.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such site")

    time_range = resolve(period)
    series = reports.timeseries(db, site_id=site.domain, time_range=time_range)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "site_id": site.domain,
            "period": period,
            "period_labels": PERIOD_LABELS,
            "comparison": reports.summary_with_comparison(
                db, site_id=site.domain, time_range=time_range
            ),
            "live": reports.live_visitors(db, site_id=site.domain),
            "chart": charts.build(
                [point.visitors for point in series],
                [point.bucket for point in series],
            ),
            "panels": [
                (
                    key,
                    title,
                    reports.breakdown(
                        db, site_id=site.domain, time_range=time_range, prop=prop
                    ),
                )
                for key, title, prop in PANELS
            ],
        },
    )
