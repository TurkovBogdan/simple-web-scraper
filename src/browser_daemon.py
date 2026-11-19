"""Heavy browser worker — launches one Chromium and serves scrape requests.

Spawned and managed by the lightweight resident (see `browser_manager.py`). The
resident passes identity and lifecycle tuning via environment variables; this
process is intentionally self-contained and does not import the resident's
`Settings`.

Lifecycle: lazy-launch on first request → close the browser after
`BROWSER_IDLE_EVICT_MINUTES` idle → exit the process after
`BROWSER_DAEMON_SHUTDOWN_MINUTES` idle. The resident transparently respawns it.
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI
from loguru import logger

from browser.context import ScraperBackend
from schemas import (
    ScrapBatchRequest,
    ScrapBatchResponse,
    ScrapRequest,
    ScrapResponse,
)


@dataclass
class _WorkerConfig:
    profile_name: str
    profile_dir: Path
    page_concurrency: int
    batch_concurrency: int
    idle_evict_seconds: float
    shutdown_seconds: float
    host: str
    port_file: Path
    pid_file: Path | None

    @classmethod
    def from_env(cls) -> "_WorkerConfig":
        pid_file = os.environ.get("DAEMON_PID_FILE")
        return cls(
            profile_name=os.environ.get("SCRAPER_PROFILE", "default"),
            profile_dir=Path(os.environ["PROFILE_DIR"]),
            page_concurrency=int(os.environ.get("PAGE_CONCURRENCY", "4")),
            batch_concurrency=int(os.environ.get("SCRAPE_BATCH_CONCURRENCY", "4")),
            idle_evict_seconds=float(os.environ.get("BROWSER_IDLE_EVICT_MINUTES", "5")) * 60.0,
            shutdown_seconds=float(os.environ.get("BROWSER_DAEMON_SHUTDOWN_MINUTES", "30")) * 60.0,
            host=os.environ.get("DAEMON_HOST", "127.0.0.1"),
            port_file=Path(os.environ["DAEMON_PORT_FILE"]),
            pid_file=Path(pid_file) if pid_file else None,
        )


class _State:
    def __init__(self) -> None:
        self.cfg: _WorkerConfig | None = None
        self.backend: ScraperBackend | None = None
        self.last_used: float = time.monotonic()
        self.in_flight: int = 0
        self.lock = asyncio.Lock()  # guards in_flight/last_used and evict/exit
        self.bound_port: int = 0


state = _State()


def _remove_runtime_files(cfg: _WorkerConfig) -> None:
    """Unlink the port and pid files: the resident and `run.sh` stop seeing this worker."""
    for f in (cfg.port_file, cfg.pid_file):
        if f is None:
            continue
        try:
            f.unlink()
        except FileNotFoundError:
            pass


async def _idle_tick() -> bool:
    """One check of the idle watcher; True when the worker is to exit."""
    cfg = state.cfg
    assert cfg is not None
    async with state.lock:
        if state.in_flight > 0:
            return False
        idle = time.monotonic() - state.last_used
        if idle >= cfg.shutdown_seconds:
            logger.bind(stage="idle").info(
                "idle {:.0f}s ≥ shutdown {:.0f}s — exiting", idle, cfg.shutdown_seconds,
            )
            if state.backend is not None:
                await state.backend.unload()
            # The hard exit in the watcher skips the lifespan cleanup; without this the
            # stale pid file keeps `run.sh status` reporting a worker that is gone.
            _remove_runtime_files(cfg)
            return True
        if state.backend is not None and state.backend.loaded and idle >= cfg.idle_evict_seconds:
            await state.backend.unload()
        return False


async def _idle_watcher(interval: float = 10.0) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            if await _idle_tick():
                asyncio.get_running_loop().call_later(0.1, lambda: os._exit(0))
                return
        except Exception:  # noqa: BLE001 — the watcher is the worker's only way out
            logger.bind(stage="idle").opt(exception=True).warning("idle check failed")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    cfg = state.cfg
    assert cfg is not None
    logger.bind(stage="boot").info(
        "worker up pid={} profile={} port={} evict={:.0f}s shutdown={:.0f}s",
        os.getpid(), cfg.profile_name, state.bound_port,
        cfg.idle_evict_seconds, cfg.shutdown_seconds,
    )
    state.backend = ScraperBackend(cfg.profile_dir, cfg.profile_name, cfg.page_concurrency)
    watcher = asyncio.create_task(_idle_watcher())
    try:
        yield
    finally:
        watcher.cancel()
        try:
            if state.backend is not None:
                await state.backend.unload()
        finally:
            _remove_runtime_files(cfg)


app = FastAPI(lifespan=_lifespan)


@app.get("/healthz")
async def healthz() -> dict:
    return {
        "ok": True,
        "profile": state.cfg.profile_name if state.cfg else None,
        "loaded": bool(state.backend and state.backend.loaded),
        "in_flight": state.in_flight,
        "idle_seconds": time.monotonic() - state.last_used,
        "pid": os.getpid(),
    }


@app.post("/scrap", response_model=ScrapResponse)
async def scrap(req: ScrapRequest) -> ScrapResponse:
    assert state.backend is not None
    started = time.monotonic()
    async with state.lock:
        state.in_flight += 1
        state.last_used = time.monotonic()
    try:
        result = await state.backend.scrap(
            req.url, req.timeout,
            use_idle=req.use_idle, use_scroll=req.use_scroll,
        )
    finally:
        async with state.lock:
            state.in_flight -= 1
            state.last_used = time.monotonic()
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return ScrapResponse(**result)


@app.post("/scrap/batch", response_model=ScrapBatchResponse)
async def scrap_batch(req: ScrapBatchRequest) -> ScrapBatchResponse:
    """Scrape all `urls` in parallel, bounded by `batch_concurrency`.

    A dedicated semaphore caps how many URLs load at once; the backend's own
    page semaphore (`PAGE_CONCURRENCY`) still applies underneath. The whole
    batch counts as one in-flight unit so the idle watcher won't evict mid-run.
    A per-URL failure is captured as `outcome="internal"` — the batch never
    fails as a whole.
    """
    assert state.backend is not None
    assert state.cfg is not None
    backend = state.backend
    started = time.monotonic()
    async with state.lock:
        state.in_flight += 1
        state.last_used = time.monotonic()
    try:
        sem = asyncio.Semaphore(max(1, state.cfg.batch_concurrency))

        async def _one(url: str) -> ScrapResponse:
            item_started = time.monotonic()
            async with sem:
                try:
                    result = await backend.scrap(
                        url, req.timeout,
                        use_idle=req.use_idle, use_scroll=req.use_scroll,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.bind(stage="scrap/batch").warning(
                        "scrape failed url={} err={}", url, exc,
                    )
                    result = {
                        "outcome": "internal",
                        "url": url,
                        "final_url": None,
                        "target_status": None,
                        "title": None,
                        "headers": None,
                        "content": None,
                        "body": None,
                    }
            result["elapsed_ms"] = int((time.monotonic() - item_started) * 1000)
            return ScrapResponse(**result)

        results = await asyncio.gather(*(_one(u) for u in req.urls))
    finally:
        async with state.lock:
            state.in_flight -= 1
            state.last_used = time.monotonic()
    return ScrapBatchResponse(
        results=list(results),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


@app.delete("/profile")
async def delete_profile() -> dict:
    if state.backend is not None:
        async with state.lock:
            await state.backend.delete_profile()
    return {"ok": True}


def _pick_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def main() -> None:
    import uvicorn

    logger.configure(extra={"component": "worker", "stage": "-"})

    cfg = _WorkerConfig.from_env()
    state.cfg = cfg
    cfg.port_file.parent.mkdir(parents=True, exist_ok=True)
    port = _pick_free_port(cfg.host)
    state.bound_port = port
    # Atomic write of the port file: write to temp, rename.
    tmp = cfg.port_file.with_suffix(".tmp")
    tmp.write_text(str(port), encoding="utf-8")
    os.replace(tmp, cfg.port_file)
    if cfg.pid_file is not None:
        cfg.pid_file.write_text(str(os.getpid()), encoding="utf-8")
    uvicorn.run(app, host=cfg.host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
