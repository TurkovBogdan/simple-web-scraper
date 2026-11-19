"""Access checks for the scraper API: same-origin and the bearer token."""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import get_settings

# auto_error=False: we validate ourselves so auth can be disabled when the key is
# empty. Registering the scheme makes the "Authorize" button appear in Swagger UI.
_bearer = HTTPBearer(auto_error=False, description="SCRAPER_API_KEY value")


async def same_origin_only(request: Request) -> None:
    """FastAPI dependency: refuse browser requests sent from another origin.

    The resident listens on localhost, and a web page open in a local browser can send it
    a POST that needs no CORS preflight (a form, a body-less or text body). Browsers always
    attach `Origin` to such requests; programs (curl, httpx) do not, and the API's own
    Swagger UI sends an `Origin` equal to `Host` — both pass.
    """
    origin = request.headers.get("origin")
    if origin is None or urlsplit(origin).netloc == request.headers.get("host"):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "message": "Cross-origin requests are refused.",
            "type": "invalid_request_error",
            "code": "origin_refused",
        },
    )


async def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """FastAPI dependency. If SCRAPER_API_KEY is empty, auth is disabled."""
    key = get_settings().scraper_api_key
    if not key:
        return
    token = credentials.credentials if credentials else None
    if token != key:
        raise HTTPException(
            status_code=401,
            detail={
                "message": "Invalid or missing API key.",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            },
        )
