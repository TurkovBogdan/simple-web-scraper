"""Scrape zone — `/api/1.0/scrap`: load pages in the browser, return their main content."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from api.common import WORKER_ERROR, browser
from schemas import (
    ErrorResponse,
    ScrapBatchRequest,
    ScrapBatchResponse,
    ScrapRequest,
    ScrapResponse,
)

router = APIRouter(prefix="/scrap", tags=["scrap"])


@router.post(
    "",
    response_model=ScrapResponse,
    summary="Scrape one page",
    response_description="The scrape result; the verdict is in `outcome`.",
    responses={400: {"model": ErrorResponse, "description": "`url` is empty."}, **WORKER_ERROR},
)
async def scrap(req: ScrapRequest, request: Request) -> ScrapResponse:
    """Load the page in the browser and return its main content as markdown, with the
    verdict in `outcome` (see `ScrapOutcome`). `use_idle` and `use_scroll` change how the
    page is loaded."""
    if not req.url:
        raise HTTPException(status_code=400, detail="'url' must not be empty")
    return ScrapResponse(**await browser(request).scrap(req.model_dump()))


@router.post(
    "/batch",
    response_model=ScrapBatchResponse,
    summary="Scrape several pages",
    response_description="One result per URL, in request order.",
    responses={
        400: {"model": ErrorResponse, "description": "`urls` is empty once empty strings are dropped."},
        **WORKER_ERROR,
    },
)
async def scrap_batch(req: ScrapBatchRequest, request: Request) -> ScrapBatchResponse:
    """Load the pages in parallel with shared options and return one result per URL, in
    request order.

    A URL that fails inside the worker comes back as `internal` instead of failing the
    batch. For the idle timers the whole batch is one request, and it shares one
    resident→worker timeout.
    """
    urls = [u for u in req.urls if u]
    if not urls:
        raise HTTPException(status_code=400, detail="'urls' must not be empty")
    payload = req.model_dump()
    payload["urls"] = urls
    return ScrapBatchResponse(**await browser(request).scrap_batch(payload))
