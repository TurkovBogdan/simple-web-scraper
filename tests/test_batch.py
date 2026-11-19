"""Unit tests for the worker's /scrap/batch endpoint (no real browser).

The backend is faked so we can assert three things without launching Chromium:
parallelism is bounded by `batch_concurrency`, order is preserved, and a single
failing URL degrades to `outcome="internal"` instead of failing the whole batch.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import browser_daemon
from browser_daemon import _State, _WorkerConfig, app


class _FakeBackend:
    """Records max observed concurrency; fails for URLs containing 'boom'."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.max_in_flight = 0

    @property
    def loaded(self) -> bool:
        return True

    async def unload(self) -> None:
        return None

    async def scrap(self, url, timeout, use_idle=False, use_scroll=False) -> dict:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.02)  # hold the slot so overlap is observable
            if "boom" in url:
                raise RuntimeError("kaboom")
            return {
                "outcome": "ok",
                "url": url,
                "final_url": url,
                "target_status": 200,
                "title": "t",
                "headers": {},
                "content": "# t",
                "body": "x" * 300,
            }
        finally:
            self.in_flight -= 1


def _cfg(profile_dir, batch_concurrency: int) -> _WorkerConfig:
    return _WorkerConfig(
        profile_name="test", profile_dir=profile_dir, page_concurrency=99,
        batch_concurrency=batch_concurrency, idle_evict_seconds=1e9,
        shutdown_seconds=1e9, host="127.0.0.1", port_file=None, pid_file=None,
    )


@pytest.fixture
def worker(monkeypatch, tmp_path):
    """A TestClient with a fake backend injected AFTER lifespan startup.

    `_lifespan` rebuilds `state.backend` with a real `ScraperBackend` (never
    launched — `load()` is lazy), so we swap in the fake once the app started.
    """
    profile_dir = tmp_path / "profile"
    state = _State()
    state.cfg = _cfg(profile_dir, batch_concurrency=2)
    monkeypatch.setattr(browser_daemon, "state", state)
    with TestClient(app) as client:
        fake = _FakeBackend()
        state.backend = fake  # override the real backend the lifespan built
        yield client, state, fake


def test_batch_bounds_concurrency_and_preserves_order(worker):
    client, state, fake = worker
    state.cfg = _cfg(state.cfg.profile_dir, batch_concurrency=2)
    urls = [f"https://e/{i}" for i in range(6)]

    r = client.post("/scrap/batch", json={"urls": urls})

    assert r.status_code == 200
    body = r.json()
    assert [item["url"] for item in body["results"]] == urls
    assert all(item["outcome"] == "ok" for item in body["results"])
    assert fake.max_in_flight <= 2  # bounded by batch_concurrency


def test_batch_isolates_per_url_failure(worker):
    client, state, fake = worker
    state.cfg = _cfg(state.cfg.profile_dir, batch_concurrency=4)
    urls = ["https://ok/1", "https://boom/2", "https://ok/3"]

    r = client.post("/scrap/batch", json={"urls": urls})

    assert r.status_code == 200
    results = r.json()["results"]
    outcomes = {item["url"]: item["outcome"] for item in results}
    assert outcomes["https://ok/1"] == "ok"
    assert outcomes["https://boom/2"] == "internal"
    assert outcomes["https://ok/3"] == "ok"
