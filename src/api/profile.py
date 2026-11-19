"""Profile zone — `/api/1.0/profile`: the persistent browser profile."""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.common import WORKER_ERROR, browser
from schemas import OkResponse

router = APIRouter(prefix="/profile", tags=["profile"])


@router.delete(
    "",
    response_model=OkResponse,
    summary="Reset the browser profile",
    responses=WORKER_ERROR,
)
async def delete_profile(request: Request) -> OkResponse:
    """Delete the persistent browser profile (`runtime/profile/`: cookies, cache, local
    storage), closing the browser first; the next scrape starts with a fresh one. Works
    whether or not the browser is running — the worker is started if needed.

    It does not wait for scrapes in flight, it cuts them off: a single `/scrap` in progress
    fails, batch items in progress come back as `internal`. Use it after switching
    `SCRAPER_PROFILE`. Nothing restores a deleted profile.
    """
    return OkResponse(**await browser(request).delete_profile())
