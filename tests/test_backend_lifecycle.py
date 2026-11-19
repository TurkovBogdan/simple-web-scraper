"""Unit tests for the browser lifecycle in `ScraperBackend` (no real browser).

The playwright driver and the browser context are faked, so we can check what the
worker does when the browser cannot launch, has died, or will not close: no driver
process left behind, no dead context kept for the next scrape, nothing raised out of
`unload()`.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from browser import context as context_module
from browser.context import ScraperBackend


async def _forever() -> None:
    await asyncio.Event().wait()


class _Response:
    status = 200

    @property
    def headers(self) -> dict:
        return {}


class _Cdp:
    async def send(self, *_args) -> None:
        return None

    async def detach(self) -> None:
        return None


class _Page:
    url = "https://example.test/"

    async def goto(self, url, wait_until, timeout):
        return _Response()

    async def content(self) -> str:
        return "<html><body><article>" + "text " * 100 + "</article></body></html>"

    async def title(self) -> str:
        return "t"

    async def close(self) -> None:
        return None


class _Context:
    def __init__(self, new_page_error=None, close_error=None, hang_close=False) -> None:
        self.new_page_error = new_page_error
        self.close_error = close_error
        self.hang_close = hang_close
        self.closed = False

    async def new_page(self) -> _Page:
        if self.new_page_error is not None:
            raise self.new_page_error
        return _Page()

    async def new_cdp_session(self, page) -> _Cdp:
        return _Cdp()

    async def close(self) -> None:
        if self.hang_close:
            await _forever()
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class _Driver:
    """What `async_playwright().start()` returns: a browser launcher that can be stopped."""

    def __init__(self, launch_error=None, stop_error=None) -> None:
        self.launch_error = launch_error
        self.stop_error = stop_error
        self.stopped = False
        self.chromium = self

    async def launch_persistent_context(self, **_kwargs) -> _Context:
        if self.launch_error is not None:
            raise self.launch_error
        return _Context()

    async def stop(self) -> None:
        self.stopped = True
        if self.stop_error is not None:
            raise self.stop_error


class _Playwright:
    """Replaces `patchright.async_api.async_playwright`; one driver per `start()`."""

    def __init__(self, launch_errors) -> None:
        self.launch_errors = list(launch_errors)
        self.drivers: list[_Driver] = []

    def __call__(self) -> _Playwright:
        return self

    async def start(self) -> _Driver:
        error = self.launch_errors.pop(0) if self.launch_errors else None
        driver = _Driver(launch_error=error)
        self.drivers.append(driver)
        return driver


@pytest.fixture
def fake_playwright(monkeypatch):
    """Install a fake driver factory; `launch_errors[i]` fails the i-th launch."""
    import patchright.async_api as pw_api

    def install(*launch_errors) -> _Playwright:
        factory = _Playwright(launch_errors)
        monkeypatch.setattr(pw_api, "async_playwright", factory)
        return factory

    return install


def _backend(tmp_path, profile: str = "test") -> ScraperBackend:
    return ScraperBackend(tmp_path / "profile", profile, page_concurrency=1)


async def test_failed_launch_stops_its_driver(tmp_path, fake_playwright) -> None:
    """A launch that fails (browser build missing) must not leak the driver process:
    each one holds pipes of the worker, and a few hundred exhaust its descriptors."""
    factory = fake_playwright(RuntimeError("Executable doesn't exist"), None)
    backend = _backend(tmp_path, "ru-rtx3060")

    with pytest.raises(RuntimeError):
        await backend.load()

    assert factory.drivers[0].stopped
    assert not backend.loaded and backend._pw is None
    await backend.load()                      # the next attempt launches afresh
    assert backend.loaded and len(factory.drivers) == 2
    assert not factory.drivers[1].stopped


async def test_unload_survives_a_dead_browser(tmp_path) -> None:
    """Closing a browser that is already gone must not raise: the exception would end
    the idle watcher (no more eviction, no self-exit) or the shutdown cleanup."""
    backend = _backend(tmp_path)
    context = backend._context = _Context(close_error=RuntimeError("Target closed"))
    driver = backend._pw = _Driver(stop_error=RuntimeError("driver gone"))

    await backend.unload()

    assert context.closed and driver.stopped
    assert not backend.loaded and backend._pw is None


async def test_unload_is_bounded_when_close_hangs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(context_module, "BROWSER_CLOSE_SECONDS", 0.2)
    backend = _backend(tmp_path)
    backend._context = _Context(hang_close=True)
    driver = backend._pw = _Driver()

    started = time.monotonic()
    await backend.unload()

    assert time.monotonic() - started < 2.0
    assert driver.stopped and not backend.loaded and backend._pw is None


async def test_dead_context_is_dropped_so_the_next_scrape_relaunches(
    tmp_path, fake_playwright,
) -> None:
    """A browser that died between scrapes is forgotten by the first scrape that cannot
    open a page; before, every later scrape failed the same way for as long as requests
    kept the worker busy."""
    from patchright._impl._errors import TargetClosedError

    factory = fake_playwright()
    backend = _backend(tmp_path, "ru-rtx3060")
    dead = backend._context = _Context(new_page_error=TargetClosedError())

    with pytest.raises(TargetClosedError):
        await backend.scrap("https://example.test/", 1)

    assert dead.closed and not backend.loaded
    assert not backend._sem.locked()
    result = await backend.scrap("https://example.test/", 1)
    assert backend.loaded and len(factory.drivers) == 1
    assert result["target_status"] == 200
