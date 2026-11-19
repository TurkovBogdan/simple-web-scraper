# API guide

How to call the scraper and read what it returns. The exact contract — every field,
type and default — is the OpenAPI document generated from the code:

- on a running instance: `/docs` (Swagger UI), `/redoc`, `/openapi.json`;
- in the repository: [`openapi.json`](openapi.json).

This guide explains what the reference cannot: when to use the load flags, what a verdict
means for you, where the time goes, and how errors look.

## Basics

- Base URL: `http://127.0.0.1:19020` (`SCRAPER_HOST`, `SCRAPER_PORT`).
- The API version is in the path: `/api/1.0/`.
- Bodies are JSON (`Content-Type: application/json`).

The API is split by entity into two zones, plus the resident's own liveness:

| Zone | Method | Path | Purpose |
|---|---|---|---|
| scrap | `POST` | `/api/1.0/scrap` | Scrape one page. |
| scrap | `POST` | `/api/1.0/scrap/batch` | Scrape several pages with shared options. |
| profile | `DELETE` | `/api/1.0/profile` | Reset the browser profile (cookies, cache, local storage). |
| service | `GET` | `/health` | The resident is up. No auth; does not touch the browser. |

## Authentication

Send the key as a bearer token:

```
Authorization: Bearer <SCRAPER_API_KEY>
```

With an empty `SCRAPER_API_KEY` the check is off and any token is accepted; the resident
logs a warning at start. `/health` never needs a token. A missing or wrong token, with a
key set, gets `401`:

```json
{"error": {"message": "Invalid or missing API key.", "type": "invalid_request_error", "code": "invalid_api_key"}}
```

Every `/api/1.0/*` route also refuses requests a browser sends from another origin — an
`Origin` header that differs from `Host` gets `403` with code `origin_refused`. Programs
(curl, httpx) send no `Origin` and are unaffected; the Swagger UI at `/docs` is the same
origin and works. A web page open in a local browser can send some requests without a
CORS preflight, such as a form or a POST without a JSON body; the check refuses them, which
matters most while authentication is off.

## Reading a result

A scrape that fails on the target's side still answers HTTP 200. Look at `outcome`
first: `content` is filled whatever the verdict, so a bot wall or an error page comes
with text too.

| `outcome` | Meaning | What to do |
|---|---|---|
| `ok` | Usable main content. | Use `content`. |
| `redirected` | As `ok`, but the page ended on another URL. | Use `content`; the real address is `final_url`. |
| `not_found` | The target answered 404. | Do not retry. |
| `access_denied` | 401/403/429, or a short bot-wall or challenge page. | `content` is the wall, not the page. Retrying right away rarely helps; for big platforms an official API is usually the way (Reddit, GitHub, YouTube). |
| `upstream_error` | Another 4xx or 5xx from the target. | 5xx: retry later. 4xx: usually permanent. |
| `upstream_down` | No HTTP response: DNS or connection failure, or a non-page resource such as a direct PDF. | Check the URL; PDFs are not handled. |
| `empty` | A response, but under 200 characters of main content — usually a JS shell. | Retry with `"use_idle": true`. |
| `timeout` | The load cap fired before enough content loaded, or the page stopped answering. | Retry with a larger `timeout`, or with `"use_idle": true`. |
| `internal` | This URL failed inside the worker (batch only). | Retry the URL. |

Successful verdicts are `ok` and `redirected`; treat anything else as no material, even
when `content` is not empty.

## Loading a page

By default the worker waits for `DOMContentLoaded`: server-rendered pages — articles,
docs, most sites — have their content by then, and it is the fastest. It deliberately does
not wait for the browser's `load` event, which also waits for every image, font and
analytics beacon: asset-heavy pages would time out although their content was already
there. Two flags change that, alone or together:

| Flag | Does | Use for |
|---|---|---|
| `use_idle` | Waits for network idle instead of `DOMContentLoaded`. | JS-rendered pages and SPAs that fill in after DOM ready. |
| `use_scroll` | After the load, scrolls to the bottom with a kinetic gesture. | Lazy-loaded and infinite feeds. |

## Where the time goes

Per page:

- `timeout` — whole seconds, 1 to 60, default 5 — is the budget for loading the page:
  the wait for `DOMContentLoaded` (network idle with `use_idle`) and the scroll with
  `use_scroll`. When the load runs out of it, the worker stops the pending load and still
  reads and extracts whatever arrived — enough content is reported as `ok`.
- `use_scroll` takes what the load left of `timeout`, at least 0.5 s.
- A page that stops answering is abandoned at `timeout` + 15 s and reported as `timeout`;
  closing it may add up to 10 s more.

