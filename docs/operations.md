# Operations

Running, autostart, upgrades and troubleshooting. Only the resident is ever started by
hand or by systemd; the browser worker and Chrome are started by the resident on demand.

## Control script

```bash
./run.sh start      # resident in the background (pid → runtime/resident.pid)
./run.sh status     # resident + worker, exit code 0 either way
./run.sh logs       # follow runtime/logs/resident.log
./run.sh stop       # resident, then the worker by its pid file; clears port/lock files
./run.sh restart    # stop + start
./run.sh fg         # resident in the foreground (exec uv run …)
```

`stop` kills processes by their pid files only — never `pkill -f`, which would match
unrelated processes. Health check: `curl -s localhost:19020/health` → `{"ok":true}`.

## Autostart (systemd user unit)

A ready unit ships in `deploy/simple-web-scraper.service`.

### Install

```bash
ln -s "$PWD/deploy/simple-web-scraper.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now simple-web-scraper

sudo loginctl enable-linger "$USER"      # start at boot, not only at login
```

Symlink rather than copy, so the unit stays versioned with the project.

**Linger is not optional.** Without it a user unit lives only inside a login session: it
starts when you log in and dies with your last session, and it never starts at boot.
Check with `loginctl show-user "$USER" -p Linger` (want `Linger=yes`).

### Verify

```bash
systemctl --user is-enabled simple-web-scraper    # enabled
systemctl --user is-active  simple-web-scraper    # active
curl -s localhost:19020/health                    # {"ok":true}
./run.sh status                                   # resident + worker, exit 0
```

### Day to day

```bash
systemctl --user restart simple-web-scraper       # after editing .env
systemctl --user stop    simple-web-scraper       # also kills the worker and Chrome
journalctl --user -u simple-web-scraper -f        # stdout/stderr
tail -f runtime/logs/resident.log                 # the resident's own log
```

`./run.sh status` and `stop` keep working next to `systemctl`: the unit writes `$MAINPID`
into `runtime/resident.pid` on start. Prefer `systemctl restart` over `./run.sh restart`
once the unit is enabled — mixing them works (`Restart=on-failure` does not fight a
deliberate stop), it is just noisier.

### Why the unit looks the way it does

| Choice | Reason |
|---|---|
| `ExecStart=run.sh fg`, never `start` | `fg` execs `uv run …`, so systemd supervises the real process. `start` forks under `nohup` and systemd loses the pid. |
| `Environment=PATH=…/.local/bin:…` | `uv` is not on a unit's default PATH; without it the unit fails with `203/EXEC`. |
| `UV_FROZEN=1` | Keeps `uv run` from resolving or syncing over the network at boot. |
| `RequiresMountsFor=/mnt/store-dev` | The project lives on a separate volume; otherwise the unit races the mount at boot and fails on `WorkingDirectory`. Change it if you move the project. |
| `Restart=on-failure`, not `always` | A deliberate `./run.sh stop` is a clean SIGTERM; `always` would undo it at once. |
| `SuccessExitStatus=143 SIGTERM` | `uv` reports a forwarded SIGTERM as exit 143, not 0, even on a clean shutdown. Without this every stop leaves the unit `failed`, and `Restart=on-failure` would resurrect a deliberate `./run.sh stop`. |
| `ExecStopPost` removes `runtime/browser-*` | The worker sits in the service cgroup, so `KillMode=control-group` kills it before its own cleanup can delete the port/pid/lock files. |

### Uninstall

```bash
systemctl --user disable --now simple-web-scraper
rm ~/.config/systemd/user/simple-web-scraper.service
systemctl --user daemon-reload
sudo loginctl disable-linger "$USER"   # only if nothing else needs it
```

A system unit (`/etc/systemd/system/`) also works and needs no linger, but must set
`User=<you>`: runtime file names come from the current user, so running as root orphans
`runtime/browser-<you>.*` and the persistent profile.

## Upgrading libraries and the browser

patchright is pinned exactly in `pyproject.toml` (`patchright==…`): the stealth layer
depends on its internals, so it moves only when you bump the pin — `uv lock --upgrade`
leaves an exact pin alone. The order matters: the browser install deletes the previous
Chrome build before the new one is in place, and the new patchright already wants the new
build. Do the steps back to back:

```bash
# bump patchright==… in pyproject.toml first, if a new version is wanted
uv lock --upgrade
uv sync
uv run patchright install chromium
systemctl --user restart simple-web-scraper
uv run pytest && uv run pytest -m live -s
uv run python -m openapi_spec             # if FastAPI/pydantic changed the document
```

Until the restart, a newly spawned worker fails to launch the browser
(`Executable doesn't exist`) on every URL.

## Moving the project

The unit, the virtualenv and the browser registration all hold absolute paths. Stop the
resident before you touch the folder: a running one keeps the old paths, and its next
worker spawn fails with `500` while `/health` still answers.

```bash
systemctl --user disable --now simple-web-scraper
rm ~/.config/systemd/user/simple-web-scraper.service
mv /old/place/simple-web-scraper /new/place/   # then work from the new folder
rm -rf .venv && uv sync                        # scripts in .venv/bin hold the old path
uv run patchright install chromium             # registers the browser for the new .venv
# fix the paths (and RequiresMountsFor, if the volume changes) in
# deploy/simple-web-scraper.service, then install it again — see Install above
```

`patchright install` downloads nothing when the build is already there. Skip it, and the
next patchright or Playwright install by this user deletes the build as unused.

## Logs and runtime files

- `runtime/logs/resident.log` — the resident, rotated (`LOG_ROTATION`, `LOG_RETENTION`).
- `runtime/logs/browser.log` — the worker and the browser driver, not rotated.
- `runtime/logs/resident.out` — stdout/stderr of `./run.sh start`; under systemd see `journalctl`.
- `runtime/` — also the pid/port/lock files and the browser profile; see
  [architecture](architecture.md#runtime-files).

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Unit `status=203/EXEC` | `uv` not found — fix the `PATH=` line for your install. |
| Dead after reboot, fine after login | Linger is off. |
| Fails right after boot, starts fine by hand | The volume is not mounted yet — check `RequiresMountsFor`. |
| Runs, but `./run.sh status` says stopped | `runtime/resident.pid` missing — `ExecStartPost` failed, usually because `runtime/` does not exist. |
| Stale `runtime/browser-*` after `kill -9` | Only `systemctl stop`, `./run.sh stop` and a clean worker exit (shutdown or idle self-exit) remove them. Delete by hand; a dead port fails its health check anyway. |
| Every URL fails, `runtime/logs/browser.log` says `Executable doesn't exist` | The browser build is missing: `uv run patchright install chromium`, then restart the resident. |
| A scrape fails with `Target page, context or browser has been closed`, the next ones work | The browser died under the worker (crashed, killed); the worker dropped it and launched a new one. If it repeats, look for the crash in `runtime/logs/browser.log`. |
| Every scrape gets `500` at once while `/health` answers, after the folder moved | The resident still runs with the old paths — see [Moving the project](#moving-the-project). |
| Callers get `500` after minutes of waiting | The worker hangs or keeps dying; the resident retried once. Look at `runtime/logs/browser.log`, restart the resident. |
| Callers get connection refused | The resident is down — `./run.sh status`, `journalctl --user -u simple-web-scraper`. |
