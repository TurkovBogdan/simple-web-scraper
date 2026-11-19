"""Unit tests for the resident-side BrowserManager (no real browser).

These verify the always-on-resident / on-demand-worker logic by faking the worker
layer: discovery from the port file, spawn-when-absent, reuse-when-healthy, and
transparent respawn when the worker has died.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from browser_manager import BrowserManager, _read_port
from config import Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        runtime_dir=tmp_path / "runtime",
        log_dir=tmp_path / "logs",
        scraper_profile="ru-rtx3060",
    )


def test_read_port_roundtrip_and_garbage(tmp_path) -> None:
    p = tmp_path / "w.port"
    assert _read_port(p) is None            # missing
    p.write_text("55501", encoding="utf-8")
    assert _read_port(p) == 55501           # valid
    p.write_text("not-a-number", encoding="utf-8")
    assert _read_port(p) is None            # garbage → None, no raise


async def test_ensure_running_reuses_healthy_worker(tmp_path, monkeypatch) -> None:
    mgr = BrowserManager(_settings(tmp_path))
    monkeypatch.setattr(mgr, "_probe_from_file", AsyncMock(return_value=44444))
    spawn = MagicMock()
    monkeypatch.setattr(mgr, "_spawn", spawn)
    try:
        assert await mgr.ensure_running() == 44444
        spawn.assert_not_called()           # already up → no spawn
    finally:
        await mgr.aclose()


async def test_ensure_running_spawns_when_absent(tmp_path, monkeypatch) -> None:
    mgr = BrowserManager(_settings(tmp_path))
    monkeypatch.setattr(mgr, "_probe_from_file", AsyncMock(return_value=None))
    spawn = MagicMock()
    monkeypatch.setattr(mgr, "_spawn", spawn)
    monkeypatch.setattr(mgr, "_wait_healthy", AsyncMock(return_value=55555))
    try:
        assert await mgr.ensure_running() == 55555
        spawn.assert_called_once()          # absent → spawned the browser worker
    finally:
        await mgr.aclose()


async def test_ensure_running_raises_when_worker_never_healthy(tmp_path, monkeypatch) -> None:
    s = _settings(tmp_path)
    s.daemon_spawn_retries = 1
    mgr = BrowserManager(s)
    monkeypatch.setattr(mgr, "_probe_from_file", AsyncMock(return_value=None))
    monkeypatch.setattr(mgr, "_spawn", MagicMock())
    monkeypatch.setattr(mgr, "_wait_healthy", AsyncMock(return_value=None))
    try:
        with pytest.raises(RuntimeError):
            await mgr.ensure_running()
    finally:
        await mgr.aclose()


async def test_scrap_respawns_on_worker_disconnect(tmp_path, monkeypatch) -> None:
    """A worker that dies mid-request surfaces as RemoteProtocolError → respawn+retry."""
    mgr = BrowserManager(_settings(tmp_path))
    ensure = AsyncMock(return_value=1234)
    monkeypatch.setattr(mgr, "ensure_running", ensure)
    ok = {"outcome": "ok", "url": "u"}
    call = AsyncMock(side_effect=[httpx.RemoteProtocolError("server disconnected"), ok])
    monkeypatch.setattr(mgr, "_call", call)
    try:
        assert await mgr.scrap({"url": "u"}) == ok
        assert call.call_count == 2          # retried once after the disconnect
        ensure.assert_awaited()              # respawn was triggered
    finally:
        await mgr.aclose()


async def test_delete_profile_retries_when_the_worker_vanishes(tmp_path, monkeypatch) -> None:
    """A worker gone mid-call must not be reported as a deleted profile: respawn + retry."""
    mgr = BrowserManager(_settings(tmp_path))
    ensure = AsyncMock(return_value=1234)
    monkeypatch.setattr(mgr, "ensure_running", ensure)
    call = AsyncMock(side_effect=[httpx.ConnectError("worker gone"), {"ok": True}])
    monkeypatch.setattr(mgr, "_call", call)
    try:
        assert await mgr.delete_profile() == {"ok": True}
        assert call.call_args_list[-1].args[:2] == ("DELETE", "/profile")
        assert call.call_count == 2
        ensure.assert_awaited()
    finally:
        await mgr.aclose()


async def test_delete_profile_fails_loudly_when_the_worker_stays_down(tmp_path, monkeypatch) -> None:
    mgr = BrowserManager(_settings(tmp_path))
    monkeypatch.setattr(mgr, "ensure_running", AsyncMock(return_value=1234))
    monkeypatch.setattr(mgr, "_call", AsyncMock(side_effect=httpx.ConnectError("worker gone")))
    try:
        with pytest.raises(httpx.ConnectError):
            await mgr.delete_profile()
    finally:
        await mgr.aclose()
