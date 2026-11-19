"""Public HTTP API of the resident, `/api/1.0`, split by entity.

- `scrap`   — `/scrap`, `/scrap/batch`: load pages, return their main content;
- `profile` — `/profile`: the persistent browser profile.

Every route here refuses cross-origin browser requests, then checks the bearer token.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api import profile, scrap
from api.common import AUTH_ERROR, ORIGIN_ERROR
from auth import require_api_key, same_origin_only

API_PREFIX = "/api/1.0"

router = APIRouter(
    prefix=API_PREFIX,
    dependencies=[Depends(same_origin_only), Depends(require_api_key)],
    responses={**ORIGIN_ERROR, **AUTH_ERROR},
)
router.include_router(scrap.router)
router.include_router(profile.router)

__all__ = ["API_PREFIX", "router"]
