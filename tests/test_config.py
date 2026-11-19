"""Unit tests for Settings / env handling."""
from __future__ import annotations

import importlib


def test_defaults_and_derived_paths(monkeypatch) -> None:
    # Isolate from any real .env by pointing HOME-independent fields at defaults.
    import config as config_mod

    importlib.reload(config_mod)
    s = config_mod.Settings(_env_file=None)
    assert s.scraper_port == 19020
    assert s.daemon_host == "127.0.0.1"
    assert s.profile_dir == s.runtime_dir / "profile"
    assert s.user_tag  # non-empty, filesystem-safe


def test_env_override(monkeypatch) -> None:
    import config as config_mod

    monkeypatch.setenv("SCRAPER_PORT", "19025")
    monkeypatch.setenv("PAGE_CONCURRENCY", "8")
    monkeypatch.setenv("SCRAPE_BATCH_CONCURRENCY", "6")
    s = config_mod.Settings(_env_file=None)
    assert s.scraper_port == 19025
    assert s.page_concurrency == 8
    assert s.scrape_batch_concurrency == 6
