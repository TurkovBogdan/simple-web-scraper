"""LIVE end-to-end lifecycle: the always-on resident spawns a real browser worker
on demand, scrapes through it, and respawns it transparently after the worker dies.

Run: `uv run pytest -m live tests/live/test_lifecycle.py`. Launches real Chromium.
"""
from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path

import pytest

from browser_manager import BrowserManager
from config import Settings

pytestmark = pytest.mark.live

# A self-contained data: URL — proves the browser launched and rendered without
# depending on a live external site (cold-launch + network would make it flaky).
_MARKER = "LIFECYCLE_MARKER_42"
_HTML = f"data:text/html,<html><body><h1>hi</h1><p>{_MARKER}</p></body></html>"
_REQ = {
    "url": _HTML,
    "timeout": 5,
}


def _running(pid: int) -> bool:
    """True only if the process exists AND is not a reaped/zombie corpse.

    The worker is a child of the test process; after we kill it, it lingers as a
    zombie (state 'Z') until reaped — `os.kill(pid, 0)` still succeeds for it, so we
    read /proc to tell a live worker from a dead-but-unreaped one.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return stat.split(") ", 1)[1].split(" ", 1)[0] != "Z"
    except OSError:
        return False


def _reap(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)  # reap our own child so it doesn't linger as a zombie
    except OSError:
        pass


async def _wait_gone(pid: int, timeout: float = 6.0) -> None:
    for _ in range(int(timeout / 0.1)):
        if not _running(pid):
            return
        await asyncio.sleep(0.1)


def _settings(tmp_path: Path, **overrides) -> Settings:
    return Settings(
        _env_file=None, scraper_profile="ru-rtx3060",
        runtime_dir=tmp_path / "runtime", log_dir=tmp_path / "logs", **overrides,
    )


async def test_resident_spawns_and_respawns_browser(tmp_path: Path) -> None:
    mgr = BrowserManager(_settings(tmp_path))
    pid_file = tmp_path / "runtime" / f"browser-{mgr.settings.user_tag}.pid"
    pids: list[int] = []
    try:
        # 1. On demand: no worker yet → ensure_running spawns the heavy browser worker.
        assert not pid_file.exists()
        port = await mgr.ensure_running()
        assert await mgr._probe(port)                     # worker is healthy
        first_pid = int(pid_file.read_text(encoding="utf-8"))
        pids.append(first_pid)
        assert _running(first_pid)                        # real process is up

        # 2. It actually drives the browser: the rendered DOM contains our marker.
        res = await mgr.scrap(dict(_REQ))
        assert _MARKER in (res["body"] or "")

        # 3. Worker dies (as it would on idle self-exit) → resident respawns it
        #    transparently on the next request.
        os.kill(first_pid, signal.SIGKILL)
        res2 = await mgr.scrap(dict(_REQ))                # transparent respawn + retry
        assert _MARKER in (res2["body"] or "")
        second_pid = int(pid_file.read_text(encoding="utf-8"))
        pids.append(second_pid)
        assert second_pid != first_pid                    # a fresh worker
        assert _running(second_pid)                       # and it's alive
        await _wait_gone(first_pid)
        assert not _running(first_pid)                    # old worker is gone
    finally:
        for pid in pids:
            _reap(pid)
        await mgr.aclose()


async def test_worker_exits_on_idle_and_removes_its_files(tmp_path: Path) -> None:
    """The idle self-exit is a hard exit (`os._exit`); it must still leave no port or
    pid file behind, or `run.sh status` keeps reporting a worker that is gone."""
    mgr = BrowserManager(_settings(
        tmp_path, browser_idle_evict_minutes=0.001, browser_daemon_shutdown_minutes=0.01,
    ))
    runtime = tmp_path / "runtime"
    pid_file = runtime / f"browser-{mgr.settings.user_tag}.pid"
    port_file = runtime / f"browser-{mgr.settings.user_tag}.port"
    pid: int | None = None
    try:
        res = await mgr.scrap(dict(_REQ))                 # spawns the worker, launches Chrome
        assert _MARKER in (res["body"] or "")
        pid = int(pid_file.read_text(encoding="utf-8"))
        await _wait_gone(pid, timeout=30.0)               # the watcher checks every 10 s
        assert not _running(pid)
        assert not pid_file.exists() and not port_file.exists()
    finally:
        if pid is not None:
            _reap(pid)
        await mgr.aclose()
