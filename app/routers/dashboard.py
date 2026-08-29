import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dependencies import CurrentUser, DbSession
from app.services import accounts, charts, reports, timeranges, tokens, zones
from app.services.stats import BreakdownProperty
from app.services.timeranges import Period, resolve
from app.templating import templates

# Rendered pages, not part of the public API surface.
router = APIRouter(tags=["dashboard"], include_in_schema=False)

# Tab key, heading, and the dimension behind it.
PANELS = (
    ("page", "Pages", BreakdownProperty.PAGE),
    ("entry_page", "Entry pages", BreakdownProperty.ENTRY_PAGE),
    ("exit_page", "Exit pages", BreakdownProperty.EXIT_PAGE),
    ("source", "Sources", BreakdownProperty.SOURCE),
    ("country", "Countries", BreakdownProperty.COUNTRY),
    ("device", "Devices", BreakdownProperty.DEVICE),
    ("browser", "Browsers", BreakdownProperty.BROWSER),
    ("os", "Systems", BreakdownProperty.OS),
    ("screen", "Screens", BreakdownProperty.SCREEN),
    ("event", "Goals", BreakdownProperty.EVENT),
    ("campaign", "Campaigns", BreakdownProperty.CAMPAIGN),
    ("medium", "Mediums", BreakdownProperty.MEDIUM),
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
        request,
        "index.html",
        {
            "user": user,
            "sites": accounts.sites_for(db, user),
            "tokens": tokens.for_owner(db, user),
        },
    )


@router.get("/sites/{site_id}", response_class=HTMLResponse)
def site_dashboard(
    request: Request,
    site_id: str,
    db: DbSession,
    user: CurrentUser,
    period: Period = Period.LAST_30_DAYS,
    start: Annotated[dt.date | None, Query(alias="from")] = None,
    end: Annotated[dt.date | None, Query(alias="to")] = None,
) -> Response:
    site = accounts.readable_site(db, viewer=user, domain=site_id)
    if site is None:
        # A signed-out visitor may simply need to sign in. Anyone else is told
        # the same thing they would hear about a domain that does not exist.
        if user is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such site")

    # A bad range is shown rather than thrown: the page still renders, on the
    # default period, with the reason above the numbers. A 422 here would be a
    # blank screen for a typo in a URL somebody pasted.
    range_error = None
    try:
        time_range = timeranges.resolve_window(period, start, end, timezone=site.timezone)
    except timeranges.InvalidRange as error:
        range_error = str(error)
        start = end = None
        time_range = resolve(period, timezone=site.timezone)

    chose_dates = start is not None and end is not None
    if start is not None and end is not None:
        # The tiles carry a comparison whichever way the window was chosen, so
        # the page has to say what it is compared against. Naming the period
        # but not the comparison leaves a "-8.6%" on screen with nothing to
        # read it against.
        #
        # Day written out rather than "%-d", which is a GNU extension: Windows
        # wants "%#d" and this project is developed on one.
        span = (end - start).days + 1
        nights = "day" if span == 1 else "days"
        window_label = (
            f"{start.day} {start:%b %Y} to {end.day} {end:%b %Y}, "
            f"compared with the {span} {nights} before"
        )
    else:
        window_label = f"{PERIOD_LABELS[period].lower()}, compared with the period before"
    series = reports.timeseries(db, site_id=site.domain, time_range=time_range)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "site_id": site.domain,
            "is_public": site.public,
            "timezone": site.timezone,
            "currency": site.currency,
            "timezones": zones.COMMON,
            "is_owner": user is not None and site.owner_id == user.id,
            "period": period,
            "period_labels": PERIOD_LABELS,
            "window_label": window_label,
            "chose_dates": chose_dates,
            "range_from": start.isoformat() if start else "",
            "range_to": end.isoformat() if end else "",
            "range_error": range_error,
            # The axis ticks are shortened differently per grain.
            "interval": time_range.interval,
            "comparison": reports.summary_with_comparison(
                db, site_id=site.domain, time_range=time_range
            ),
            "live": reports.live_visitors(db, site_id=site.domain),
            "chart": charts.build(
                [point.visitors for point in series],
                [point.bucket for point in series],
            ),
            "visitor_spark": charts.sparkline([point.visitors for point in series]),
            "pageview_spark": charts.sparkline([point.pageviews for point in series]),
            "spark_width": charts.SPARKLINE_WIDTH,
            "spark_height": charts.SPARKLINE_HEIGHT,
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
