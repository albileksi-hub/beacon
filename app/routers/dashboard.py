from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import DbSession
from app.services import charts, stats
from app.services.stats import BreakdownProperty
from app.services.timeranges import Period, resolve

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.filters["comma"] = lambda value: f"{value:,}"

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
def index(request: Request, db: DbSession):
    return templates.TemplateResponse(
        request, "index.html", {"sites": stats.known_sites(db)}
    )


@router.get("/sites/{site_id}", response_class=HTMLResponse)
def site_dashboard(
    request: Request,
    site_id: str,
    db: DbSession,
    period: Period = Period.LAST_30_DAYS,
):
    time_range = resolve(period)
    series = stats.timeseries(db, site_id=site_id, time_range=time_range)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "site_id": site_id,
            "period": period,
            "period_labels": PERIOD_LABELS,
            "summary": stats.summary(db, site_id=site_id, time_range=time_range),
            "live": stats.live_visitors(db, site_id=site_id),
            "chart": charts.build(
                [point.visitors for point in series],
                [point.bucket for point in series],
            ),
            "panels": [
                (title, stats.breakdown(db, site_id=site_id, time_range=time_range, prop=prop))
                for title, prop in PANELS
            ],
        },
    )
