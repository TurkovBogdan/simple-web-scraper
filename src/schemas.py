"""Request/response models for the scraper API and the resident↔worker wire.

The public API reference (`docs/openapi.json`, `/docs`) is generated from these models:
the descriptions and examples here ARE that reference. After changing them, regenerate
the committed copy with `uv run python -m openapi_spec`.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from typing_extensions import TypeAliasType

# Named aliases become their own documented schemas in the OpenAPI document.
ScrapOutcome = TypeAliasType(
    "ScrapOutcome",
    Annotated[
        Literal[
            "ok",
            "redirected",
            "not_found",
            "access_denied",
            "upstream_error",
            "upstream_down",
            "empty",
            "timeout",
            "internal",
        ],
        Field(
            description=(
                "Verdict on the scrape — check it before using `content`. A failed scrape "
                "still comes back as HTTP 200, with the failure stated here.\n\n"
                "- `ok` — usable main content, also when the load cap fired but enough content "
                "had already loaded.\n"
                "- `redirected` — as `ok`, but the page ended on a different URL (`final_url`).\n"
                "- `not_found` — the target answered 404.\n"
                "- `access_denied` — the target answered 401, 403 or 429, or returned a short "
                "bot-wall or challenge page (Cloudflare, captcha).\n"
                "- `upstream_error` — any other 4xx or 5xx from the target.\n"
                "- `upstream_down` — no HTTP response: DNS or connection failure, or a "
                "resource the browser does not render as a page, such as a direct PDF.\n"
                "- `empty` — a response, but under 200 characters of main content, typically "
                "a JS shell; retry with `use_idle`.\n"
                "- `timeout` — the load cap fired before enough content loaded, or the page "
                "stopped answering and was abandoned.\n"
                "- `internal` — this URL failed inside the worker; batch only, the other URLs "
                "are unaffected."
            ),
        ),
    ],
)

# Speed-first default: no scenario should wait longer than ~5 s to load a page.
_TIMEOUT = 5  # budget for the whole page load: goto + waits + scroll

# Options shared by the single and the batch request; defaults stay on the models.
UseIdle = Annotated[
    bool,
    Field(
        description=(
            "Wait for network idle instead of `DOMContentLoaded`: for JS-rendered pages and "
            "SPAs that fill in after DOM ready. Slower; `timeout` still caps the wait."
        ),
    ),
]
UseScroll = Annotated[
    bool,
    Field(
        description=(
            "After the load, scroll to the bottom with a kinetic gesture: for lazy-loaded and "
            "infinite feeds. The scroll takes what is left of `timeout` after the load, at "
            "least 0.5 s."
        ),
    ),
]
Timeout = Annotated[
    int,
    Field(
        ge=1,
        le=60,
        description=(
            "Budget in whole seconds, 1 to 60, for loading the page: the wait for "
            "`DOMContentLoaded` (network idle with `use_idle`) and the scroll with "
            "`use_scroll`. Whatever loaded by then is still read and extracted. A page that "
            "stops answering is abandoned at `timeout` + 15 s and reported as `timeout`."
        ),
    ),
]


class ScrapRequest(BaseModel):
    """Scrape one page."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"url": "https://example.com"},
                {"url": "https://spa.example/app", "use_idle": True, "timeout": 8},
            ],
        },
    )

    url: str = Field(
        description="Absolute URL of the page, passed to the browser as is.",
        examples=["https://example.com"],
    )
    use_idle: UseIdle = False
    use_scroll: UseScroll = False
    timeout: Timeout = _TIMEOUT


