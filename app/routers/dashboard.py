from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import CurrentUser, DbSession
from app.services import accounts, charts, stats
from app.services.stats import BreakdownProperty
from app.services.timeranges import Period, resolve
from app.templating import templates

# Rendered pages, not part of the public API surface.
router = APIRouter(tags=["dashboard"], include_in_schema=False)

PANELS = (
    ("Top pages", BreakdownProperty.PAGE),
    ("Sources", BreakdownProperty.SOURCE),
    ("Countries", BreakdownProperty.COUNTRY),
    ("Devices", BreakdownProperty.DEVICE),
)

PERIOD_LABELS = {
    Period.TODAY: "Today",
    Period.LAST_7_DAYS: "7 days",
    Period.LAST_30_DAYS: "30 days",
    Period.LAST_6_MONTHS: "6 months",
    Period.LAST_12_MONTHS: "12 months",
}


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: DbSession, user: CurrentUser):
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

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
):
    # Pages redirect rather than answering 401, which would show the browser a
    # bare error instead of a login form.
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    site = accounts.owned_site(db, owner=user, domain=site_id)
    if site is None:
        # See dependencies.require_owned_site: 404, never 403.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such site")

    time_range = resolve(period)
    series = stats.timeseries(db, site_id=site.domain, time_range=time_range)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "site_id": site.domain,
            "period": period,
            "period_labels": PERIOD_LABELS,
            "summary": stats.summary(db, site_id=site.domain, time_range=time_range),
            "live": stats.live_visitors(db, site_id=site.domain),
            "chart": charts.build(
                [point.visitors for point in series],
                [point.bucket for point in series],
            ),
            "panels": [
                (
                    title,
                    stats.breakdown(db, site_id=site.domain, time_range=time_range, prop=prop),
                )
                for title, prop in PANELS
            ],
        },
    )
