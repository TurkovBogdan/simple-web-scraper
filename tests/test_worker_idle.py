"""Unit tests for the worker's idle watcher (no real browser).

The backend is faked. The watcher must close the browser only when nothing is in
flight, must remove the port and pid files when the worker exits (the hard exit skips
the lifespan cleanup), and must keep running when closing the browser fails.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

import browser_daemon
from browser_daemon import _State, _WorkerConfig


class _FakeBackend:
    def __init__(self, fail_unloads: int = 0) -> None:
        self.loaded = True
        self.unloads = 0
        self.fail_unloads = fail_unloads

    async def unload(self) -> None:
        self.unloads += 1
        if self.fail_unloads > 0:
            self.fail_unloads -= 1
            raise RuntimeError("Target page, context or browser has been closed")
        self.loaded = False


def _state(tmp_path: Path, *, evict: float, shutdown: float, in_flight: int = 0, **backend) -> _State:
    state = _State()
    state.cfg = _WorkerConfig(
        profile_name="test", profile_dir=tmp_path / "profile", page_concurrency=1,
        batch_concurrency=1, idle_evict_seconds=evict, shutdown_seconds=shutdown,
        host="127.0.0.1", port_file=tmp_path / "w.port", pid_file=tmp_path / "w.pid",
    )
    state.cfg.port_file.write_text("1", encoding="utf-8")
    state.cfg.pid_file.write_text("2", encoding="utf-8")
    state.backend = _FakeBackend(**backend)
    state.in_flight = in_flight
    state.last_used = time.monotonic() - 1.0  # idle for a second
    return state


@pytest.fixture
def worker_state(monkeypatch, tmp_path):
    def install(**kwargs) -> _State:
        state = _state(tmp_path, **kwargs)
        monkeypatch.setattr(browser_daemon, "state", state)
        return state

    return install


async def test_idle_exit_removes_the_runtime_files(worker_state) -> None:
    state = worker_state(evict=0.0, shutdown=0.5)

    assert await browser_daemon._idle_tick() is True

    assert state.backend.unloads == 1
    assert not state.cfg.port_file.exists() and not state.cfg.pid_file.exists()


async def test_nothing_happens_while_a_request_is_in_flight(worker_state) -> None:
    state = worker_state(evict=0.0, shutdown=0.0, in_flight=1)

    assert await browser_daemon._idle_tick() is False

    assert state.backend.unloads == 0
    assert state.cfg.port_file.exists() and state.cfg.pid_file.exists()


async def test_idle_eviction_closes_the_browser_and_keeps_the_worker(worker_state) -> None:
    state = worker_state(evict=0.5, shutdown=1e9)

    assert await browser_daemon._idle_tick() is False

    assert state.backend.unloads == 1 and not state.backend.loaded
    assert state.cfg.port_file.exists() and state.cfg.pid_file.exists()


async def test_watcher_survives_a_failed_close_and_still_exits(worker_state, monkeypatch) -> None:
    state = worker_state(evict=0.0, shutdown=0.5, fail_unloads=1)
    exits: list[int] = []
    monkeypatch.setattr(browser_daemon.os, "_exit", exits.append)

    await asyncio.wait_for(browser_daemon._idle_watcher(interval=0.01), timeout=3.0)
    await asyncio.sleep(0.3)  # the exit is scheduled a moment after the watcher returns

    assert state.backend.unloads == 2        # the first close failed; the watcher went on
    assert exits == [0]
    assert not state.cfg.port_file.exists() and not state.cfg.pid_file.exists()
