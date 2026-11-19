# Changelog

Notable changes to simple-web-scraper. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), the package version follows
[Semantic Versioning](https://semver.org/). The HTTP API carries its own version in the
URL path (`/api/1.0/`).

## [Unreleased]

### Changed

- `mode` is replaced by two flags that combine: `use_idle` waits for network idle (was
  `mode=browser_idle`), `use_scroll` scrolls to the bottom after the load (was
  `mode=browser_scroll`). Without them a page loads as `mode=browser_load` did. A `mode`
  field in a request is ignored.
- `timeout` is whole seconds from 1 to 60 (default 5) and covers the whole page load,
  the scroll included: the scroll takes what the load left of it, at least 0.5 s.

### Removed

- The deprecated `POST /api/1.0/scrap-batch` and `POST /api/1.0/clear-profile` — they
  now answer `404`. Use `POST /api/1.0/scrap/batch` and `DELETE /api/1.0/profile`: same
  body, same response.
- The `format` request field: `content` is always markdown.
- `mode=get`: it was reserved and never implemented.
- `load_timeout`, which was ignored, and `scroll_timeout`, now covered by `timeout`. Both
  are ignored if sent.

### Fixed

- A `timeout` of `Infinity`, `NaN`, below 1 or above 60, or with a fraction gets `422`.
  Before, `Infinity` failed the scrape with `500` (`internal` in a batch), and the other
  values were taken silently.

## [0.4.1] — 2026-11-20

### Added

- The API is split by entity into two zones: **scrap** — `POST /api/1.0/scrap`,
  `POST /api/1.0/scrap/batch`; **profile** — `DELETE /api/1.0/profile` resets the browser
  profile.
- API reference generated from the code: descriptions and examples for every field and
  endpoint, declared error responses (400, 401, 403, 422, 500), the load modes and verdicts
  as documented schemas (`ScrapMode`, `ScrapOutcome`, `ExtractFormat`). Committed as
  `docs/openapi.json`; `tests/test_openapi.py` fails when it drifts from the code.
- Guides in `docs/`: API, architecture, configuration, operations.
- systemd user unit for the resident in `deploy/`.

### Changed

- Renamed `daemon-web-scrapper` → `simple-web-scraper`: the folder
  (`daemons/simple-web-scraper`), the package and its console script, the systemd unit
  (`simple-web-scraper.service`) and the title of the API reference. The port, the API
  paths and the `SCRAPER_*` settings are unchanged.
- Logs moved from `logs/` to `runtime/logs/`: every generated file now lives under
  `runtime/`.
- Browser engine: patchright 1.49.0 → 1.64.4, Chromium 131 → Chrome for Testing 156. All
  other libraries upgraded. The fingerprint profiles still describe Chrome 147.
- `/health` and the profile reset declare their response schema (`OkResponse`); the
  response itself is unchanged, `{"ok": true}`.
- README trimmed to an overview; the details moved to `docs/`.

### Deprecated

- `POST /api/1.0/scrap-batch` — use `POST /api/1.0/scrap/batch` (same body and response).
- `POST /api/1.0/clear-profile` — use `DELETE /api/1.0/profile` (same effect and response).

Both still work and are marked deprecated in the reference; they are removed once known
clients have moved. urb-research's `web_scrapper` connector still calls `/scrap-batch`.

### Fixed

- A page whose browser calls stop answering no longer holds its page slot for good: it is
  abandoned at `timeout` + 15 s and reported as `timeout`. Before, a few such pages left
  the worker serving nothing while its health check still passed.
- Resetting the profile no longer reports success when the worker vanished mid-call — the
  profile was not deleted then. It now restarts the worker and retries once, like the
  scrapes.
- A browser launch that fails no longer leaks its driver. With the browser build missing
  (as between `patchright install chromium` and the restart), every failed URL left a
  node process and its pipes behind, and after a few hundred the worker answered
  everything with `Too many open files` until it was restarted.
- A browser that died between scrapes (crashed, killed) is dropped by the first scrape
  that cannot open a page, and the next scrape launches a new one. Before, the worker
  kept the dead browser and failed every scrape with `Target page, context or browser
  has been closed` for as long as requests kept coming.
- Closing the browser can no longer end the worker's idle watcher or abort its shutdown:
  a browser that is already gone, or does not answer within 15 s, is logged and
  forgotten. Before, the shutdown logged `Application shutdown failed` and left the port
  and pid files behind.
- The worker's idle self-exit removes its port and pid files, as a shutdown does;
  `run.sh status` no longer reports an exited worker as running.
- A URL with non-ASCII characters (a Cyrillic path, a space) no longer comes back
  `redirected` when the page did not move: the browser reports it percent-encoded, and
  the two forms are now compared decoded.

### Security

- `/api/1.0/*` refuses requests a browser sends from another origin (`Origin` ≠ `Host`):
  `403`, code `origin_refused`. A web page open in a local browser could fire the
  body-less `POST /clear-profile` without a CORS preflight and, with authentication off,
  wipe the profile. The scrape routes were not exposed — they accept only a JSON body,
  which a cross-origin page cannot send without a preflight. Clients without `Origin`
  (curl, httpx) and the Swagger UI at `/docs` are unaffected.

## [0.1.0] — 2026-07-08

### Added

- Resident on `127.0.0.1:19020` with a browser worker started on demand, closed after
  5 idle minutes and exited after 30; respawned transparently.
- `GET /health`, `POST /api/1.0/scrap`, `POST /api/1.0/scrap-batch`,
  `POST /api/1.0/clear-profile`; Bearer authentication with `SCRAPER_API_KEY`.
- Main content as markdown via trafilatura in `content`, the rendered HTML in `body`.
- Load modes `browser_load`, `browser_idle`, `browser_scroll` (`get` reserved), with a
  5-second cap on page load.
- Verdicts in `outcome`: `ok`, `redirected`, `not_found`, `access_denied`,
  `upstream_error`, `upstream_down`, `empty`, `timeout`, `internal`.
- Fingerprint profiles `ru-rtx3060` … `ru-rtx5090` and a stealth script injected into the
  page's main world.
