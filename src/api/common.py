"""Pieces shared by the API zones: declared error responses and the worker manager."""
from __future__ import annotations

from fastapi import Request

from browser_manager import BrowserManager
from schemas import ErrorResponse

AUTH_ERROR = {
    401: {
        "model": ErrorResponse,
        "description": "Missing or wrong bearer token. Only when `SCRAPER_API_KEY` is set.",
    },
}
ORIGIN_ERROR = {
    403: {
        "model": ErrorResponse,
        "description": (
            "Sent by a browser from another origin (`Origin` differs from `Host`), code "
            "`origin_refused`. Programs send no `Origin` and are not affected."
        ),
    },
}
WORKER_ERROR = {
    500: {
        "description": (
            "The browser worker failed or could not be reached, even after one respawn and "
            "retry. Plain text, not JSON."
        ),
        "content": {"text/plain": {"schema": {"type": "string"}, "example": "Internal Server Error"}},
    },
}


def browser(request: Request) -> BrowserManager:
    """The resident's manager of the browser worker."""
    return request.app.state.services.browser
