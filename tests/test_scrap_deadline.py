"""Unit tests for `ScraperBackend.scrap` over a fake page (no real browser).

A page whose driver calls never answer must not keep its semaphore slot: the scrape
returns `outcome="timeout"` within the deadline and the next scrape gets the slot. The
load flags pick what `goto` waits for and whether the page is scrolled.
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
    headers: dict = {}


class _Page:
    def __init__(self, hang_read: bool, hang_close: bool) -> None:
        self.url = "https://example.test/"
        self.hang_read = hang_read
        self.hang_close = hang_close
        self.closed = False
        self.wait_until = None

    async def goto(self, url, wait_until, timeout):
        self.wait_until = wait_until
        return _Response()

    async def content(self) -> str:
        if self.hang_read:
            await _forever()
        return "<html><body><article>" + "text " * 100 + "</article></body></html>"

    async def title(self) -> str:
        return "t"

    async def close(self) -> None:
        if self.hang_close:
            await _forever()
        self.closed = True


class _Context:
    def __init__(self, **page_kwargs) -> None:
        self.pages: list[_Page] = []
        self.page_kwargs = page_kwargs

    async def new_page(self) -> _Page:
        page = _Page(**self.page_kwargs)
        self.pages.append(page)
        return page


@pytest.fixture(autouse=True)
def short_deadlines(monkeypatch):
    monkeypatch.setattr(context_module, "SCRAPE_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(context_module, "PAGE_CLEANUP_SECONDS", 0.2)


def _backend(tmp_path, **page_kwargs) -> ScraperBackend:
    backend = ScraperBackend(tmp_path / "profile", "test", page_concurrency=1)
    backend._context = _Context(**page_kwargs)  # skips the lazy Chromium launch
    return backend


async def test_hung_read_returns_timeout_and_frees_the_slot(tmp_path):
    backend = _backend(tmp_path, hang_read=True, hang_close=False)

    started = time.monotonic()
    result = await backend.scrap("https://example.test/", 1)

    assert result["outcome"] == "timeout"
    assert result["content"] is None
    assert time.monotonic() - started < 3.0
    assert backend._context.pages[0].closed
    assert not backend._sem.locked()


async def test_hung_close_does_not_pin_the_slot(tmp_path):
    backend = _backend(tmp_path, hang_read=False, hang_close=True)

    result = await backend.scrap("https://example.test/", 1)
    second = await asyncio.wait_for(
        backend.scrap("https://example.test/", 1), timeout=3.0,
    )

    assert result["outcome"] in ("ok", "redirected", "empty")
    assert second["url"] == "https://example.test/"
    assert not backend._sem.locked()


async def test_normal_page_is_read_and_closed(tmp_path):
    backend = _backend(tmp_path, hang_read=False, hang_close=False)

    result = await backend.scrap("https://example.test/", 1)

    assert result["target_status"] == 200
    assert result["title"] == "t"
    assert backend._context.pages[0].closed


async def test_use_idle_waits_for_network_idle(tmp_path):
    backend = _backend(tmp_path, hang_read=False, hang_close=False)

    await backend.scrap("https://example.test/", 1)
    await backend.scrap("https://example.test/", 1, use_idle=True)

    assert [p.wait_until for p in backend._context.pages] == ["domcontentloaded", "networkidle"]


async def test_use_scroll_scrolls_after_the_load(tmp_path, monkeypatch):
    scrolled = []

    async def _scroll(page) -> bool:
        scrolled.append(page)
        return True

    monkeypatch.setattr(context_module, "scroll_to_bottom", _scroll)
    backend = _backend(tmp_path, hang_read=False, hang_close=False)

    await backend.scrap("https://example.test/", 1)
    await backend.scrap("https://example.test/", 1, use_scroll=True)

    assert scrolled == [backend._context.pages[1]]
