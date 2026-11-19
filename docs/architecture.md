# How it works

Two processes with different lifecycles, as in every `daemons/` project
([`../../DAEMONS.md`](../../DAEMONS.md)):

```
client ──HTTP (Bearer)──▶ resident (src/server.py, always up, 127.0.0.1:19020)
                              │  discover / spawn / proxy / respawn
                              ▼
                          browser worker (src/browser_daemon.py, ephemeral port)
                              │  lazy launch → idle close → self-exit
                              ▼
                          headless Chrome via patchright (persistent profile + stealth)
                              │
                              ▼
                          trafilatura → markdown `content`
```

The split exists so the browser's memory is fully reclaimed: an idle worker exits and the
OS takes everything back, while the public API stays small and always available.

## Resident

The resident holds no browser. `src/server.py` builds the app and serves `/health`; the
public API lives in `src/api/`, one module per zone under `/api/1.0`:

- `api/scrap.py` — `POST /scrap`, `POST /scrap/batch`;
- `api/profile.py` — `DELETE /profile`.

The zone's router refuses cross-origin browser requests and checks the token for every
route (`src/auth.py`), validates the request and hands it to `BrowserManager`
(`src/browser_manager.py`), which owns the single worker:

- **Discovery.** Reads the worker's port from `runtime/browser-<user>.port` and probes its
  `/healthz` (2 s timeout).
- **Spawn.** If nothing answers, takes a non-blocking file lock
  (`runtime/browser-<user>.lock`), re-checks, and starts `python -m browser_daemon` in its
  own session, passing settings through the environment. A process that loses the lock
  race waits for the winner's worker instead. It waits up to
  `DAEMON_SPAWN_HEALTH_TIMEOUT_SECONDS` for health and repeats the whole flow up to
  `DAEMON_SPAWN_RETRIES` more times. An in-process lock keeps concurrent requests from
  spawning twice.
- **Proxy.** Forwards the request and returns the worker's JSON. On a transport failure —
  the worker died (`RemoteProtocolError`), self-exited (`ConnectError`) or did not answer in
  time — it runs discovery/spawn again and retries once. Scrapes and the profile reset go
  through the same path.

## Worker

`src/browser_daemon.py` is configured only from the environment the resident passes and
does not import the resident's settings. At start it binds a free port (`bind :0`),
writes it atomically to the port file (temp file + rename) and writes its pid file.

Lifecycle, checked every 10 s by an idle watcher:

1. **Lazy launch** — Chromium starts on the first scrape, not with the process. A launch
   that fails (no browser build, see [operations](operations.md#upgrading-libraries-and-the-browser))
   fails that scrape, leaves nothing behind, and the next scrape tries again.
2. **Idle close** — after `BROWSER_IDLE_EVICT_MINUTES` (5) without requests the browser is
   closed; the process stays.
3. **Self-exit** — after `BROWSER_DAEMON_SHUTDOWN_MINUTES` (30) the process exits, removing
   its port and pid files first; the next request makes the resident start a new one.

Nothing is closed while requests are in flight: an `in_flight` counter gates both steps,
and a batch counts as one request. Closing the browser is bounded at 15 s and never fails
the worker: a browser that is already gone, or does not answer, is logged and forgotten.

### Internal contract

The worker's routes mirror the public ones without the `/api/1.0` prefix — `GET /healthz`,
`POST /scrap`, `POST /scrap/batch`, `DELETE /profile` — and share the models in
`src/schemas.py`. Resident and worker always run the same code: restarting the resident
also stops its worker, so the contract needs no compatibility aliases. The worker listens
on 127.0.0.1 on an ephemeral port; its routes are not part of the public API and are not
in `docs/openapi.json`. `/healthz` reports `ok`, `profile`, `loaded` (browser running),
`in_flight`, `idle_seconds` and `pid`.

## One scrape

`ScraperBackend` (`src/browser/context.py`) owns one persistent Chromium context:

1. Take a page slot — `PAGE_CONCURRENCY` slots shared by all requests.
2. Open a page and register the stealth script in its main world over CDP.
3. Navigate: wait for `DOMContentLoaded` (`networkidle` with `use_idle`), capped at
   `timeout`. If the cap fires, stop the pending load (`Page.stopLoading`) so the partial
   DOM can be read at once.
4. With `use_scroll` only: scroll to the bottom within what is left of the budget
   (`src/browser/scroll.py`, CDP `Input.synthesizeScrollGesture`).
5. Read the final URL, the rendered HTML and the title.
6. Extract the main content with trafilatura in a thread (`src/extract.py`).
7. Decide the verdict from the HTTP status, the HTML and the extracted content.
8. Close the CDP session and the page, each bounded at 5 s.

Steps 2–7 run under a deadline of `timeout` + 15 s: a page whose browser calls stop
answering is abandoned and reported as `timeout` instead of holding its slot forever.

A browser that died between scrapes — crashed, or killed — is noticed at step 2: the
scrape that cannot open a page fails (`internal` in a batch, `500` on its own), the dead
browser is dropped, and the next scrape launches a new one.

One CDP session per page, reused and closed explicitly: leaking sessions crashed the
browser driver under load.

## Verdicts

`_decide_outcome` in `src/browser/context.py`:

- With an HTTP status, the status and the HTML decide: 404 → `not_found`; 401/403/429 or a
  short challenge page → `access_denied`; other 4xx/5xx → `upstream_error`. A 2xx/3xx page
  with under 200 characters of extracted content becomes `empty`; a different final URL
  makes `redirected` (the URLs are compared percent-decoded: a Cyrillic path the browser
  reports encoded is the same page); the rest is `ok`.
- Without a status (the cap fired or the navigation failed), the content decides: 200+
  characters → `ok`, otherwise `timeout` after a fired cap, `upstream_down` after a failed
  navigation.

Challenge pages are recognised by markers — `just a moment`, `cf-browser-verification`,
`cf-challenge`, `captcha`, `access denied`, `attention required` — and only in HTML under
1500 characters, so a long article that mentions captchas stays `ok`.

## Content extraction

trafilatura over the rendered HTML, `output_format="markdown"`: links and tables kept,
images dropped, recall-oriented (`favor_precision=False`). It removes boilerplate, not bot
walls: a challenge page's text is extracted like any other, which is why the verdict
exists. The research behind the choice is in the project's agent notes.

## Anti-bot layer

- **Fingerprint profile** — `src/browser/profiles/<name>.json`, chosen by
  `SCRAPER_PROFILE` (see [configuration](configuration.md#fingerprint-profiles)). Its
  user agent, locale, timezone, viewport, screen and pixel ratio go into the browser
  launch options.
- **Stealth script** — `src/browser/stealth.py` builds a script from the same profile: CPU
  and memory, plugins, `userAgentData`, screen, `window.chrome`, WebGL vendor and renderer,
  slight canvas and audio noise. It disguises its own overrides as native code
  (`Function.prototype.toString`, `Object.getOwnPropertyDescriptor`).
- **Main-world injection** — patchright runs `add_init_script` in an isolated world, out
  of the page's reach. The script is therefore registered per page over CDP
  (`Page.enable` + `Page.addScriptToEvaluateOnNewDocument`), so the page's own code sees
  the profile.
- Chromium also starts with `--disable-blink-features=AutomationControlled`, on top of
  patchright's own patches.

`uv run pytest -m live -s` checks the result against browserscan, pixelscan and scrapfly.

## Runtime files

All generated, all gitignored:

| Path | What |
|---|---|
| `runtime/profile/` | Persistent Chromium user-data dir: cookies, cache, local storage. |
| `runtime/browser-<user>.port` | The worker's port — how the resident finds it. |
| `runtime/browser-<user>.pid` | The worker's pid — how `run.sh stop` kills it. |
| `runtime/browser-<user>.lock` | Spawn lock between resident processes. |
| `runtime/resident.pid` | The resident's pid, written by `run.sh start` or the systemd unit. |
| `runtime/logs/resident.log` | Resident log (loguru, rotated at 10 MB, kept 7 days). |
| `runtime/logs/resident.out` | stdout/stderr of `run.sh start`. |
| `runtime/logs/browser.log` | Worker stdout/stderr, not rotated. |

## Project layout

```
src/
  cli.py             entry point: simple-web-scraper → the resident
  server.py          resident: FastAPI app, /health, OpenAPI metadata
  api/               the /api/1.0 zones: scrap.py, profile.py
  auth.py            same-origin check + bearer token
  browser_manager.py resident side: discover / spawn / proxy / respawn the worker
  browser_daemon.py  the worker: python -m browser_daemon
  browser/
    context.py       ScraperBackend: browser context, page slots, scrape, verdicts
    stealth.py       build_stealth_js(profile)
    profile.py       BrowserProfile + load_profile
    scroll.py        kinetic CDP scroll for use_scroll
    profiles/*.json  fingerprint profiles
  extract.py         trafilatura: HTML → markdown
  schemas.py         request/response models — the source of the API reference
  openapi_spec.py    writes docs/openapi.json
  check/             live bot-detection self-checks (browserscan, pixelscan, scrapfly)
  config.py logging_setup.py
tests/               unit tests; tests/live — real browser
docs/                these guides + openapi.json
deploy/              systemd user unit simple-web-scraper.service
runtime/             generated, gitignored: browser profile, logs, pid/port/lock files
run.sh               start | stop | restart | status | logs | fg
```