class ScrapResponse(BaseModel):
    """Result of one scrape.

    Read `outcome` first: `content` is filled whatever the verdict, so a bot wall or an
    error page comes with text too.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "outcome": "ok",
                    "url": "https://example.com",
                    "final_url": "https://example.com/",
                    "target_status": 200,
                    "title": "Example Domain",
                    "headers": {"content-type": "text/html; charset=utf-8", "content-encoding": "br"},
                    "content": (
                        "This domain is for use in documentation examples without needing "
                        "permission. This is not a service; avoid relying on it for testing "
                        "and monitoring purposes."
                    ),
                    "body": '<!DOCTYPE html><html lang="en"><head>…</head><body>…</body></html>',
                    "redirects": [],
                    "elapsed_ms": 596,
                },
            ],
        },
    )

    outcome: ScrapOutcome
    url: str = Field(description="The requested URL, exactly as sent — match results by it.")
    final_url: str | None = Field(
        None,
        description=(
            "URL the page ended on after redirects. `about:blank` when the navigation never "
            "started (`timeout`); null when the page was abandoned or failed (`internal`)."
        ),
    )
    target_status: int | None = Field(
        None,
        description=(
            "HTTP status of the page's main document; null when there was no response "
            "(connection failure, the load cap fired first, abandoned page)."
        ),
    )
    title: str | None = Field(
        None,
        description="The document `<title>`; empty or null when the page could not be read.",
    )
    headers: dict[str, str] | None = Field(
        None,
        description=(
            "Response headers of the main document, lower-case names; null when there was "
            "no response."
        ),
    )
    content: str | None = Field(
        None,
        description=(
            "Main content as markdown: trafilatura removes navigation, headers, footers and "
            "similar boilerplate; links and tables are kept, images dropped. Filled whatever "
            "the `outcome` — on `access_denied` it is the text of the wall. Null when nothing "
            "could be extracted."
        ),
    )
    body: str | None = Field(
        None,
        description=(
            "Rendered HTML of the page (the DOM after load), unprocessed — for debugging or "
            "your own extraction. Can be large."
        ),
    )
    redirects: list[str] = Field(
        [],
        description="Reserved for intermediate redirect URLs; always empty for now.",
    )
    elapsed_ms: int = Field(
        0,
        description=(
            "Time spent on this URL in the worker, in milliseconds, including the wait for "
            "a free page slot."
        ),
    )


class ScrapBatchRequest(BaseModel):
    """Scrape several pages in one call; the options apply to every URL."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"urls": ["https://example.com", "https://httpbin.org/status/404"]},
            ],
        },
    )

    urls: list[str] = Field(
        description=(
            "Pages to scrape; empty strings are dropped. They load in parallel: at most "
            "`SCRAPE_BATCH_CONCURRENCY` from this batch at once, within the worker's "
            "`PAGE_CONCURRENCY` page slots that all requests share. The whole call has one "
            "resident→worker timeout (`DAEMON_REQUEST_TIMEOUT_SECONDS`, 180 s by default) — "
            "size batches to fit it."
        ),
        examples=[["https://example.com", "https://httpbin.org/status/404"]],
    )
    use_idle: UseIdle = False
    use_scroll: UseScroll = False
    timeout: Timeout = _TIMEOUT


class ScrapBatchResponse(BaseModel):
    """Results of a batch, one per URL."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "results": [
                        {
                            "outcome": "ok",
                            "url": "https://example.com",
                            "final_url": "https://example.com/",
                            "target_status": 200,
                            "title": "Example Domain",
                            "headers": {"content-type": "text/html; charset=utf-8"},
                            "content": "This domain is for use in documentation examples…",
                            "body": "<!DOCTYPE html><html lang=\"en\">…</html>",
                            "redirects": [],
                            "elapsed_ms": 69,
                        },
                        {
                            "outcome": "not_found",
                            "url": "https://httpbin.org/status/404",
                            "final_url": "https://httpbin.org/status/404",
                            "target_status": 404,
                            "title": "",
                            "headers": {"content-type": "text/html; charset=utf-8"},
                            "content": None,
                            "body": "<html><head></head><body></body></html>",
                            "redirects": [],
                            "elapsed_ms": 791,
                        },
                    ],
                    "elapsed_ms": 792,
                },
            ],
        },
    )

    results: list[ScrapResponse] = Field(
        [],
        description=(
            "One result per URL, in request order (after dropping empty strings). A URL that "
            "failed inside the worker comes back as `internal`; the batch itself does not fail."
        ),
    )
    elapsed_ms: int = Field(
        0,
        description="Wall time of the whole batch in the worker, in milliseconds.",
    )


class OkResponse(BaseModel):
    """Acknowledgement."""

    ok: bool = Field(True, description="Always `true`.")


# ---- errors --------------------------------------------------------------

class ErrorDetail(BaseModel):
    """What went wrong."""

    message: str = Field(description="Human-readable explanation.")
    type: str = Field(
        "invalid_request_error",
        description="Error class; `invalid_request_error` for every error the API raises itself.",
    )
    code: str | None = Field(
        None,
        description="Machine-readable code, e.g. `invalid_api_key`; null when there is none.",
    )


class ErrorResponse(BaseModel):
    """Error envelope of 400, 401 and 403 responses.

    Request-body validation errors (422) use FastAPI's `{"detail": [...]}` format instead.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "message": "Invalid or missing API key.",
                        "type": "invalid_request_error",
                        "code": "invalid_api_key",
                    },
                },
            ],
        },
    )

    error: ErrorDetail
