# simple-web-scraper

A local scraping service: send it a URL, get the page's **main content as markdown**.
Pages load in a real headless Chrome behind a fingerprint profile and a stealth script —
it passes the public bot detectors browserscan, pixelscan and scrapfly — and trafilatura
strips navigation, footers and other boilerplate.

It follows the `daemons/` resident + worker model: a small always-on **resident** serves
the API on `127.0.0.1:19020` and starts a heavy **browser worker** on demand; an idle
worker exits and its memory goes back to the OS. Family rules are in
[`../DAEMONS.md`](../DAEMONS.md), the port pool (19020–19029) in
[`../../NETWORKS.md`](../../NETWORKS.md).

Stack: Python 3.12, FastAPI + uvicorn, patchright 1.64 (Chrome for Testing 156),
trafilatura, httpx, filelock, loguru; run via `uv`.

## Quick start

```bash
uv sync
uv run patchright install chromium      # one-time browser download
./run.sh start                           # resident on 127.0.0.1:19020, in the background
curl -s localhost:19020/health           # {"ok":true}
```

The first run creates `.env` from `.env.example`. Set `SCRAPER_API_KEY` there; empty means
no authentication, for local development only. For autostart at boot see
[operations](docs/operations.md#autostart-systemd-user-unit).

## Scrape

```bash
curl -s localhost:19020/api/1.0/scrap \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'
```

```json
{
  "outcome": "ok",
  "url": "https://example.com",
  "final_url": "https://example.com/",
  "target_status": 200,
  "title": "Example Domain",
  "content": "This domain is for use in documentation examples without needing permission. …",
  "body": "<!DOCTYPE html><html lang=\"en\">…</html>",
  "elapsed_ms": 596
}
```

(`headers` and `redirects` trimmed.) The API has two zones: **scrap** — one page
(`POST /api/1.0/scrap`) or several in one call (`POST /api/1.0/scrap/batch` with
`{"urls": [...]}`, one result per URL, in order); **profile** — `DELETE /api/1.0/profile`
resets the browser's cookies, cache and storage.

**Read `outcome` before `content`.** A blocked or failed scrape still answers HTTP 200,
and `content` then holds whatever the page showed — a bot wall included.

## API reference

- **Running instance:** `/docs` (Swagger UI), `/redoc`, `/openapi.json`.
- **Repository:** [`docs/openapi.json`](docs/openapi.json) — generated from the code;
  opens in any OpenAPI viewer.
- **Guide:** [`docs/api.md`](docs/api.md) — modes, verdicts, time limits, errors, examples.

## Documentation

| File | What's inside |
|---|---|
| [`docs/api.md`](docs/api.md) | Calling the API: authentication, verdicts and what to do about them, load modes, time limits, batches, errors, security. |
| [`docs/architecture.md`](docs/architecture.md) | Resident and worker, the worker lifecycle, one scrape step by step, verdict rules, the anti-bot layer, runtime files, project layout. |
| [`docs/configuration.md`](docs/configuration.md) | Every `.env` variable; fingerprint profiles and how to switch them. |
| [`docs/operations.md`](docs/operations.md) | `run.sh`, systemd autostart, upgrading libraries and the browser, troubleshooting. |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed, version by version. |

## Development

```bash
uv run pytest                   # unit tests, no browser
uv run pytest -m live -s        # real Chrome: lifecycle + browserscan / pixelscan / scrapfly
uv run python -m openapi_spec   # after a public API change: rewrite docs/openapi.json
```

The API reference is written in the code — descriptions and examples in `src/schemas.py`
and `src/server.py`. `tests/test_openapi.py` fails while `docs/openapi.json` is stale.
Note public changes in [`CHANGELOG.md`](CHANGELOG.md).

## Limitations

- No PDF handling: a direct PDF URL comes back `upstream_down`.
- No fallback extractor when trafilatura finds nothing.
- No URL filtering (SSRF guard) and no response cache.
- The fingerprint profiles describe Chrome 147, the engine is Chrome 156.
