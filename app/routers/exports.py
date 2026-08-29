from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.dependencies import ReadableSite, SessionFactory, Window
from app.services import exports

router = APIRouter(tags=["export"], include_in_schema=False)


@router.get("/sites/{site_id}/export.csv")
def export_csv(
    site: ReadableSite,
    sessions: SessionFactory,
    window: Window,
) -> StreamingResponse:
    """The site's aggregates, as a file.

    Resolved through ReadableSite rather than ownership: a published dashboard
    already serves these numbers over the API, so refusing the same numbers in
    a different shape would be theatre rather than a control.
    """
    time_range = window

    return StreamingResponse(
        exports.daily_stats_csv(sessions, site_id=site.domain, time_range=time_range),
        media_type="text/csv; charset=utf-8",
        headers={
            "content-disposition":
                f'attachment; filename="{exports.filename_for(site.domain, time_range)}"'
        },
    )
