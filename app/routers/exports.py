from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.dependencies import MemberSite, SessionFactory, Window
from app.services import exports

router = APIRouter(tags=["export"], include_in_schema=False)


@router.get("/sites/{site_id}/export.csv")
def export_csv(
    site: MemberSite,
    sessions: SessionFactory,
    window: Window,
) -> StreamingResponse:
    """The site's aggregates, as a file.

    Resolved through membership, not readability. It used to be readability, on
    the reasoning that a published dashboard already serves these numbers over
    the API -- so withholding the same numbers in another shape would be
    theatre. The reasoning was wrong, and measurably so: breakdowns are capped
    at ten and the API refuses a larger limit, while this file has no cap at
    all. On a site with forty distinct pages a stranger saw ten and could
    download all forty, along with every referrer, campaign and screen size
    ever recorded. Unlinked pages, staging paths and internal tools are exactly
    the long tail that cap was hiding.

    Publishing a dashboard is a decision about the numbers on that page. It is
    not a decision to hand over the history. The template only ever offered
    this link to the owner, so the control existed -- it was just in the markup
    rather than in the handler, which is no control at all.
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