Per call:

- The first call after idle starts the browser worker: about one to two seconds extra.
- The resident waits for the worker at most `DAEMON_REQUEST_TIMEOUT_SECONDS` (180 s by
  default) per call — for a batch, for the whole batch.
- If the connection to the worker breaks or the wait runs out, the resident makes sure a
  worker is up — a new one if the old died, the same one if it still answers — and
  retries the call once. The worst case for one call is therefore about twice that
  timeout, after which you get `500`.

## Batches

- Results come back in request order, one per URL; empty strings in `urls` are dropped.
- A URL that fails inside the worker comes back as `internal`; the batch does not fail.
- At most `SCRAPE_BATCH_CONCURRENCY` (default 10) URLs of one batch load at once, and every
  request shares the worker's `PAGE_CONCURRENCY` (default 10) page slots. Two batches sent
  together take turns for the same slots.
- Size batches so that the slowest plausible run fits the call timeout. A page that hangs
  takes about 30 s (5 s cap + 15 s grace + up to 10 s to close it); with 10 slots that is
  60 such pages per 180 s, less when other requests share the slots.
- A batch counts as one request for the idle timers: the browser is not closed in the
  middle of it.

## Errors

| Status | When | Body |
|---|---|---|
| `400` | `url` is empty, or `urls` is empty once empty strings are dropped. | `{"error": {...}}` envelope. |
| `401` | A key is set and the token is missing or wrong. | `{"error": {...}}`, `code` = `invalid_api_key`. |
| `403` | A browser sent the request from another origin. | `{"error": {...}}`, `code` = `origin_refused`. |
| `404`, `405` | Unknown path, wrong method (e.g. `POST /api/1.0/profile`). | `{"detail": "Not Found"}`, `{"detail": "Method Not Allowed"}`. |
| `422` | The body fails validation: no `url`, a `timeout` that is not a whole number from 1 to 60, a flag that is not a boolean. | FastAPI format: `{"detail": [{"type", "loc", "msg", "input", …}]}`. |
| `500` | The worker failed or could not be reached, even after one respawn and retry. | Plain text `Internal Server Error`. |

A `422` example:

```json
{"detail": [{"type": "missing", "loc": ["body", "url"], "msg": "Field required", "input": {}}]}
```

## Resetting the profile

`DELETE /api/1.0/profile` closes the browser and deletes `runtime/profile/` — cookies,
cache, local storage; the next scrape starts with a fresh profile. It works whether or not
the browser is running: the worker is started if needed, and if it vanishes mid-call the
resident starts it again and retries once.

It does not wait for scrapes in flight: a single `/scrap` in progress fails, batch items
in progress come back as `internal`. Nothing restores a deleted profile. Call it after
switching `SCRAPER_PROFILE`, so old cookies do not contradict the new fingerprint — not
as routine maintenance, and never just to see what it answers.

## Security

- The resident binds to `127.0.0.1` and, with an empty key, accepts any program that can
  reach it. Set `SCRAPER_API_KEY` when anything else runs on the machine. Browsers on
  other origins are refused either way (see [Authentication](#authentication)).
- There is no URL filtering. The browser opens internal addresses and `file://` paths as
  readily as public pages — `file:///etc/hostname` comes back with the file in `body`.
  Never expose the API beyond localhost.
- `content` is whatever the page says. If it goes to an LLM agent, treat it as untrusted
  input: a page can carry instructions aimed at the reader.

## Examples

```bash
TOKEN=...   # SCRAPER_API_KEY; drop the header if auth is off

# one page
curl -s localhost:19020/api/1.0/scrap \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# a JS-rendered page, with a longer cap
curl -s localhost:19020/api/1.0/scrap \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://spa.example/app", "use_idle": true, "timeout": 8}'

# several pages
curl -s localhost:19020/api/1.0/scrap/batch \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com", "https://httpbin.org/status/404"]}'

# reset the browser profile — destructive, see above
curl -s -X DELETE localhost:19020/api/1.0/profile -H "Authorization: Bearer $TOKEN"
```

```python
import httpx

GOOD = {"ok", "redirected"}

r = httpx.post(
    "http://127.0.0.1:19020/api/1.0/scrap/batch",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"urls": ["https://example.com", "https://httpbin.org/status/404"]},
    timeout=200,  # above the resident's own 180 s
)
r.raise_for_status()
for item in r.json()["results"]:
    if item["outcome"] in GOOD:
        print(item["url"], len(item["content"] or ""), "chars")
    else:
        print(item["url"], "→", item["outcome"])  # no material, whatever `content` says
```
