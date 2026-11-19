"""Browser worker lifecycle manager — the resident's grip on the heavy process.

Unlike daemon-local-models (one worker per declared model), the scraper has a
single browser worker. The manager:
  - finds a running worker via its port file + `/healthz`,
  - spawns one (detached) under a cross-process file lock if absent,
  - proxies scrape requests to it,
  - respawns transparently if the worker has self-exited on idle.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from filelock import FileLock, Timeout
from loguru import logger

from config import Settings


def _read_port(port_file: Path) -> int | None:
    try:
        raw = port_file.read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (FileNotFoundError, ValueError, OSError):
        return None


def _base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _popen_detached_kwargs() -> dict:
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        return {"creationflags": flags}
    return {"start_new_session": True}


class BrowserManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=settings.daemon_request_timeout_seconds)
        # In-process gate so concurrent requests don't race to spawn.
        self._gate = asyncio.Lock()
        tag = settings.user_tag
        self._port_file = settings.runtime_dir / f"browser-{tag}.port"
        self._lock_file = settings.runtime_dir / f"browser-{tag}.lock"
        self._pid_file = settings.runtime_dir / f"browser-{tag}.pid"
        self._log_file = settings.log_dir / "browser.log"

    # ---- health / spawn --------------------------------------------------

    async def _probe(self, port: int, timeout: float = 2.0) -> bool:
        try:
            r = await self._client.get(
                f"{_base_url(self.settings.daemon_host, port)}/healthz", timeout=timeout,
            )
            return r.status_code == 200
        except (httpx.TransportError, OSError):
            return False

    async def _probe_from_file(self) -> int | None:
        port = _read_port(self._port_file)
        if port is None:
            return None
        return port if await self._probe(port) else None

    def _spawn(self) -> None:
        s = self.settings
        s.runtime_dir.mkdir(parents=True, exist_ok=True)
        s.log_dir.mkdir(parents=True, exist_ok=True)
        env = {
            **os.environ,
            "SCRAPER_PROFILE": s.scraper_profile,
            "PROFILE_DIR": str(s.profile_dir),
            "PAGE_CONCURRENCY": str(s.page_concurrency),
            "SCRAPE_BATCH_CONCURRENCY": str(s.scrape_batch_concurrency),
            "BROWSER_IDLE_EVICT_MINUTES": str(s.browser_idle_evict_minutes),
            "BROWSER_DAEMON_SHUTDOWN_MINUTES": str(s.browser_daemon_shutdown_minutes),
            "DAEMON_HOST": s.daemon_host,
            "DAEMON_PORT_FILE": str(self._port_file),
            "DAEMON_PID_FILE": str(self._pid_file),
        }
        log_fh = open(self._log_file, "ab")
        try:
            subprocess.Popen(
                [sys.executable, "-m", "browser_daemon"],
                stdout=log_fh,
                stderr=log_fh,
                stdin=subprocess.DEVNULL,
                env=env,
                close_fds=True,
                **_popen_detached_kwargs(),
            )
        finally:
            log_fh.close()

    async def _wait_healthy(self, deadline: float) -> int | None:
        while time.monotonic() < deadline:
            port = await self._probe_from_file()
            if port is not None:
                return port
            await asyncio.sleep(0.5)
        return None

    async def ensure_running(self) -> int:
        """Return the port of a healthy worker, spawning if needed."""
        log = logger.bind(stage="ensure")
        async with self._gate:
            port = await self._probe_from_file()
            if port is not None:
                return port

            self.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
            for attempt in range(self.settings.daemon_spawn_retries + 1):
                lock = FileLock(str(self._lock_file))
                got = False
                try:
                    try:
                        lock.acquire(timeout=0)
                        got = True
                    except Timeout:
                        got = False

                    if got:
                        # Re-check under the lock — a peer may have just spawned.
                        if await self._probe_from_file() is None:
                            log.info("spawning worker (attempt {})", attempt + 1)
                            self._spawn()
                    else:
                        log.info("another process is spawning; waiting")

                    deadline = time.monotonic() + self.settings.daemon_spawn_health_timeout_seconds
                    port = await self._wait_healthy(deadline)
                    if port is not None:
                        return port
                    log.warning(
                        "worker not healthy within {}s",
                        self.settings.daemon_spawn_health_timeout_seconds,
                    )
                finally:
                    if got:
                        try:
                            lock.release()
                        except Exception:  # noqa: BLE001
                            pass

        raise RuntimeError(
            f"browser worker failed to become healthy after "
            f"{self.settings.daemon_spawn_retries + 1} attempts"
        )

    # ---- proxy -----------------------------------------------------------

    async def scrap(self, payload: dict) -> dict:
        return await self._call_retrying("POST", "/scrap", payload)

    async def scrap_batch(self, payload: dict) -> dict:
        return await self._call_retrying("POST", "/scrap/batch", payload)

    async def delete_profile(self) -> dict:
        return await self._call_retrying("DELETE", "/profile")

    async def _call_retrying(self, method: str, path: str, payload: dict | None = None) -> dict:
        """Call the worker; if the connection fails, make sure one is up and retry once."""
        try:
            return await self._call(method, path, payload)
        except (httpx.TransportError, OSError) as exc:
            logger.bind(stage=path.strip("/")).warning(
                "worker connection failed ({!r}); respawning and retrying once", exc,
            )
            await self.ensure_running()
            return await self._call(method, path, payload)

    async def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        port = await self.ensure_running()
        r = await self._client.request(
            method, f"{_base_url(self.settings.daemon_host, port)}{path}", json=payload,
        )
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._client.aclose()
