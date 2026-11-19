# Configuration

Everything is set in `.env` in the project root. The first run copies it from
`.env.example` and never overwrites it afterwards. Paths are derived from the project root
and are not configured. Variable names are case-insensitive; an environment variable of
the same name overrides `.env`.

The resident reads the settings at start and hands the worker's share to every worker it
spawns, so a change takes effect after a restart: `systemctl --user restart
simple-web-scraper` or `./run.sh restart`. Both stop the worker too.

## Variables

| Variable | Default | Description |
|---|---|---|
| `SCRAPER_API_KEY` | *(empty)* | Bearer token for `/api/1.0/*`. Empty turns authentication off — local development only. |
| `SCRAPER_HOST` | `127.0.0.1` | Where the resident listens. |
| `SCRAPER_PORT` | `19020` | Resident port, the first of the pool 19020–19029. |
| `SCRAPER_PROFILE` | `ru-rtx3060` | Fingerprint profile, see below. |
| `PAGE_CONCURRENCY` | `10` | Page slots in the worker, shared by all requests. |
| `SCRAPE_BATCH_CONCURRENCY` | `10` | URLs of one batch loading at once (within the page slots). |
| `BROWSER_IDLE_EVICT_MINUTES` | `5.0` | Idle time before the browser is closed; the worker stays. |
| `BROWSER_DAEMON_SHUTDOWN_MINUTES` | `30.0` | Idle time before the worker exits. |
| `DAEMON_REQUEST_TIMEOUT_SECONDS` | `180.0` | How long the resident waits for the worker per call, batches included. |
| `DAEMON_SPAWN_HEALTH_TIMEOUT_SECONDS` | `60.0` | How long a freshly spawned worker has to answer `/healthz`. |
| `DAEMON_SPAWN_RETRIES` | `2` | Extra attempts of the whole spawn flow. |
| `DAEMON_HOST` | `127.0.0.1` | Where the worker listens and the resident looks for it. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` — the resident's log only. |
| `LOG_ROTATION` | `10 MB` | When `runtime/logs/resident.log` rotates (loguru syntax). |
| `LOG_RETENTION` | `7 days` | How long rotated resident logs are kept. |

The worker logs everything to `runtime/logs/browser.log`; the `LOG_*` settings do not apply to it.

Generate a key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(36))"
```

## Fingerprint profiles

A profile is a coherent desktop identity: user agent, `userAgentData`, locale, timezone,
screen, viewport, CPU and memory, WebGL vendor and renderer. All shipped profiles describe
Chrome 147 on Linux x86_64, locale `ru-RU`, timezone `Europe/Moscow`, languages `ru`,
`ru-RU`, `en-US`, `en`, and an NVIDIA card:

| Profile | GPU | CPU threads | Memory | Viewport |
|---|---|---|---|---|
| `ru-rtx3060` *(default)* | RTX 3060 | 8 | 4 GB | 1903×946 |
| `ru-rtx3060-ti` | RTX 3060 Ti | 12 | 8 GB | 1903×946 |
| `ru-rtx3070` | RTX 3070 | 16 | 8 GB | 2543×1306 |
| `ru-rtx3080` | RTX 3080 | 20 | 8 GB | 2543×1306 |
| `ru-rtx4060` | RTX 4060 | 12 | 8 GB | 1903×946 |
| `ru-rtx4070` | RTX 4070 | 16 | 8 GB | 2543×1306 |
| `ru-rtx4070-sup` | RTX 4070 SUPER | 20 | 8 GB | 2543×1306 |
| `ru-rtx4080` | RTX 4080 | 24 | 8 GB | 2543×1306 |
| `ru-rtx4090` | RTX 4090 | 32 | 8 GB | 3423×1306 |
| `ru-rtx5090` | RTX 5090 | 28 | 8 GB | 3823×2026 |

The browser itself is Chrome for Testing 156: the profiles claim an older version than
the engine runs.

To switch:

1. Set `SCRAPER_PROFILE` in `.env`.
2. Restart the resident.
3. `DELETE /api/1.0/profile`, so cookies and storage from the old identity do not
   contradict the new one.

The profile values are checked against public detectors; change them only together with
`uv run pytest -m live -s`.
