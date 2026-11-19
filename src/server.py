"""Lightweight scraper resident.

Always-on, cheap process: handles auth and routes scrape requests to a heavy
browser worker it spawns and manages. Holds no browser itself.

This app is the public API: `/health` here, the `/api/1.0` zones in `api/`. Its OpenAPI
document (`docs/openapi.json`) is generated from the route metadata and the models in
`schemas.py`.
"""
from __future__ import annotations

import math
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger

import api
from browser_manager import BrowserManager
from config import Settings, get_settings
from logging_setup import setup_logging
from schemas import OkResponse

_DESCRIPTION = """\
Loads web pages in a real headless Chrome and returns their main content as markdown.

**Zones.** `/api/1.0/scrap` loads pages — one (`POST /scrap`) or several
(`POST /scrap/batch`). `/api/1.0/profile` is the browser's persistent profile —
`DELETE /profile` resets it. `/health` is the resident's liveness.

**Authentication.** Every `/api/1.0/*` call takes `Authorization: Bearer <SCRAPER_API_KEY>`.
With an empty `SCRAPER_API_KEY` the check is off — local development only. `/health` is
always open. Requests a browser sends from another origin are refused (403).

**Verdicts, not HTTP errors.** A scrape that fails on the target's side — blocked, not
found, timed out — still returns HTTP 200; the verdict is in `outcome`. An HTTP error means
the request itself was rejected (400, 401, 403, 422) or the browser worker could not serve
it (500).

**Cold start.** The first call after idle starts the browser worker, about one to two
seconds on top of the scrape.

**No URL filtering.** The browser opens whatever URL it is given, internal addresses and
`file://` paths included. Keep the API on 127.0.0.1 and set a key.

Guide — modes, verdicts, limits, errors: `docs/api.md` in the repository.
"""

_TAGS = [
    {"name": "scrap", "description": "Load pages in the browser and return their main content."},
    {"name": "profile", "description": "The persistent browser profile: cookies, cache, local storage."},
    {"name": "service", "description": "Liveness of the resident."},
]


def _version() -> str:
    try:
        return version("simple-web-scraper")
    except PackageNotFoundError:  # a bare source tree without `uv sync`
        return "0.0.0"


class _Services:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.browser = BrowserManager(settings)

    async def aclose(self) -> None:
        await self.browser.aclose()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings, component="resident")
    if not settings.scraper_api_key:
        logger.bind(stage="boot").warning(
            "SCRAPER_API_KEY is empty — the API is UNAUTHENTICATED",
        )
    logger.bind(stage="boot").info(
        "resident up host={} port={} profile={}",
        settings.scraper_host, settings.scraper_port, settings.scraper_profile,
    )
    app.state.services = _Services(settings)
    try:
        yield
    finally:
        await app.state.services.aclose()
        logger.bind(stage="boot").info("resident stopped")


app = FastAPI(
    title="simple-web-scraper",
    version=_version(),
    summary="Scrape pages in a real browser; get their main content as markdown.",
    description=_DESCRIPTION,
    openapi_tags=_TAGS,
    lifespan=_lifespan,
)


@app.exception_handler(HTTPException)
async def _error(_request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        error = detail
    else:
        error = {"message": str(detail), "type": "invalid_request_error", "code": None}
    return JSONResponse(status_code=exc.status_code, content={"error": error})


def _json_safe(value: Any) -> Any:
    """JSON has no Infinity or NaN: echo such inputs back as strings."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


@app.exception_handler(RequestValidationError)
async def _invalid(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's own 422, except that a body with `Infinity` or `NaN` (Python's JSON parser
    accepts both) gets its 422 too instead of failing to render it with a 500."""
    errors = _json_safe(jsonable_encoder(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/health", response_model=OkResponse, tags=["service"], summary="Resident liveness")
async def health() -> OkResponse:
    """Answers without authentication and without touching the browser worker: the resident
    is up, which does not promise that a scrape would succeed."""
    return OkResponse()


app.include_router(api.router)


def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.scraper_host, port=settings.scraper_port, access_log=False)
